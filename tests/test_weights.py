# -*- coding: utf-8 -*-
"""Арифметика лексикографического порядка целей.

Приоритет задан постановщиком: «Больше выполненных заявок — это основной
критерий, а второй по приоритету — меньше исполнителей». Дальше — пробег.
Порядок реализован не отдельными проходами, а разделением масштабов в одной
целевой функции, поэтому он держится на неравенствах между константами.
Если хоть одно из них нарушено, солвер молча начнёт торговать заявки за
километры, и увидеть это в готовом плане невозможно.
"""
from app.solver import weights as W

MAX_FLEET = 15          # самый большой парк по трём регионам (Юго-восток)


def test_distance_never_outweighs_a_vehicle():
    """Экономия километров не может оправдать лишнюю бригаду.

    Верхняя граница выигрыша по расстоянию от добавления одной бригады — весь
    пробег плана, то есть не больше MAX_ROUTE_M на каждую из бригад.
    """
    assert W.MAX_ROUTE_M * MAX_FLEET < W.VEHICLE_COST


def test_vehicles_never_outweigh_a_dropped_job():
    """Брошенная заявка дороже всего парка вместе с его пробегом: заявку не
    отдают ради экономии бригад."""
    assert (MAX_FLEET * W.VEHICLE_COST + MAX_FLEET * W.MAX_ROUTE_M
            < W.drop_penalty(MAX_FLEET))


def test_no_integer_overflow():
    """Целевая функция считается в int64. Худший случай — сотня заявок высшего
    приоритета, брошенных разом."""
    assert W.drop_penalty(MAX_FLEET) * max(W.PRIORITY_MULT.values()) * 100 < 2 ** 63


def test_priority_order_matches_the_customer():
    """Авария -> Подключение -> Ремонт/Дозаказ (разъяснение экспертов 19.09)."""
    from app.config import PRIO_CONNECT, PRIO_EMERGENCY, PRIO_ROUTINE
    assert (W.PRIORITY_MULT[PRIO_EMERGENCY] > W.PRIORITY_MULT[PRIO_CONNECT]
            > W.PRIORITY_MULT[PRIO_ROUTINE] >= 1)


def test_late_penalty_is_cheaper_than_a_vehicle():
    """Штраф за опоздание включается только при перепланировании. Он обязан
    быть дешевле бригады: иначе солвер вызовет человека ради двух минут."""
    from app.config import REPLAN_LATE_MAX_MIN
    assert W.LATE_PENALTY_PER_MIN * REPLAN_LATE_MAX_MIN < W.VEHICLE_COST


def test_stability_penalty_sits_between_a_leg_and_a_vehicle():
    """Штраф за перетасовку должен быть заметно больше типичного плеча (3-7 км),
    иначе он не удержит ни одного назначения, и заметно меньше стоимости
    бригады, иначе переназначение станет невозможным."""
    assert 7_000 < W.STABILITY_PENALTY < W.VEHICLE_COST
