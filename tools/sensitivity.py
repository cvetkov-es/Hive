# -*- coding: utf-8 -*-
"""Цена каждого ограничения и каждого допущения, в бригадах и километрах.

Зачем. Справочника исполнителей в данных нет — парк, квалификации, транспорт и
оборудование мы смоделировали сами. Проверить эти числа снаружи невозможно, и
естественное подозрение эксперта: «вы подобрали входные данные под красивый
результат». Единственная защита — показать, сколько стоит каждое правило, и дать
его снять. Тогда разговор идёт не о том, верить ли нашим числам, а о том, какие
правила действуют у заказчика на самом деле.

Первая строка («Как есть») — основной план из artifacts/, а не новый прогон:
иначе таблица начиналась бы с числа, которое расходится с результатом, и
разница в бригаду читалась бы как цена правила, хотя это разброс поиска.
Остальные строки — прогон с одним снятым ограничением или изменённым
допущением. Если основной план проходит аудит и при изменённом правиле
(правило только ослаблено), он тоже решение этой задачи, и в строку идёт
лучшее из двух: нового прогона и основного плана. Так ослабление правила не
показывает лишней бригады из-за разброса поиска, а крупный выигрыш, который
находит новый прогон, сохраняется.

Запуск:
    python tools/sensitivity.py --seconds 180
    python tools/sensitivity.py --seconds 120 --region Восток
"""
import argparse
import copy
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config                                          # noqa: E402
from app.config import REGIONS                                   # noqa: E402
import app.solver.engine                                         # noqa: E402,F401
import app.solver.audit                                          # noqa: E402,F401

OUT = ROOT / "artifacts"


def relax_skills(problem, Problem):
    """Снять квалификацию: каждая бригада умеет всё."""
    engs = [copy.copy(e) for e in problem.engineers]
    for e in engs:
        e.skills = set(config.SKILLS)
    return Problem(problem.rd, engs, problem.travel)


def relax_transport(problem, Problem):
    """Снять ТРЕБОВАНИЕ к транспорту, не меняя состав парка.

    Пересадить всех на автомобиль было бы ошибкой измерения: это одновременно
    снимает ограничение И ускоряет весь парк, и строка таблицы показывала бы
    сумму двух эффектов вместо цены ограничения.
    """
    rd = copy.copy(problem.rd)
    rd.jobs = [copy.copy(j) for j in problem.rd.jobs]
    for j in rd.jobs:
        j.requires_transport = None
    return Problem(rd, problem.engineers, problem.travel)


def relax_equipment(problem, Problem):
    """Снять ограничение по оборудованию: запас неисчерпаем."""
    engs = [copy.copy(e) for e in problem.engineers]
    for e in engs:
        e.equipment = {k: 999 for k in config.EQUIPMENT}
    return Problem(problem.rd, engs, problem.travel)


def relax_clusters(problem, Problem):
    """Снять ТОЛЬКО зоны: любая бригада едет в любой город региона.

    Требование к транспорту при этом сохраняется — иначе в одной строке
    смешались бы два разных ограничения.
    """
    rd = copy.copy(problem.rd)
    rd.jobs = [copy.copy(j) for j in problem.rd.jobs]
    for j in rd.jobs:
        j.cluster = "Москва"
    engs = [copy.copy(e) for e in problem.engineers]
    for e in engs:
        e.cluster, e.depot = "Москва", "office"
    return Problem(rd, engs, problem.travel)


def relax_zone_ban(problem, Problem):
    """Снять ТОЛЬКО запрет ездить в чужую зону; базы бригад остаются на месте.

    Прямая проверка разъяснения куратора от 22.09: отправка московской бригады
    в Домодедово или Каширу «не считается ошибкой», нежелательно лишь кочевание
    Москва → область → Москва. В отличие от relax_clusters, бригада из Каширы
    по-прежнему выезжает из Каширы: меряется цена запрета, а не цена баз.
    """
    rd = copy.copy(problem.rd)
    rd.jobs = [copy.copy(j) for j in problem.rd.jobs]
    for j in rd.jobs:
        j.cluster = "весь регион"
    engs = [copy.copy(e) for e in problem.engineers]
    for e in engs:
        e.cluster = "весь регион"
    return Problem(rd, engs, problem.travel)


