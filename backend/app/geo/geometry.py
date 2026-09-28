# -*- coding: utf-8 -*-
"""Форма маршрутов по дорогам.

Дорожная матрица знает длину плеча, но не его форму. Прямая линия между двумя
адресами показывает на карте то, чего не было: бригада не летит через дома.
Форму берём у того же OSRM, что считал матрицу. Для планов, посчитанных
заранее, — тоже заранее: tools/build_geometry.py складывает её в
data/geo/routes.json.

Плечо, которого в кэше нет, появляется после «Пересчитать», события, урезанного
парка или ручного хода. Форма для него берётся живым запросом (live=True): один
запрос /route на маршрут бригады со всеми её точками, ответ запоминается. Сеть
здесь — улучшение, а не условие: не ответил OSRM — плечо отдаётся прямой с
пометкой estimated, и интерфейс рисует его пунктиром. Честнее показать, что
форма неизвестна, чем выдать прямую за дорогу.

Живой запрос идёт отдельным вызовом: сохранённая форма нужна карте сразу, а
недостающая может ехать секунды — по запросу в секунду, как просит публичный
сервер.
"""
from __future__ import annotations
import http.client
import json
import threading
import urllib.error
import urllib.request

from ..config import DATA

_CACHE: dict | None = None
PATH = DATA / "geo" / "routes.json"


def _legs() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(PATH.read_text(encoding="utf-8")) if PATH.is_file() else {}
    return _CACHE


# Кэш ключуется номерами точек, а не координатами. Если координаты точки потом
# поправили (так было с «Талалихина, 16», стоявшей в Щербинке), старая форма
# дороги осталась бы в кэше под тем же ключом и рисовала маршрут в чужой город.
# Поэтому форма принимается, только если её концы рядом с нужными точками.
# OSRM привязывает точку к дороге, отсюда допуск, а не равенство.
ENDPOINT_TOLERANCE_KM = 1.0


def _km(a, b) -> float:
    import math
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(h))


def _fits(path, a_pt, b_pt) -> bool:
    if a_pt is None or b_pt is None:
        return True
    return (_km(path[0], a_pt) <= ENDPOINT_TOLERANCE_KM
            and _km(path[-1], b_pt) <= ENDPOINT_TOLERANCE_KM)


def leg_path(a_key: str, b_key: str, a_pt=None, b_pt=None):
    """-> список [lat, lon] по дорогам либо None, если плеча нет ни в кэше, ни
    среди полученных живьём, или форма ведёт не туда (концы дальше допуска от
    a_pt и b_pt)."""
    key = f"{a_key}>{b_key}"
    for path in (_legs().get(key), _LIVE.get(key)):
        if path and _fits(path, a_pt, b_pt):
            return path
    return None


# --- живой запрос формы -------------------------------------------------------

OSRM_ROUTE = "https://router.project-osrm.org/route/v1/driving/"
LIVE_TIMEOUT_S = 8.0
# Запросов на один вызов: в регионе не больше 15 бригад, один запрос на бригаду.
LIVE_MAX_ROUTES = 16
MIN_STEP_M = 25            # прореживание, как в tools/build_geometry.py

_LIVE: dict = {}           # "a>b" -> [[lat, lon], ...], память процесса
_LIVE_MAX = 5000
_LIVE_LOCK = threading.Lock()


class ShapeUnavailable(RuntimeError):
    """Формы дороги взять неоткуда. Текст — причина для человека."""


def _decimate(path: list) -> list:
    from .travel import haversine_km
    if len(path) <= 2:
        return path
    out = [path[0]]
    for pt in path[1:-1]:
        if haversine_km(out[-1][0], out[-1][1], pt[0], pt[1]) * 1000 >= MIN_STEP_M:
            out.append(pt)
    out.append(path[-1])
    return out


