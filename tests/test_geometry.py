# -*- coding: utf-8 -*-
"""Форма дороги из кэша не должна вести в чужую точку."""
from app.geo import geometry


def test_cached_leg_with_wrong_endpoint_is_not_used(monkeypatch):
    # Сохранённая форма ведёт в Щербинку, а точка — в Таганском районе.
    stale = [[55.69985, 37.77215], [55.60, 37.68], [55.5069, 37.58209]]
    monkeypatch.setattr(geometry, "_CACHE", {"depot:office>job:X": stale})
    office, taganka = (55.6998, 37.7726), (55.7355, 37.6768)
    assert geometry.leg_path("depot:office", "job:X", office, taganka) is None
    # С правильными концами та же форма принимается.
    assert geometry.leg_path("depot:office", "job:X", office, (55.5069, 37.5821)) == stale
    # Без координат проверка не делается.
    assert geometry.leg_path("depot:office", "job:X") == stale


# --- живой запрос формы -------------------------------------------------------
from types import SimpleNamespace as NS

OFFICE, A, B = (55.70, 37.77), (55.73, 37.68), (55.75, 37.60)


def _plan_and_problem():
    """Бригада из офиса едет к A, затем к B. Поддельные план и задача: ровно
    те поля, которые читает plan_geometry."""
    problem = NS(rd=NS(jobs=[NS(id="A", lat=A[0], lon=A[1]), NS(id="B", lat=B[0], lon=B[1])],
                       depots={"office": NS(lat=OFFICE[0], lon=OFFICE[1])}),
                 engineers=[NS(id="E1", depot="office")])
    plan = NS(routes=[NS(engineer_id="E1", stops=[NS(job_id="A", seq=1), NS(job_id="B", seq=2)])])
    return plan, problem


def _road(a, b):
    """Форма «по дорогам»: концы на месте, посередине излом."""
    return [list(a), [(a[0] + b[0]) / 2 + 0.01, (a[1] + b[1]) / 2], list(b)]


def test_leg_missing_from_cache_is_straight_without_live(monkeypatch):
    monkeypatch.setattr(geometry, "_CACHE", {"depot:office>job:A": _road(OFFICE, A)})
    plan, problem = _plan_and_problem()
    g = geometry.plan_geometry(plan, problem)
    legs = g["routes"][0]["legs"]
    assert [l["estimated"] for l in legs] == [False, True]
    assert legs[1]["path"] == [list(A), list(B)]
    assert g["estimated_legs"] == 1 and "прямой" in g["note"]


def test_live_fetches_missing_leg_once_and_remembers(monkeypatch):
    monkeypatch.setattr(geometry, "_CACHE", {"depot:office>job:A": _road(OFFICE, A)})
    calls = []

    def fake(points, timeout=0):
        calls.append(points)
        return [_road(points[i], points[i + 1]) for i in range(len(points) - 1)]

    monkeypatch.setattr(geometry, "ask_route", fake)
    plan, problem = _plan_and_problem()
    g = geometry.plan_geometry(plan, problem, live=True)
    assert [l["estimated"] for l in g["routes"][0]["legs"]] == [False, False]
    assert g["routes"][0]["legs"][1]["path"] == _road(A, B)
    assert g["note"] == ""
    # Один запрос на маршрут бригады со всеми её точками.
    assert calls == [[list(OFFICE), list(A), list(B)]]
    # Второй показ того же плана берёт форму из памяти, без сети.
    geometry.plan_geometry(plan, problem, live=True)
    assert len(calls) == 1
    # И обычный быстрый вызов тоже её видит.
    assert geometry.plan_geometry(plan, problem)["estimated_legs"] == 0


def test_live_without_network_keeps_straight_and_says_why(monkeypatch):
    monkeypatch.setattr(geometry, "_CACHE", {})
    plan, problem = _plan_and_problem()
    g = geometry.plan_geometry(plan, problem, live=True)
    assert g["estimated_legs"] == 2
    assert "не ответил" in g["note"] and "сети в тестах нет" in g["note"]


def test_live_shape_leading_elsewhere_is_rejected(monkeypatch):
    monkeypatch.setattr(geometry, "_CACHE", {})
    far = (55.50, 37.58)                      # Щербинка вместо точки B

    def wrong(points, timeout=0):
        return [_road(points[0], points[1]), _road(points[1], far)]

    monkeypatch.setattr(geometry, "ask_route", wrong)
    plan, problem = _plan_and_problem()
    legs = geometry.plan_geometry(plan, problem, live=True)["routes"][0]["legs"]
    assert [l["estimated"] for l in legs] == [False, True]
