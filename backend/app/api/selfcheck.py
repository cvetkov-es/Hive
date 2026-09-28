# -*- coding: utf-8 -*-
"""Самопроверка окружения при старте.

Правило одно: любой провал — ПРЕДУПРЕЖДЕНИЕ, а не отказ старта. Работающий
интерфейс с честной плашкой «матрица Юго-востока не найдена»
полезнее пустого экрана с трассировкой стека. Что именно сломано, диспетчер
должен видеть в шапке, а не узнавать по молчанию карты.

Проверяется не наличие файлов, а пригодность данных. Файл матрицы на месте —
это ещё не значит, что её размерность совпала с числом точек модели; файл
эталонного плана на месте — это ещё не значит, что внутри план, а не то, что
осталось от прошлой версии модели. Поэтому задача действительно собирается,
а эталон действительно прогоняется через независимый аудит.
"""
from __future__ import annotations
import json

from . import artifacts
from ..config import DATA, REGIONS
from ..solver.audit import audit_plan
from ..solver.engine import build_problem


def run_selfcheck(regions: tuple = REGIONS) -> dict:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = ""):
        checks.append(dict(name=name, ok=bool(ok), detail=detail))

    _check_geocache(add)
    for region in regions:
        _check_region(region, add)

    failed = sum(1 for c in checks if not c["ok"])
    return dict(ok=failed == 0, passed=len(checks) - failed, total=len(checks),
                checks=checks,
                headline=("Окружение в порядке" if not failed else
                          f"Не прошло проверок: {failed} из {len(checks)} — "
                          f"работаем с ограничениями"))


def _check_geocache(add) -> None:
    path = DATA / "geo" / "geocode.json"
    if not path.is_file():
        add("геокэш", False, "нет data/geo/geocode.json — запустите tools/normalize_addr.py")
        return
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        add("геокэш", False, f"файл не читается: {exc}; пересоберите tools/normalize_addr.py")
        return
    add("геокэш", bool(cache), f"адресов {len(cache)}" if cache else
        "кэш пуст — запустите tools/normalize_addr.py")


def _check_region(region: str, add) -> None:
    """Четыре проверки на регион. Первая — самая дорогая: если задача не
    собирается, остальные три проверять не на чем, и молчать о них честнее,
    чем красить в красный по той же самой причине."""
    try:
        problem = build_problem(region)
    except Exception as exc:                       # noqa: BLE001 — сообщаем любое
        add(f"данные {region}", False, f"{type(exc).__name__}: {exc}")
        return

    add(f"данные {region}", True,
        f"заявок {len(problem.rd.jobs)}, бригад {len(problem.engineers)}")

    _check_matrix(region, problem, add)
    add(f"точки выезда {region}", bool(problem.rd.depots),
        f"офис и базы: {len(problem.rd.depots)}" if problem.rd.depots else
        "ни офиса, ни базы — маршруту неоткуда начинаться")
    _check_reference(region, problem, add)


def _check_matrix(region: str, problem, add) -> None:
    """Размерность матрицы против модели. Матрица считается офлайн отдельным
    скриптом: стоит добавить заявку и не пересобрать матрицу — и номера точек
    разъедутся. Плечи при этом остаются правдоподобными, просто не теми."""
    want = len(problem.rd.depots) + len(problem.rd.jobs)
    points = len(problem.travel.points)
    square = (len(problem.travel.dist) == points
              and all(len(row) == points for row in problem.travel.dist)
              and len(problem.travel.dur) == points
              and all(len(row) == points for row in problem.travel.dur))
    ok = points == want and square
    add(f"матрица {region}", ok,
        f"{points}×{points} точек" if ok else
        f"точек в матрице {points}, в модели {want} "
        f"(офис и базы {len(problem.rd.depots)} + заявок {len(problem.rd.jobs)}) — "
        f"запустите tools/build_matrix.py")


def _check_reference(region: str, problem, add) -> None:
    name = f"эталон {region}"
    try:
        plan = artifacts.load_plan(region, "solver", problem)
    except Exception as exc:                       # noqa: BLE001 — сообщаем любое
        add(name, False, f"{exc}")
        return
    if plan is None:
        add(name, False, "нет заранее посчитанного плана — запустите "
                         "tools/build_reference.py (кнопка «Спланировать» будет "
                         "считать вживую)")
        return
    report = audit_plan(plan, problem)
    add(name, report.ok,
        f"аудит {report.passed}/{report.total}, пробег {plan.total_km:.1f} км"
        if report.ok else
        f"аудит {report.passed}/{report.total}: {report.violations[0]} — "
        f"пересоберите tools/build_reference.py")
