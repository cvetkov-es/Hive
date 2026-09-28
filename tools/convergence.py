# -*- coding: utf-8 -*-
"""Кривая «время счёта -> результат»: чем дольше ищет решатель, тем лучше план.

Зачем. Эталонные планы считаются по 600 с на регион, а кнопка «Пересчитать» —
20 с, перепланирование по событию — 10 с. Естественный вопрос эксперта: что даёт
каждая лишняя минута и может ли диспетчер ждать десять. Ответ — эта кривая.

Как считается. Ход поиска не зависит от лимита времени: лимит только решает,
где поиск остановится. Поэтому одного прогона на регион хватает на всю кривую:
на каждом найденном плане решатель сообщает, сколько секунд прошло и сколько
в плане бригад, километров и закрытых заявок (engine.solve, on_solution).
Значение кривой в момент t — лучший план, найденный к моменту t.

Где считать. Лимит — по настенным часам, поэтому кривая зависит от машины: на
медленной всё сдвигается вправо. Считать на той же машине, что и эталонные
планы (она записана в artifacts/summary.json), и ничем её в это время не
занимать. Регионы идут параллельно, по процессу на регион, как у эталона.

Запуск:
    python tools/convergence.py                  # 600 с на регион
    python tools/convergence.py --seconds 60     # быстрая проверка
"""
import argparse
import json
import os
import platform
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import REGIONS                                  # noqa: E402
from app.solver.engine import build_problem, solve              # noqa: E402

OUT = ROOT / "artifacts" / "convergence.json"
# Моменты для таблицы в README и подписей на экране. Сама кривая хранится
# целиком — ступеньками, по одной на каждое улучшение.
CHECKPOINTS = [1, 2, 5, 10, 20, 30, 60, 120, 300, 600]
# В интерфейсе столько ждут: перепланирование по событию и «Пересчитать».
# Подписи в README ставит make_numbers.py по такому же словарю.
MARKS = {10: "событие дня", 20: "кнопка «Пересчитать»", 600: "план, посчитанный заранее"}


def run_region(args):
    region, seconds = args
    problem = build_problem(region)
    steps = []

    def record(s):
        # Решатель сообщает и планы хуже лучшего: управляемый поиск нарочно
        # выходит из ямы через ухудшение. Кривая — лучшее найденное к моменту.
        if steps and s["cost"] >= steps[-1]["cost"]:
            return
        steps.append(s)

    plan = solve(problem, time_limit_s=seconds, on_solution=record)
    return dict(region=region, jobs=len(problem.rd.jobs), fleet=len(problem.engineers),
                final=dict(engineers=plan.used_engineers, km=plan.total_km,
                           assigned=plan.assigned),
                steps=[{k: s[k] for k in ("seconds", "engineers", "km", "assigned")}
                       for s in _thin(steps)])


def _thin(steps):
    """Ступеньки, различимые глазом: смена бригад или заявок, либо пробег
    изменился на 0.1 км и больше. Последняя сохраняется всегда."""
    out = []
    for s in steps:
        if (not out or s["engineers"] != out[-1]["engineers"]
                or s["assigned"] != out[-1]["assigned"]
                or abs(s["km"] - out[-1]["km"]) >= 0.1):
            out.append(s)
    if steps and out[-1] is not steps[-1]:
        out.append(steps[-1])
    return out


def at(steps, t):
    """Лучший план к моменту t, или None, если к t решения ещё не было."""
    best = None
    for s in steps:
        if s["seconds"] > t:
            break
        best = s
    return best


def table(regions: dict, seconds: int) -> list:
    rows = []
    for t in [c for c in CHECKPOINTS if c <= seconds] + (
            [seconds] if seconds not in CHECKPOINTS else []):
        row = dict(seconds=t, mark=MARKS.get(t, ""), regions={})
        for name, r in regions.items():
            s = at(r["steps"], t)
            row["regions"][name] = (None if s is None else
                                    dict(engineers=s["engineers"], km=s["km"],
                                         assigned=s["assigned"]))
        if all(v is not None for v in row["regions"].values()):
            vals = row["regions"].values()
            row["total"] = dict(engineers=sum(v["engineers"] for v in vals),
                                km=round(sum(v["km"] for v in vals), 1),
                                assigned=sum(v["assigned"] for v in vals))
        rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser(description="Кривая «время счёта -> результат».")
    ap.add_argument("--seconds", type=int, default=600)
    ap.add_argument("--jobs", type=int, default=0,
                    help="сколько регионов считать одновременно; по умолчанию "
                         "все сразу, если ядер хватает")
    ap.add_argument("--out", default=str(OUT),
                    help="куда сохранить; пробный прогон — не в artifacts/")
    a = ap.parse_args()
    out = Path(a.out)

    machine = platform.processor() or platform.machine()
    cores = os.cpu_count() or 1
    workers = a.jobs or max(1, min(len(REGIONS), cores - 1))
    print(f"Машина: {machine}, ядер {cores}, процессов {workers}, "
          f"{a.seconds} с на регион")
    ref_path = ROOT / "artifacts" / "summary.json"
    ref = json.loads(ref_path.read_text(encoding="utf-8")) if ref_path.is_file() else {}
    if ref.get("machine") and ref["machine"] != machine:
        print(f"ВНИМАНИЕ: эталонные планы считались на другой машине "
              f"({ref['machine']}) — кривая с ними несопоставима.")
    if workers < len(REGIONS):
        print("ВНИМАНИЕ: регионы пойдут по очереди — каждый получит столько же "
              "процессора, но общий расчёт займёт дольше.")

    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_region, [(r, a.seconds) for r in REGIONS]))
    regions = {r["region"]: {k: v for k, v in r.items() if k != "region"}
               for r in results}

    payload = dict(built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   machine=machine, cores=cores, processes=workers,
                   seconds_per_region=a.seconds, regions=regions,
                   table=table(regions, a.seconds))
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{'время':>7}  {'бригад':>6}  {'км':>7}  {'заявок':>7}")
    for row in payload["table"]:
        tot = row.get("total")
        if tot:
            print(f"{row['seconds']:>5} с  {tot['engineers']:>6}  {tot['km']:>7.1f}  "
                  f"{tot['assigned']:>7}  {row['mark']}")
    ref_by = {r["region"]: r["plan"] for r in ref.get("regions", [])}
    for name, r in regions.items():
        f, e = r["final"], ref_by.get(name)
        if e:
            print(f"{name}: итог {f['engineers']} бригад {f['km']:.1f} км, "
                  f"эталон {e['engineers']} бригад {e['km']:.1f} км")
    print(f"\nсохранено: {out}")


if __name__ == "__main__":
    main()