def relax_late(problem, Problem):
    """Сценарий мягких окон: аудит проверяет жёсткое правило и будет прав,
    поэтому строку помечаем как неаудируемую этим набором проверок."""
    return problem


SCENARIOS = [
    ("Как есть", None, None,
     "все правила действуют"),
    ("Без квалификаций", relax_skills, None,
     "каждая бригада умеет все три типа работ"),
    ("Без требований к транспорту", relax_transport, None,
     "заявке не нужен автомобиль; парк тот же, пешие ходят пешком"),
    ("Без лимита оборудования", relax_equipment, None,
     "утренний запас оборудования у бригады не кончается"),
    ("Без зон и без местных баз", relax_clusters, None,
     "все бригады выезжают из московского офиса и едут в любой город региона"),
    ("Без запрета зон, базы на месте", relax_zone_ban, None,
     "бригада может взять заявку в чужой зоне, но выезжает из своей базы"),
    ("Рабочий день 10 часов", None, dict(MAX_WORKDAY_MIN=10 * 60),
     "жёстче нашего норматива"),
    ("Рабочий день 13 часов", None, dict(MAX_WORKDAY_MIN=13 * 60),
     "как фактически бывает в контрольном дне"),
    ("Опоздание до 30 минут", None, dict(LATE_MAX_MIN=30),
     "окна мягкие. Аудитор проверяет ЖЁСТКОЕ правило и закономерно находит "
     "нарушения окон — это не дефект плана, а другая постановка. Дельта по "
     "километрам с остальными строками несопоставима."),
    ("Без надбавки на подход", None, dict(APPROACH_MIN=0),
     "визит = норматив минус 20, без 10 минут на парковку"),
]


# Модули, которые забирают константы конфигурации через `from ... import` и потому
# не видят подмену значения. Их нужно перезагрузить ПОСЛЕ изменения config, иначе
# сценарий молча отработает со старыми числами и строка таблицы соврёт «ноль разницы».
DEPENDENT = ("app.io.normatives", "app.io.csv_loader", "app.solver.feasibility",
             "app.solver.metrics", "app.solver.baseline", "app.solver.audit",
             "app.solver.engine")


def _reload_dependents():
    import importlib
    for name in DEPENDENT:
        importlib.reload(importlib.import_module(name))
    eng = importlib.import_module("app.solver.engine")
    aud = importlib.import_module("app.solver.audit")
    return eng.build_problem, eng.solve, eng.Problem, aud.audit_plan


def _better(a, b) -> bool:
    """План a лучше b в порядке целей: заявки, потом бригады, потом километры."""
    return ((a.assigned, -a.used_engineers, -a.total_km)
            > (b.assigned, -b.used_engineers, -b.total_km))


def run_one(region, name, relax, overrides, seconds):
    saved = {k: getattr(config, k) for k in (overrides or {})}
    for k, v in (overrides or {}).items():
        setattr(config, k, v)
    try:
        build, slv, Prob, audit = _reload_dependents()
        problem = build(region)
        if relax:
            problem = relax(problem, Prob)
        from app.api.artifacts import load_plan
        main = load_plan(region, "solver", problem)
        if main is None:
            raise SystemExit(f"{region}: нет artifacts/plan_{region}.json")
        # У сценария с мягкими окнами нарушения окон ожидаемы: аудитор проверяет
        # жёсткое правило. Остальные проверки для него по-прежнему обязательны.
        soft = bool(overrides and overrides.get("LATE_MAX_MIN"))

        def violations(pl):
            return [v for v in audit(pl, problem).violations
                    if not (soft and v.startswith("[A6]"))]

        t0, c0 = time.time(), time.process_time()
        took_main = name == "Как есть"
        if took_main:
            plan = main
        else:
            plan = slv(problem, time_limit_s=seconds)
            if _better(main, plan) and not violations(main):
                plan, took_main = main, True
        viol = violations(plan)
        return dict(scenario=name, engineers=plan.used_engineers, km=plan.total_km,
                    assigned=plan.assigned, total=len(problem.rd.jobs),
                    late=plan.late_jobs, audit_ok=not viol,
                    violations=viol[:5], soft_windows=soft, main_plan=took_main,
                    cpu_seconds=round(time.process_time() - c0, 1),
                    seconds=round(time.time() - t0, 1))
    finally:
        for k, v in saved.items():
            setattr(config, k, v)
        _reload_dependents()


