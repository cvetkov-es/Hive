# -*- coding: utf-8 -*-
"""Эталонные планы: считаются один раз на быстрой машине и коммитятся.

Зачем. Лимит солвера задан по настенным часам, поэтому качество плана зависит от
того, сколько процессорного времени досталось прогону. Замерено на слабой машине:
при одинаковом лимите 15 секунд прогон, получивший 12.3 процессорных секунды, дал
230.1 км, а получивший 6.6 — 243.8 км. На демонстрации такой разброс недопустим:
показывать нужно один и тот же план, а не лотерею.

Поэтому планы считаются заранее с большим бюджетом, проверяются аудитором и
кладутся в artifacts/. Интерфейс отдаёт их мгновенно, а кнопка «Пересчитать»
доказывает, что живой прогон приходит к тому же.

Запуск:
    python tools/build_reference.py                  # 180 с на регион
    python tools/build_reference.py --seconds 600    # дольше и лучше
    python tools/build_reference.py --region Восток
    python tools/build_reference.py --curve          # плюс кривая «штат -> результат»
    python tools/build_reference.py --curve-only --curve-seconds 180
                                                     # только кривая, по готовым планам
"""
import argparse
import json
import os
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import REGIONS                                  # noqa: E402
from app.solver.engine import build_problem, solve              # noqa: E402
from app.solver.baseline import solve_baseline                  # noqa: E402
from app.solver.audit import audit_plan                         # noqa: E402

OUT = ROOT / "artifacts"


def plan_to_dict(plan, problem, cpu_s=None, wall_s=None) -> dict:
    jobs = {j.id: j for j in problem.rd.jobs}
    return dict(
        region=plan.region, algo=plan.algo,
        used_engineers=plan.used_engineers, total_km=plan.total_km,
        assigned=plan.assigned, total_jobs=len(problem.rd.jobs),
        unassigned=[dict(job_id=u.job_id, code=u.code, reason=u.reason, detail=u.detail)
                    for u in plan.unassigned],
        late_jobs=plan.late_jobs, travel_min=plan.travel_min,
        routes=[dict(
            engineer_id=r.engineer_id, depart=r.depart_min,
            km=r.km, span_min=r.span_min,
            stops=[dict(job_id=s.job_id, seq=s.seq, arrive=s.arrive_min,
                        start=s.start_min, end=s.end_min, wait=s.wait_min,
                        late=s.late_min, leg_km=s.leg_km, leg_min=s.leg_min,
                        skill=jobs[s.job_id].skill, priority=jobs[s.job_id].priority)
                   for s in r.stops],
        ) for r in plan.routes if r.stops],
        compute=dict(cpu_seconds=cpu_s, wall_seconds=wall_s),
    )


def run_region(region: str, seconds: int):
    problem = build_problem(region)
    print(f"\n=== {region}: {len(problem.rd.jobs)} заявок, {len(problem.engineers)} бригад")

    c0, w0 = time.process_time(), time.time()
    plan = solve(problem, time_limit_s=seconds)
    cpu, wall = round(time.process_time() - c0, 1), round(time.time() - w0, 1)

    rep = audit_plan(plan, problem)
    ok = not rep.violations
    print(f"    план:    {plan.used_engineers:2d} бригад, {plan.total_km:7.1f} км, "
          f"{plan.assigned}/{len(problem.rd.jobs)} заявок, опозданий {plan.late_jobs}")
    print(f"    аудит:   {'пройден' if ok else 'ПРОВАЛЕН'}, нарушений {len(rep.violations)}")
    print(f"    ресурс:  {cpu} процессорных с из {wall} настенных")
    if not ok:
        for v in rep.violations[:5]:
            print(f"      ! {v}")
        raise SystemExit(f"{region}: эталон не проходит аудит, не сохраняю")

    base = solve_baseline(problem)
    brep = audit_plan(base, problem)
    print(f"    базовый: {base.used_engineers:2d} бригад, {base.total_km:7.1f} км, "
          f"{base.assigned}/{len(problem.rd.jobs)} заявок, аудит "
          f"{'пройден' if not brep.violations else 'ПРОВАЛЕН'}")

    OUT.mkdir(exist_ok=True)
    (OUT / f"plan_{region}.json").write_text(
        json.dumps(plan_to_dict(plan, problem, cpu, wall), ensure_ascii=False, indent=1),
        encoding="utf-8")
    (OUT / f"baseline_{region}.json").write_text(
        json.dumps(plan_to_dict(base, problem), ensure_ascii=False, indent=1),
        encoding="utf-8")
    return problem, plan, dict(region=region, jobs=len(problem.rd.jobs),
                fleet=len(problem.engineers),
                plan=dict(engineers=plan.used_engineers, km=plan.total_km,
                          assigned=plan.assigned, late=plan.late_jobs),
                baseline=dict(engineers=base.used_engineers, km=base.total_km,
                              assigned=base.assigned),
                cpu_seconds=cpu, wall_seconds=wall)


