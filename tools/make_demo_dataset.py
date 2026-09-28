#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Демонстрационный набор в формате ТЗ 2.4.

    python tools/make_demo_dataset.py [регион ...]

Кладёт в data/demo/<регион>/ четыре файла:
  jobs.csv           заявки, поля ровно по ТЗ 2.4, справочники 2.4.1
  engineers.csv      исполнители, стартовая точка координатами
  events.json        три события перепланирования
  plan.example.json  эталонный результат в формате ТЗ 2.4.2

Зачем он нужен отдельно от наших выгрузок. Выгрузка Beekeeper — это cp1251,
колонка «Тип заявки BK» и адрес офиса строкой внизу файла; эксперт такой файл
сам не составит и проверить по нему формат не сможет. Демо-набор — тот же
регион в формате, который описан в ТЗ и который можно открыть, прочитать и
подать обратно на вход.

plan.example.json кладётся рядом не для красоты: по нему видно, что мы
понимаем под форматом результата 2.4.2, и его можно сверить с тем, что отдаёт
работающая система.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import DATA, REGIONS                                 # noqa: E402
from app.io.export import plan_to_csv, plan_to_json                  # noqa: E402
from app.io.spec_format import (dump_engineers, dump_events,         # noqa: E402
                                dump_jobs, load_engineers, load_jobs)
from app.solver.engine import build_problem, solve                   # noqa: E402

TIME_LIMIT_S = 20


def make_demo_dataset(region: str, out_dir=None) -> Path:
    p = build_problem(region)
    out = Path(out_dir) if out_dir else (DATA / "demo" / region)
    out.mkdir(parents=True, exist_ok=True)

    dump_jobs(p.rd.jobs, out / "jobs.csv")
    dump_engineers(p.engineers, out / "engineers.csv", p.rd.depots)
    dump_events(p, out / "events.json")

    plan = solve(p, time_limit_s=TIME_LIMIT_S)
    (out / "plan.example.json").write_text(
        json.dumps(plan_to_json(plan, p), ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out / "plan.example.csv").write_text(plan_to_csv(plan, p), encoding="utf-8")

    # Круговая проверка прямо в генераторе: файл, который нельзя прочитать
    # обратно, не набор данных, а мусор с расширением .csv.
    jobs = load_jobs(out / "jobs.csv")
    engs = load_engineers(out / "engineers.csv")
    assert {j.id for j in jobs} == {j.id for j in p.rd.jobs}, \
        f"{region}: круговое преобразование потеряло заявки"
    assert {e.id for e in engs} == {e.id for e in p.engineers}, \
        f"{region}: круговое преобразование потеряло исполнителей"

    print(f"{region}: заявок {len(jobs)}, исполнителей {len(engs)}, "
          f"план {plan.used_engineers} бригад / {plan.total_km} км -> {out}")
    return out


def main(argv):
    regions = argv or list(REGIONS)
    for r in regions:
        if r not in REGIONS:
            raise SystemExit(f"неизвестный регион «{r}», есть: {', '.join(REGIONS)}")
        make_demo_dataset(r)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
