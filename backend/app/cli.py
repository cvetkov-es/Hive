# -*- coding: utf-8 -*-
"""Консоль планировщика.

    cd backend && python -m app.cli <команда> [--region=… | --all] [--time=20]

Команды: plan, baseline, compare, audit, explain, replan, export, curve.

Даёт те же числа, что интерфейс, но без браузера: видно, что результат
считается, а не нарисован.
"""
from __future__ import annotations
import argparse
import sys
import time

from .config import REGIONS
from .models import hhmm


# --- печать ------------------------------------------------------------------

def _table(headers: list, rows: list) -> str:
    cols = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
            for i, h in enumerate(headers)]
    line = "  ".join(str(h).ljust(cols[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * c for c in cols)]
    for r in rows:
        out.append("  ".join(str(v).ljust(cols[i]) for i, v in enumerate(r)))
    return "\n".join(out)


def _print_plan(plan, problem, verbose: bool = True) -> None:
    from .solver.metrics import summarize
    s = summarize(plan, problem.rd)
    print(f"\n=== {plan.region} · {plan.algo} ===")
    print(f"исполнителей {s['used_engineers']} · "
          f"заявок {s['assigned']}/{s['total_jobs']} · "
          f"пробег {s['total_km']} км · "
          f"в пути {s['travel_min']} мин · "
          f"опозданий {s['late_jobs']}")
    if not verbose:
        return
    engs = {e.id: e for e in problem.engineers}
    rows = []
    for r in plan.routes:
        if not r.stops:
            continue
        e = engs[r.engineer_id]
        rows.append([r.engineer_id, e.transport, e.cluster, r.n_jobs, r.km,
                     hhmm(r.depart_min), hhmm(r.stops[-1].end_min),
                     f"{r.span_min // 60}ч{r.span_min % 60:02d}"])
    print(_table(["бригада", "транспорт", "зона", "заявок", "км",
                  "выезд", "финиш", "день"], rows))
    if plan.unassigned:
        print(f"\nне назначено: {len(plan.unassigned)}")
        print(_table(["заявка", "причина", "подробность"],
                     [[u.job_id, u.reason, u.detail[:60]] for u in plan.unassigned]))


# --- команды -----------------------------------------------------------------

def cmd_plan(problem, args):
    from .solver.engine import solve, solve_with_fleet
    t0 = time.time()
    plan = (solve_with_fleet(problem, args.keep, time_limit_s=args.time)
            if args.keep else solve(problem, time_limit_s=args.time))
    _print_plan(plan, problem)
    print(f"решено за {time.time() - t0:.1f} с")
    return plan


def cmd_baseline(problem, args):
    from .solver.baseline import solve_baseline
    plan = solve_baseline(problem)
    _print_plan(plan, problem)
    return plan


def cmd_compare(problem, args):
    from .solver.compare import build_plans, compare, format_table
    print(format_table(compare(problem, build_plans(problem, time_limit_s=args.time))))


def cmd_audit(problem, args):
    from .solver.audit import audit_plan, format_report
    from .solver.baseline import solve_baseline
    from .solver.engine import solve
    for plan in (solve(problem, time_limit_s=args.time), solve_baseline(problem)):
        print(format_report(audit_plan(plan, problem), plan))


def cmd_explain(problem, args):
    """Артефакт «Объяснение» из ТЗ 2.1.7 в консоли."""
    from .config import TRANSPORT_RU
    from .solver.engine import solve, solve_with_fleet
    from .solver.explain import why_assigned, why_unassigned
    plan = (solve_with_fleet(problem, args.keep, time_limit_s=args.time)
            if args.keep else solve(problem, time_limit_s=args.time))
    print(f"\n=== {plan.region}: почему так распределено ===")

    assigned = [s.job_id for r in plan.routes for s in r.stops]
    chosen = [args.job] if args.job else assigned[:args.limit]
    for jid in chosen:
        if jid not in assigned:
            continue
        e = why_assigned(jid, plan, problem)
        print(f"\n[{jid}] {e.headline}")
        for x in e.reasons:
            print(f"    * {x}")
        for x in e.rejected:
            print(f"    отклонено {x.engineer_id}: {x.reason}")
        print("    время в пути: " + ", ".join(
            ("-> " if k == e.used_mode else "") + f"{TRANSPORT_RU[k]} {v} мин"
            for k, v in e.travel_by_mode.items()))
        print(f"    {e.caveat}")

    todo = [args.job] if args.job else [u.job_id for u in plan.unassigned][:args.limit]
    for jid in todo:
        if all(u.job_id != jid for u in plan.unassigned):
            continue
        u = why_unassigned(jid, plan, problem)
        print(f"\n[{jid}] НЕ НАЗНАЧЕНА: {u.reason}")
        print(f"    {u.detail}")
        for r in u.remedies:
            print(f"    цена вопроса ({r.kind}): {r.action}")
            print(f"        -> {r.effect}")


def cmd_replan(problem, args):
    """Событие в середине дня: ТЗ 2.1.6. Все три вида подряд."""
    from .solver.audit import audit_plan, format_report
    from .solver.engine import solve
    from .solver.replan import (Event, apply_event, format_diff,
                                make_demo_emergency, problem_with_event)
    plan = solve(problem, time_limit_s=args.time)
    _print_plan(plan, problem, verbose=False)
    at = args.at_min
    busiest = max(plan.routes, key=lambda r: len(r.stops))
    future = [s.job_id for s in busiest.stops if s.start_min > at]
    events = [Event(kind="urgent", at_min=at,
                    job=make_demo_emergency(problem, at))]
    if future:
        events.append(Event(kind="cancel", at_min=at, job_id=future[0]))
    events.append(Event(kind="unavailable", at_min=at,
                        engineer_id=busiest.engineer_id))
    for ev in events:
        new, diff = apply_event(plan, problem, ev, time_limit_s=args.time)
        print(format_diff(diff))
        rep = audit_plan(new, problem_with_event(problem, ev))
        print(f"  {rep.headline}")
        if not rep.ok:
            print(format_report(rep))


def cmd_export(problem, args):
    """Выгрузка результата по ТЗ 2.4.2: CSV для человека, JSON для системы."""
    import json
    from pathlib import Path
    from .config import ROOT
    from .io.export import plan_to_csv, plan_to_json
    from .solver.engine import solve
    plan = solve(problem, time_limit_s=args.time)
    _print_plan(plan, problem, verbose=False)
    out = Path(args.out) if args.out else (ROOT / "runs")
    out.mkdir(parents=True, exist_ok=True)
    name = problem.rd.name
    (out / f"{name}.plan.csv").write_text(plan_to_csv(plan, problem),
                                          encoding="utf-8")
    (out / f"{name}.plan.json").write_text(
        json.dumps(plan_to_json(plan, problem), ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"выгружено: {out / (name + '.plan.csv')}")
    print(f"           {out / (name + '.plan.json')}")


def cmd_curve(problem, args):
    from .solver.engine import solve_with_fleet
    from .solver.metrics import summarize
    n = len(problem.engineers)
    sizes = sorted({max(1, round(n * f)) for f in (0.4, 0.55, 0.7, 0.85, 1.0)})
    rows = []
    for k in sizes:
        s = summarize(solve_with_fleet(problem, k, time_limit_s=args.time), problem.rd)
        rows.append([k, s["used_engineers"], f"{s['assigned']}/{s['total_jobs']}",
                     s["unassigned"], s["total_km"]])
    print(f"\n=== {problem.rd.name}: парк -> результат ===")
    print(_table(["парк", "занято", "назначено", "не назначено", "км"], rows))


COMMANDS = {"plan": cmd_plan, "baseline": cmd_baseline, "compare": cmd_compare,
            "audit": cmd_audit, "curve": cmd_curve, "explain": cmd_explain,
            "replan": cmd_replan, "export": cmd_export}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=sorted(COMMANDS))
    ap.add_argument("--region", help="один регион: " + " | ".join(REGIONS))
    ap.add_argument("--all", action="store_true", help="все три региона подряд")
    ap.add_argument("--time", type=int, default=20, help="время счёта планировщика, секунд")
    ap.add_argument("--keep", type=int, default=0, help="урезать парк до N бригад")
    ap.add_argument("--job", help="одна заявка по номеру (для explain)")
    ap.add_argument("--limit", type=int, default=3, help="сколько заявок показать")
    ap.add_argument("--at", dest="at_min", type=int, default=15 * 60 + 40,
                    help="момент события в минутах от полуночи (для replan)")
    ap.add_argument("--out", help="каталог выгрузки (для export)")
    ap.add_argument("--from-spec", dest="from_spec",
                    help="каталог с jobs.csv и engineers.csv в формате ТЗ 2.4")
    args = ap.parse_args(argv)

    if args.from_spec:
        from pathlib import Path
        from .io.spec_format import build_problem as build_from_spec
        d = Path(args.from_spec)
        COMMANDS[args.command](build_from_spec(d / "jobs.csv",
                                               d / "engineers.csv"), args)
        return 0

    if args.all:
        regions = list(REGIONS)
    elif args.region:
        if args.region not in REGIONS:
            ap.error(f"неизвестный регион «{args.region}»; есть: {', '.join(REGIONS)}")
        regions = [args.region]
    else:
        ap.error("укажите --region=<регион>, --all или --from-spec=<каталог>")

    from .solver.engine import build_problem
    for region in regions:
        COMMANDS[args.command](build_problem(region), args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