def run_curve(problem, seconds: int, headline) -> list:
    """Кривая «штат -> результат»: сколько бригад нужно на самом деле.

    Штат урезается от основного плана (fleet_for_curve): сначала уходят
    бригады без заявок, потом наименее загруженные. Точки — полный штат, ровно
    столько, сколько работает в основном плане, и на одну-две бригады меньше.

    Пока в штате есть все работающие, точка — сам основной план: он выполним и
    на таком штате, и аудит проверяет это на урезанном составе. Меньшие штаты
    считаются с одним и тем же бюджетом, стартуя с основного плана без выбывших
    бригад: их заявки решатель пытается раздать остальным.
    """
    from app.solver.engine import Problem, fleet_for_curve
    n = len(problem.engineers)
    u = headline.used_engineers
    total = len(problem.rd.jobs)
    seed = {r.engineer_id: [s.job_id for s in r.stops] for r in headline.routes}
    points = []
    for keep in sorted({max(1, u - 2), max(1, u - 1), u, n}):
        fleet = fleet_for_curve(problem.engineers, keep, headline)
        reduced = Problem(problem.rd, fleet, problem.travel)
        if keep >= u:
            pl, note = headline, "основной прогон"
        else:
            pl = solve(reduced, time_limit_s=seconds,
                       seed_routes={e.id: seed.get(e.id, []) for e in fleet})
            note = ""
        rep = audit_plan(pl, reduced)
        if rep.violations:
            raise SystemExit(f"{problem.rd.name}: штат {keep} — план не проходит "
                             f"аудит: {rep.violations[:3]}")
        points.append(dict(fleet=keep, used=pl.used_engineers, km=pl.total_km,
                           assigned=pl.assigned, unassigned=len(pl.unassigned),
                           source=note or f"{seconds} с"))
        print(f"    штат {keep:2d} -> работают {pl.used_engineers:2d}, "
              f"{pl.assigned}/{total} заявок, {pl.total_km:7.1f} км"
              + (f"  ({note})" if note else ""))
    return points


def _curve_worker(args):
    """Только кривая региона: основной план берётся готовым из artifacts/."""
    from app.api.artifacts import load_plan
    region, seconds = args
    problem = build_problem(region)
    plan = load_plan(region, "solver", problem)
    if plan is None:
        raise SystemExit(f"{region}: нет artifacts/plan_{region}.json — сначала "
                         f"посчитайте основной план")
    print(f"\n=== {region}: штат {len(problem.engineers)}, работают "
          f"{plan.used_engineers}")
    return region, run_curve(problem, seconds, plan)


