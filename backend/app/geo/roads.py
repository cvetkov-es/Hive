# -*- coding: utf-8 -*-
"""Дороги до нового адреса — живым запросом к OSRM.

Дорожные матрицы считаются заранее (tools/build_matrix.py) и новых адресов не
содержат. Когда диспетчер вводит аварию по адресу, которого не было в
выгрузке, координаты уже берутся геокодером; тем же путём можно взять и
дороги: один запрос к тому же OSRM, что считал матрицы, — расстояние и время
от новой точки до каждой точки региона и обратно.

Сеть здесь — улучшение, а не условие. Не ответил OSRM, точка оказалась
далеко от дорог или точек больше, чем публичный сервер принимает в одном
запросе, — плечи достраиваются по прямой с коэффициентом извилистости
(TravelModel.with_point) и помечаются оценкой.

Ответ запоминается по точке. Одно событие собирает задачу дважды (расчёт и
выдача плана), и второй запрос мог бы ответить иначе, чем первый: план был бы
посчитан по дорогам, а показан по прямой. С памятью оба раза один ответ —
удачный или нет.
"""
from __future__ import annotations
import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .geocode import USER_AGENT
from .polite import Throttle
from .travel import haversine_km

OSRM_TABLE = "https://router.project-osrm.org/table/v1/driving/"
TIMEOUT_S = 6.0
MAX_POINTS = 100          # предел таблицы на публичном сервере OSRM
SNAP_MAX_KM = 1.0         # OSRM привязывает точку к дороге; дальше — не наша точка
POLITE = Throttle(1.0)    # публичный сервер OSRM: без частых запросов

ROADS_NOTE = "расстояния до этой точки запрошены у OSRM по дорогам"


class RoadsUnavailable(RuntimeError):
    """Дорог до точки взять неоткуда. Текст — причина для человека."""


@dataclass(frozen=True)
class Legs:
    """Плечи до новой точки: to_new[i] — от точки i к новой, from_new[i] —
    от новой к точке i. Метры и секунды свободного потока, как в матрице."""
    to_new: tuple          # ((метры, секунды), ...)
    from_new: tuple


def ask_table(coords: list, timeout: float = TIMEOUT_S) -> dict:
    """Один запрос OSRM /table -> ответ целиком. Единственное место модуля,
    которое ходит в сеть; в тестах подменяется."""
    path = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
    req = urllib.request.Request(f"{OSRM_TABLE}{path}?annotations=distance,duration",
                                 headers={"User-Agent": USER_AGENT})
    POLITE.wait()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise RoadsUnavailable(f"OSRM ответил HTTP {exc.code}") from exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise RoadsUnavailable(f"OSRM недоступен: {type(exc).__name__}") from exc
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise RoadsUnavailable("OSRM прислал непонятный ответ") from exc
    if data.get("code") != "Ok":
        raise RoadsUnavailable(f"OSRM отказал: {data.get('code')}")
    return data


def legs_to(points: list, lat: float, lon: float,
            timeout: float = TIMEOUT_S) -> Legs:
    """Плечи между новой точкой и всеми точками региона или RoadsUnavailable."""
    if len(points) + 1 > MAX_POINTS:
        raise RoadsUnavailable(f"точек больше {MAX_POINTS}: публичный OSRM "
                               f"не считает такую таблицу одним запросом")
    coords = [(p["lat"], p["lon"]) for p in points] + [(lat, lon)]
    data = ask_table(coords, timeout)
    try:
        dist, dur = data["distances"], data["durations"]
        snapped_lon, snapped_lat = data["sources"][-1]["location"]
        n = len(points)
        to_new = tuple((int(round(dist[i][n])), int(round(dur[i][n])))
                       for i in range(n))
        from_new = tuple((int(round(dist[n][i])), int(round(dur[n][i])))
                         for i in range(n))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RoadsUnavailable("OSRM прислал неполную таблицу") from exc
    off = haversine_km(lat, lon, snapped_lat, snapped_lon)
    if off > SNAP_MAX_KM:
        raise RoadsUnavailable(f"ближайшая дорога в {off:.1f} км от точки")
    return Legs(to_new=to_new, from_new=from_new)


_MEMO: dict = {}
_MEMO_MAX = 64


def remembered_legs(region: str, key: str, points: list, lat: float, lon: float):
    """-> (Legs или None, причина отказа или ""). Один запрос на точку."""
    memo_key = (region, key, len(points), round(lat, 6), round(lon, 6))
    if memo_key not in _MEMO:
        if len(_MEMO) >= _MEMO_MAX:
            _MEMO.pop(next(iter(_MEMO)))
        try:
            _MEMO[memo_key] = (legs_to(points, lat, lon), "")
        except RoadsUnavailable as exc:
            _MEMO[memo_key] = (None, str(exc))
    return _MEMO[memo_key]
