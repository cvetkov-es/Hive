# -*- coding: utf-8 -*-
"""Модель времени в пути.

Один дорожный граф — четыре профиля. Расстояние для всех профилей ОДНО И ТО ЖЕ:
это обязательная метрика ТЗ, и она не должна зависеть от того, кого мы посадили
на маршрут, иначе планы с разным составом парка становятся несравнимыми.
Тип транспорта влияет только на минуты.

OSRM отдаёт время свободного потока (~40-44 км/ч по Москве) — это оптимистично,
поэтому применяем понижающий коэффициент скорости. Прогноз пробок сознательно
не строим: планирование ведётся заранее, и это прямо подтверждено постановщиком
(«берётся среднее значение на передвижение в зависимости от транспортного средства»).
"""
from __future__ import annotations
import json
import math

from ..config import (DATA, CAR_FREEFLOW_FACTOR, BIKE_KMH, FOOT_KMH,
                      TRANSIT_KMH, TRANSIT_ACCESS_MIN)

DETOUR_FACTOR = 1.45   # замерено по трём регионам: 1.44 / 1.51 / 1.26


class TravelModel:
    """distances[i][j] — метры по дорогам, durations[i][j] — секунды (авто, свободный поток)."""

    def __init__(self, points: list, distances: list, durations: list, region: str = ""):
        self.points = points
        self.dist = distances
        self.dur = durations
        self.region = region
        self.index = {p["key"]: i for i, p in enumerate(points)}

    @classmethod
    def load(cls, region: str):
        p = DATA / "matrices" / f"{region}.json"
        if not p.exists():
            raise FileNotFoundError(
                f"Нет матрицы для «{region}». Запустите: python tools/build_matrix.py")
        d = json.loads(p.read_text(encoding="utf-8"))
        return cls(d["points"], d["distances"], d["durations"], region)

    def i(self, key: str) -> int:
        return self.index[key]

    def km(self, a, b) -> float:
        """Пробег между точками, км. Не зависит от транспорта."""
        return self.dist[self._k(a)][self._k(b)] / 1000.0

    def minutes(self, a, b, transport: str) -> int:
        i, j = self._k(a), self._k(b)
        if i == j:
            return 0
        meters = self.dist[i][j]
        if transport == "car":
            return max(1, round(self.dur[i][j] / 60.0 / CAR_FREEFLOW_FACTOR))
        km = meters / 1000.0
        if transport == "bike":
            return max(1, round(km / BIKE_KMH * 60))
        if transport == "foot":
            return max(1, round(km / FOOT_KMH * 60))
        if transport == "transit":
            return max(1, round(TRANSIT_ACCESS_MIN + km / TRANSIT_KMH * 60))
        raise ValueError(f"Неизвестный транспорт: {transport}")

    def all_modes(self, a, b) -> dict:
        """Для карточки объяснения: сколько ехать каждым способом."""
        return {t: self.minutes(a, b, t) for t in ("car", "bike", "transit", "foot")}

    @classmethod
    def estimated(cls, points: list, region: str = "") -> "TravelModel":
        """Матрица целиком по прямой — для наборов, которых нет среди наших
        регионов. Каждая точка помечена estimated: интерфейс обязан сказать,
        что расстояния приблизительные, а не выдать оценку за измерение."""
        pts = [dict(p, estimated=True) for p in points]
        n = len(pts)
        dist = [[0] * n for _ in range(n)]
        dur = [[0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                m = haversine_km(pts[i]["lat"], pts[i]["lon"],
                                 pts[j]["lat"], pts[j]["lon"]) * DETOUR_FACTOR * 1000
                dist[i][j] = int(round(m))
                dur[i][j] = int(round(m / 1000.0 / 40.0 * 3600))
        return cls(pts, dist, dur, region)

    def with_point(self, key: str, lat: float, lon: float, legs=None) -> "TravelModel":
        """Копия модели с ещё одной точкой в конце.

        Нужна ровно для одного случая: диспетчер вводит срочную заявку по
        адресу, которого не было в выгрузке, а дорожная матрица посчитана
        офлайн и новых строк не содержит. `legs` — плечи по дорогам, взятые
        живым запросом к OSRM (geo/roads.py). Без них — по прямой с
        коэффициентом извилистости, замеренным по этим же трём регионам.

        Точка добавляется В КОНЕЦ: порядок узлов «сначала депо, потом заявки»
        сохраняется, и проверка соответствия матрицы модели в Problem остаётся
        в силе. Приблизительность помечается в points[i]["estimated"], чтобы
        интерфейс показал плашку, а не выдал оценку за измерение.
        """
        if key in self.index:
            raise ValueError(f"точка {key} уже есть в матрице")
        pts = list(self.points) + [dict(key=key, lat=lat, lon=lon,
                                        estimated=legs is None)]
        n = len(pts)
        dist = [list(row) + [0] for row in self.dist]
        dur = [list(row) + [0] for row in self.dur]
        dist.append([0] * n)
        dur.append([0] * n)
        for i, pt in enumerate(pts[:-1]):
            if legs is not None:
                dist[i][n - 1], dur[i][n - 1] = legs.to_new[i]
                dist[n - 1][i], dur[n - 1][i] = legs.from_new[i]
                continue
            m = haversine_km(pt["lat"], pt["lon"], lat, lon) * DETOUR_FACTOR * 1000
            sec = m / 1000.0 / 40.0 * 3600          # 40 км/ч свободного потока
            dist[i][n - 1] = dist[n - 1][i] = int(round(m))
            dur[i][n - 1] = dur[n - 1][i] = int(round(sec))
        return TravelModel(pts, dist, dur, self.region)

    def is_estimated(self, key: str) -> bool:
        """Плечо до этой точки посчитано по прямой, а не по дорогам."""
        i = self.index.get(key)
        return bool(i is not None and self.points[i].get("estimated"))

    def _k(self, x):
        return x if isinstance(x, int) else self.index[x]


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))