def curve_only(regions, seconds: int, workers: int) -> None:
    """Пересчитать только кривую «штат -> результат» и дописать её в
    summary.json. Основные планы, базовые варианты и сводка по регионам не
    трогаются."""
    path = OUT / "summary.json"
    if not path.is_file():
        raise SystemExit("нет artifacts/summary.json — сначала build_reference.py")
    summary = json.loads(path.read_text(encoding="utf-8"))
    machine = platform.processor() or platform.machine()
    if summary.get("machine") and summary["machine"] != machine:
        print(f"ВНИМАНИЕ: основные планы считались на другой машине "
              f"({summary['machine']}) — кривая будет с ними несопоставима.")
    jobs = [(r, seconds) for r in regions]
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_curve_worker, jobs))
    else:
        results = [_curve_worker(j) for j in jobs]
    curves = dict(summary.get("curves", {}))
    curves.update(dict(results))
    summary["curves"] = {r: curves[r] for r in REGIONS if r in curves}
    summary["curve_seconds"] = seconds
    summary["curve_built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary["curve_machine"] = machine
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nкривая записана в {path.relative_to(ROOT)}")


def _region_worker(args):
    """Регион целиком в отдельном процессе: основной план, базовый вариант и кривая."""
    region, seconds, want_curve, curve_seconds = args
    problem, plan, row = run_region(region, seconds)
    curve = run_curve(problem, curve_seconds, plan) if want_curve else None
    return row, curve


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=180,
                    help="бюджет солвера на регион, секунды (по умолчанию 180)")
    ap.add_argument("--region", default=None, help="только один регион")
    ap.add_argument("--curve", action="store_true", help="посчитать кривую «штат -> результат»")
    ap.add_argument("--curve-only", action="store_true",
                    help="только кривая по готовым планам из artifacts/, без пересчёта планов")
    ap.add_argument("--jobs", type=int, default=0,
                    help="сколько регионов считать одновременно (0 = по числу ядер минус один)")
    ap.add_argument("--curve-seconds", type=int, default=120,
                    help="бюджет на точку кривой, секунды (по умолчанию 120)")
    a = ap.parse_args()

    regions = [a.region] if a.region else list(REGIONS)
    print(f"Машина: {platform.processor() or platform.machine()}, "
          f"Python {platform.python_version()}")
    if not a.curve_only:
        print(f"Бюджет солвера: {a.seconds} с на регион")

    workers = a.jobs or max(1, min(len(regions), (os.cpu_count() or 2) - 1))
    print(f"Регионов: {len(regions)}, считаем одновременно: {workers}")
    if a.curve_only:
        print(f"Только кривая: {a.curve_seconds} с на точку")
        curve_only(regions, a.curve_seconds, workers)
        return

    summary, curves = [], {}
    if workers > 1 and len(regions) > 1:
        # Солвер однопоточный, поэтому регионы считаются параллельными процессами.
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for row, curve in pool.map(_region_worker,
                                       [(r, a.seconds, a.curve, a.curve_seconds)
                                        for r in regions]):
                summary.append(row)
                if curve:
                    curves[row["region"]] = curve
        summary.sort(key=lambda x: list(REGIONS).index(x["region"]))
    else:
        for r in regions:
            row, curve = _region_worker((r, a.seconds, a.curve, a.curve_seconds))
            summary.append(row)
            if curve:
                curves[r] = curve

    OUT.mkdir(exist_ok=True)
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    machine = platform.processor() or platform.machine()
    for row in summary:
        row.setdefault("built_at", built_at)
        row.setdefault("machine", machine)
    # Пересчёт одного региона (--region) не должен стирать остальные: сводку
    # дополняем, заменяя только свою строку и свою кривую. Иначе исправление
    # координат в одном регионе молча выбросило бы два других из README и слайдов.
    path = OUT / "summary.json"
    if a.region and path.is_file():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("seconds_per_region") not in (None, a.seconds):
            raise SystemExit(f"в summary.json бюджет {old['seconds_per_region']} с, "
                             f"а сейчас {a.seconds} с — регионы стали бы несравнимы")
        keep = [r for r in old.get("regions", []) if r["region"] != a.region]
        summary = sorted(keep + summary, key=lambda x: list(REGIONS).index(x["region"]))
        merged_curves = dict(old.get("curves", {}))
        merged_curves.update(curves)
        curves = {r: merged_curves[r] for r in REGIONS if r in merged_curves}
    path.write_text(json.dumps(dict(
        built_at=built_at,
        seconds_per_region=a.seconds,
        machine=machine,
        regions=summary, curves=curves), ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n" + "=" * 74)
    print(f"{'регион':12s} {'наш план':>22s} {'базовый вариант':>24s}")
    for s in summary:
        p, b = s["plan"], s["baseline"]
        print(f"{s['region']:12s} {p['engineers']:3d} бригад {p['km']:7.1f} км "
              f"{p['assigned']:3d}/{s['jobs']:<3d}  {b['engineers']:3d} бригад "
              f"{b['km']:7.1f} км {b['assigned']:3d}/{s['jobs']}")
    print(f"\nАртефакты: {OUT}")
    print("Проверьте числа, затем закоммитьте artifacts/ — интерфейс берёт планы оттуда.")


if __name__ == "__main__":
    main()