def _worker(args):
    """Один прогон в отдельном процессе.

    Солвер OR-Tools однопоточный, поэтому единственный способ занять машину —
    считать разные сценарии параллельно. Отдельные процессы здесь даже удобнее
    потоков: сценарий подменяет константы конфигурации и перезагружает модули,
    а в своём интерпретаторе это никому не мешает.
    """
    region, idx, seconds = args
    name, relax, over, note = SCENARIOS[idx]
    r = run_one(region, name, relax, over, seconds)
    r["note"], r["order"] = note, idx
    return region, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=180)
    ap.add_argument("--region", default=None)
    ap.add_argument("--jobs", type=int, default=0,
                    help="сколько прогонов считать одновременно (0 = по числу ядер минус один)")
    a = ap.parse_args()
    regions = [a.region] if a.region else list(REGIONS)

    tasks = [(reg, i, a.seconds) for reg in regions for i in range(len(SCENARIOS))]
    workers = a.jobs or max(1, (os.cpu_count() or 2) - 1)
    workers = min(workers, len(tasks))
    eta = a.seconds * ((len(tasks) + workers - 1) // workers) / 60
    print(f"Прогонов: {len(tasks)}, одновременно: {workers}, "
          f"бюджет {a.seconds} с — ожидаемое время около {eta:.0f} мин")

    done = {reg: {} for reg in regions}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for n, (region, r) in enumerate(pool.map(_worker, tasks), 1):
            done[region][r["order"]] = r
            flag = "" if r["audit_ok"] else "  АУДИТ ПРОВАЛЕН"
            print(f"  [{n:2d}/{len(tasks)}] {region:11s} {r['scenario']:30s} "
                  f"{r['engineers']:2d} бригад {r['km']:7.1f} км{flag}", flush=True)
    print(f"Все прогоны заняли {(time.time() - t0) / 60:.1f} мин")

    all_rows = {}
    for region in regions:
        rows, base = [], None
        for i in range(len(SCENARIOS)):
            r = done[region][i]
            if base is None:
                base = r
            r["d_eng"] = r["engineers"] - base["engineers"]
            r["d_km"] = round(r["km"] - base["km"], 1)
            rows.append(r)
        all_rows[region] = rows
        print(f"\n{'=' * 78}\n{region}: цена ограничений (бюджет {a.seconds} с на прогон)\n")
        for r in rows:
            d = ("база" if r["scenario"] == "Как есть"
                 else f"{r['d_eng']:+d} бригад, {r['d_km']:+.1f} км")
            flag = "" if r["audit_ok"] else "   ПРОВАЛЕН АУДИТ, строка недостоверна"
            soft = "  *мягкие окна" if r.get("soft_windows") else ""
            print(f"  {r['scenario']:30s} {r['engineers']:2d} бригад {r['km']:7.1f} км "
                  f"{r['assigned']:3d}/{r['total']:<3d}  {d}{soft}{flag}")
        bad = [r for r in rows if not r["audit_ok"]]
        if bad:
            print(f"\n  ВНИМАНИЕ: {len(bad)} строк не прошли аудит и НЕ ГОДЯТСЯ для слайда:")
            for r in bad:
                print(f"    {r['scenario']}: {'; '.join(r.get('violations', []))[:160]}")

    OUT.mkdir(exist_ok=True)
    (OUT / "sensitivity.json").write_text(
        json.dumps(dict(seconds=a.seconds, jobs=workers, from_main_plan=True,
                        regions=all_rows),
                   ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\nСохранено: {OUT / 'sensitivity.json'}")
    print("Отрицательная дельта = ограничение стоит нам столько бригад и километров.")
    print("«Как есть» — основной план; где он допустим и лучше нового прогона, "
          "строка берёт его.")


if __name__ == "__main__":
    main()
