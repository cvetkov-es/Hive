# -*- coding: utf-8 -*-
"""Пакет `app` лежит в backend/, а тесты запускаются из корня репозитория."""
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest


@pytest.fixture(autouse=True)
def _no_live_roads(monkeypatch):
    """Тесты в сеть не ходят. Живые запросы к OSRM — дороги до новой точки
    (geo/roads.py) и форма участка на карте (geo/geometry.py) — по умолчанию
    «не отвечают», как без сети. Тест, которому нужен ответ, подменяет
    ask_table или ask_route сам."""
    from app.geo import roads

    def offline(coords, timeout=0):
        raise roads.RoadsUnavailable("сети в тестах нет")

    monkeypatch.setattr(roads, "ask_table", offline)
    roads._MEMO.clear()

    # То же для формы дороги на карте (geo/geometry.py): без сети — прямая.
    from app.geo import geometry

    def no_shape(points, timeout=0):
        raise geometry.ShapeUnavailable("сети в тестах нет")

    monkeypatch.setattr(geometry, "ask_route", no_shape)
    geometry._LIVE.clear()
    yield
    roads._MEMO.clear()
    geometry._LIVE.clear()
