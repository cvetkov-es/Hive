# -*- coding: utf-8 -*-
"""Дороги до нового адреса — живым запросом к OSRM, с деградацией до прямой.

Сеть здесь улучшение, а не условие: не ответил OSRM — плечи по прямой с
пометкой оценки, как без сети. И одно событие видит один ответ: задача
собирается дважды, и второй запрос не должен разойтись с первым.
"""
import pytest

from app.geo import roads
from app.geo.travel import TravelModel

POINTS = [dict(key="depot:office", lat=55.75, lon=37.62),
          dict(key="job:1", lat=55.70, lon=37.70)]


def _table(n_old, lat, lon, snap=None):
    """Ответ OSRM на n_old точек плюс новую: несимметричные плечи, чтобы было
    видно, какое направление куда легло."""
    n = n_old + 1
    dist = [[0 if i == j else 1000 * (i + 1) + 10 * j for j in range(n)] for i in range(n)]
    dur = [[0 if i == j else 100 * (i + 1) + j for j in range(n)] for i in range(n)]
    slat, slon = snap or (lat, lon)
    sources = [{"location": [0.0, 0.0]}] * n_old + [{"location": [slon, slat]}]
    return {"code": "Ok", "distances": dist, "durations": dur, "sources": sources}


def test_legs_are_taken_in_both_directions(monkeypatch):
    monkeypatch.setattr(roads, "ask_table",
                        lambda coords, timeout=0: _table(2, 55.72, 37.65))
    legs = roads.legs_to(POINTS, 55.72, 37.65)
    # к новой точке (столбец 2) и от неё (строка 2) — разные числа
    assert legs.to_new == ((1020, 102), (2020, 202))
    assert legs.from_new == ((3000, 300), (3010, 301))


def test_point_far_from_roads_is_not_trusted(monkeypatch):
    monkeypatch.setattr(roads, "ask_table",
                        lambda coords, timeout=0: _table(2, 55.72, 37.65, snap=(55.80, 37.65)))
    with pytest.raises(roads.RoadsUnavailable, match="ближайшая дорога"):
        roads.legs_to(POINTS, 55.72, 37.65)


def test_too_many_points_do_not_go_to_the_public_server(monkeypatch):
    def forbidden(coords, timeout=0):
        raise AssertionError("запрос не должен был уйти")
    monkeypatch.setattr(roads, "ask_table", forbidden)
    many = [dict(key=f"job:{i}", lat=55.7, lon=37.6) for i in range(roads.MAX_POINTS)]
    with pytest.raises(roads.RoadsUnavailable, match="точек больше"):
        roads.legs_to(many, 55.72, 37.65)


def test_one_request_per_point_success_and_failure_alike(monkeypatch):
    calls = []

    def once(coords, timeout=0):
        calls.append(1)
        raise roads.RoadsUnavailable("сеть недоступна")
    monkeypatch.setattr(roads, "ask_table", once)
    a = roads.remembered_legs("Восток", "job:N", POINTS, 55.72, 37.65)
    b = roads.remembered_legs("Восток", "job:N", POINTS, 55.72, 37.65)
    assert a == b == (None, "сеть недоступна")
    assert len(calls) == 1, "второй сбор задачи спросил OSRM заново и мог получить другой ответ"


def test_with_point_uses_roads_and_is_not_an_estimate():
    base = TravelModel(POINTS, [[0, 5000], [5200, 0]], [[0, 400], [420, 0]])
    legs = roads.Legs(to_new=((1020, 102), (2020, 202)),
                      from_new=((3000, 300), (3010, 301)))
    t = base.with_point("job:NEW", 55.72, 37.65, legs=legs)
    assert not t.is_estimated("job:NEW")
    assert t.km("job:1", "job:NEW") == 2.02
    assert t.km("job:NEW", "job:1") == 3.01
    # без плеч — по прямой, с пометкой оценки
    assert base.with_point("job:NEW", 55.72, 37.65).is_estimated("job:NEW")


def test_event_uses_roads_when_osrm_answers(monkeypatch):
    from app.solver.engine import build_problem
    from app.solver.replan import Event, make_demo_emergency, problem_with_event
    problem = build_problem("Восток")
    job = make_demo_emergency(problem, 15 * 60 + 40)
    n = len(problem.travel.points)
    monkeypatch.setattr(roads, "ask_table",
                        lambda coords, timeout=0: _table(n, job.lat, job.lon))
    p = problem_with_event(problem, Event(kind="urgent", at_min=15 * 60 + 40, job=job))
    key = f"job:{job.id}"
    assert not p.travel.is_estimated(key)
    assert p.travel.dist[0][p.travel.index[key]] == 1000 + 10 * n


def test_event_falls_back_to_straight_line_without_osrm():
    """Сеть в тестах отключена (conftest): точка встаёт, плечи — оценка."""
    from app.solver.engine import build_problem
    from app.solver.replan import Event, make_demo_emergency, problem_with_event
    problem = build_problem("Восток")
    job = make_demo_emergency(problem, 15 * 60 + 40)
    p = problem_with_event(problem, Event(kind="urgent", at_min=15 * 60 + 40, job=job))
    assert p.travel.is_estimated(f"job:{job.id}")
