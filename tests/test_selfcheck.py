# -*- coding: utf-8 -*-
"""Самопроверка окружения при старте.

У самопроверки два обязательства, и они тянут в разные стороны. Она обязана
ЛОВИТЬ поломку — иначе это украшение шапки. И она обязана НЕ ПАДАТЬ на ней:
план реализации требует предупреждения, а не отказа старта, потому что
работающий интерфейс с честной плашкой полезнее пустого экрана.

Поэтому тесты идут парами: ломаем окружение — проверка краснеет; ломаем
окружение — функция всё равно возвращает отчёт. Тест «на исправных данных всё
зелено» без них не значит ничего: его прошла бы и функция, которая ничего не
измеряет.
"""
import copy

import pytest

from app.api import selfcheck
from app.config import REGIONS


@pytest.fixture(scope="module")
def report():
    return selfcheck.run_selfcheck()


def test_real_environment_is_green(report):
    red = [f"{c['name']}: {c['detail']}" for c in report["checks"] if not c["ok"]]
    assert not red, red
    assert report["ok"] and report["passed"] == report["total"]


def test_every_region_is_checked(report):
    """Одного региона мало: матрицы, парк и эталон собираются по отдельности,
    и потерять можно любой из трёх."""
    names = " | ".join(c["name"] for c in report["checks"])
    for region in REGIONS:
        assert region in names, names


def test_reference_plan_is_audited_not_just_opened(monkeypatch):
    """Эталон проверяется аудитом. Факт существования файла не значит, что
    внутри план, а не то, что осталось от прошлой версии модели."""
    real = selfcheck.artifacts.load_plan

    def tampered(region, algo, problem):
        plan = copy.deepcopy(real(region, algo, problem))
        donor = next(r for r in plan.routes if r.stops)
        victim = next(r for r in plan.routes if r is not donor)
        victim.stops.append(copy.deepcopy(donor.stops[0]))   # заявка дважды
        return plan

    monkeypatch.setattr(selfcheck.artifacts, "load_plan", tampered)
    rep = selfcheck.run_selfcheck(regions=(REGIONS[0],))
    assert rep["ok"] is False
    red = [c for c in rep["checks"] if not c["ok"]]
    assert red and all(c["name"].startswith("эталон") for c in red), rep["checks"]


def test_missing_reference_plan_warns_and_tells_what_to_run(monkeypatch):
    monkeypatch.setattr(selfcheck.artifacts, "load_plan",
                        lambda region, algo, problem: None)
    rep = selfcheck.run_selfcheck(regions=(REGIONS[0],))
    assert rep["ok"] is False
    red = next(c for c in rep["checks"] if not c["ok"])
    assert "build_reference" in red["detail"], red


def test_unbuildable_region_does_not_raise(monkeypatch):
    """Сборка задачи — самая дорогая проверка и единственная, которая может
    упасть чем угодно. Любое исключение обязано превратиться в строку отчёта."""
    def boom(region):
        raise FileNotFoundError(f"нет матрицы для «{region}»")

    monkeypatch.setattr(selfcheck, "build_problem", boom)
    rep = selfcheck.run_selfcheck()
    assert rep["ok"] is False
    assert any("нет матрицы" in c["detail"] for c in rep["checks"]), rep["checks"]
    assert rep["passed"] < rep["total"]


def test_missing_geocache_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(selfcheck, "DATA", tmp_path)
    rep = selfcheck.run_selfcheck(regions=())
    geo = next(c for c in rep["checks"] if c["name"] == "геокэш")
    assert geo["ok"] is False and "normalize_addr" in geo["detail"]
    assert rep["ok"] is False


def test_headline_names_the_damage(monkeypatch):
    """Шапка показывает одну строку. Если она не называет число провалов,
    диспетчер видит «что-то не так» и не знает, стоит ли верить экрану."""
    def boom(region):
        raise RuntimeError("сломано")

    monkeypatch.setattr(selfcheck, "build_problem", boom)
    rep = selfcheck.run_selfcheck(regions=(REGIONS[0],))
    failed = rep["total"] - rep["passed"]
    assert str(failed) in rep["headline"] and str(rep["total"]) in rep["headline"]
