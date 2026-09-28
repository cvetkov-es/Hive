# -*- coding: utf-8 -*-
"""Кривая «штат -> результат»: кого оставить, если бригад меньше."""
from types import SimpleNamespace as NS

from app.solver.engine import fleet_for_curve


def _eng(i, zone):
    return NS(id=f"E{i}", cluster=zone)


def _plan(loads):
    return NS(routes=[NS(engineer_id=k, stops=[None] * n) for k, n in loads.items()])


ENGS = [_eng(1, "Москва"), _eng(2, "Москва"), _eng(3, "Москва"),
        _eng(4, "Кашира"), _eng(5, "Москва")]
# E2 и E5 без заявок; E4 — единственная бригада Каширы, у неё одна заявка.
PLAN = _plan({"E1": 5, "E2": 0, "E3": 2, "E4": 1, "E5": 0})


def ids(fleet):
    return [e.id for e in fleet]


def test_idle_brigades_leave_first():
    # Работают трое; из четырёх мест четвёртое — первой по списку из незанятых.
    assert ids(fleet_for_curve(ENGS, 4, PLAN)) == ["E1", "E2", "E3", "E4"]
    assert ids(fleet_for_curve(ENGS, 3, PLAN)) == ["E1", "E3", "E4"]


def test_then_least_loaded_but_zone_keeps_a_brigade():
    # Наименее загружена E4, но она последняя в Кашире — уходит E3.
    assert ids(fleet_for_curve(ENGS, 2, PLAN)) == ["E1", "E4"]


def test_full_and_empty_fleet():
    assert ids(fleet_for_curve(ENGS, 99, PLAN)) == ["E1", "E2", "E3", "E4", "E5"]
    assert fleet_for_curve(ENGS, 0, PLAN) == []


def test_every_working_brigade_is_kept_while_places_allow():
    """Пока мест не меньше, чем работающих, основной план выполним на урезанном
    штате: ни одна работающая бригада не выбывает."""
    working = {"E1", "E3", "E4"}
    for keep in range(3, 6):
        assert working <= set(ids(fleet_for_curve(ENGS, keep, PLAN)))