def ask_route(points: list, timeout: float = LIVE_TIMEOUT_S) -> list:
    """Один запрос OSRM /route по точкам маршрута -> форма каждого участка
    (список списков [lat, lon]). Единственное место модуля, которое ходит в
    сеть; в тестах подменяется."""
    from .geocode import USER_AGENT
    from .roads import POLITE
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    req = urllib.request.Request(
        f"{OSRM_ROUTE}{coords}?overview=false&geometries=geojson&steps=true",
        headers={"User-Agent": USER_AGENT})
    POLITE.wait()      # общая с roads.py пауза: сервер тот же
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ShapeUnavailable(f"OSRM ответил HTTP {exc.code}") from exc
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise ShapeUnavailable(f"OSRM недоступен: {type(exc).__name__}") from exc
    if data.get("code") != "Ok":
        raise ShapeUnavailable(f"OSRM отказал: {data.get('code')}")
    try:
        legs = []
        for leg in data["routes"][0]["legs"]:
            path = []
            for step in leg["steps"]:
                for lon, lat in step["geometry"]["coordinates"]:
                    pt = [round(lat, 5), round(lon, 5)]
                    if not path or path[-1] != pt:
                        path.append(pt)
            legs.append(_decimate(path))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ShapeUnavailable("OSRM прислал неполный маршрут") from exc
    if len(legs) != len(points) - 1:
        raise ShapeUnavailable("OSRM прислал не столько участков, сколько просили")
    return legs


def _fetch_missing(routes: list) -> str:
    """Добирает у OSRM форму участков, которых нет в памяти. routes — список
    (ключи точек, координаты точек) по маршрутам. -> причина отказа или "".
    Первый же отказ сети останавливает остальные запросы: без сети каждый ждал
    бы тайм-аута."""
    asked = 0
    with _LIVE_LOCK:     # параллельный вызов дождётся и возьмёт из памяти
        for keys, pts in routes:
            need = [i for i in range(len(keys) - 1)
                    if leg_path(keys[i], keys[i + 1], pts[i], pts[i + 1]) is None]
            if not need:
                continue
            if asked >= LIVE_MAX_ROUTES:
                break
            asked += 1
            try:
                shapes = ask_route(pts)
            except ShapeUnavailable as exc:
                return str(exc)
            for i in need:
                path = shapes[i]
                if path and _fits(path, pts[i], pts[i + 1]):
                    if len(_LIVE) >= _LIVE_MAX:
                        _LIVE.pop(next(iter(_LIVE)))
                    _LIVE[f"{keys[i]}>{keys[i + 1]}"] = path
    return ""


def plan_geometry(plan, problem, live: bool = False) -> dict:
    """Геометрия всех маршрутов плана. Отдаётся отдельным запросом, а не внутри
    плана: полилинии весят сотни килобайт, а нужны только карте. С live=True
    недостающая форма добирается у OSRM."""
    jobs = {j.id: j for j in problem.rd.jobs}
    engs = {e.id: e for e in problem.engineers}
    depots = problem.rd.depots

    routes = []                      # (engineer_id, ключи, точки, остановки)
    for r in plan.routes:
        if not r.stops:
            continue
        eng = engs[r.engineer_id]
        keys = [f"depot:{eng.depot}"]
        pts = [[depots[eng.depot].lat, depots[eng.depot].lon]]
        stops = []
        for s in r.stops:
            job = jobs.get(s.job_id)
            if job is None:
                continue
            keys.append(f"job:{job.id}")
            pts.append([job.lat, job.lon])
            stops.append(s)
        routes.append((r.engineer_id, keys, pts, stops))

    why = _fetch_missing([(k, p) for _, k, p, _ in routes]) if live else ""

    out, known, guessed = [], 0, 0
    for engineer_id, keys, pts, stops in routes:
        legs = []
        for i, s in enumerate(stops):
            path = leg_path(keys[i], keys[i + 1], pts[i], pts[i + 1])
            estimated = not path
            if path:
                known += 1
            else:
                path, guessed = [pts[i], pts[i + 1]], guessed + 1
            legs.append(dict(job_id=s.job_id, seq=s.seq, path=path,
                             estimated=estimated))
        out.append(dict(engineer_id=engineer_id, legs=legs))

    note = ""
    if guessed:
        note = ("часть участков маршрута показана прямой линией: формы дороги "
                "для них нет в сохранённых картах")
        if why:
            note += f", а картографический сервис не ответил ({why})"
    return dict(routes=out, known_legs=known, estimated_legs=guessed, note=note)
