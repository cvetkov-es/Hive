#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Геометрия маршрутов по дорогам. Офлайн, в рантайме НЕ вызывается.

    python tools/build_geometry.py            # плечи эталонных планов
    python tools/build_geometry.py --all      # плюс базовые варианты

Дорожная матрица даёт длину плеча, но не его форму. Рисовать маршруты прямыми
линиями между точками значит показывать на карте то, чего не было: бригада не
летит через дома, она едет по улицам, и это видно.

Берём форму у того же OSRM, что считал матрицу, и кладём рядом в
data/geo/routes.json. Один запрос на МАРШРУТ, а не на плечо: /route принимает
все точки сразу и возвращает участки по отдельности, поэтому три региона
обходятся тремя десятками запросов вместо двух сотен.

Кэш накопительный и адресуется парой точек, а не номером маршрута: после
перепланирования плечи меняются, и уже известные переиспользуются. Плечо,
которого в кэше нет, интерфейс рисует прямой линией и помечает — это честнее,
чем ходить в сеть во время демонстрации.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import DATA, REGIONS                          # noqa: E402
from app.solver.engine import build_problem                   # noqa: E402

OSRM = "https://router.project-osrm.org/route/v1/driving/"
UA = {"User-Agent": "LCT2026-hive/1.0 (+https://github.com/cvetkov-es/Hive)"}
OUT = DATA / "geo" / "routes.json"
ARTIFACTS = ROOT / "artifacts"
PAUSE_S = 1.1                  # публичный OSRM просит не частить
MIN_STEP_M = 25                # прореживание: точки ближе не влияют на вид


def leg_key(a: str, b: str) -> str:
    return f"{a}>{b}"


def _ends_match(path, a, b, tol_km: float = 1.0) -> bool:
    from app.geo.geometry import _km
    return bool(path) and _km(path[0], a) <= tol_km and _km(path[-1], b) <= tol_km


def _decimate(coords: list) -> list:
    """Прореживание по расстоянию. Полилиния на 97 точек и на 30 выглядит
    одинаково при любом масштабе карты, а весит втрое меньше."""
    from app.geo.travel import haversine_km
    if len(coords) <= 2:
        return coords
    out = [coords[0]]
    for lat, lon in coords[1:-1]:
        if haversine_km(out[-1][0], out[-1][1], lat, lon) * 1000 >= MIN_STEP_M:
            out.append([lat, lon])
    out.append(coords[-1])
    return out


def osrm_route(points: list, tries: int = 3):
    """-> список участков, каждый — список [lat, lon]."""
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    url = (f"{OSRM}{coords}?overview=false&geometries=geojson&steps=true"
           f"&annotations=false")
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=60) as fh:
                data = json.load(fh)
            if data.get("code") != "Ok":
                raise ValueError(data.get("message", data.get("code")))
            legs = []
            for leg in data["routes"][0]["legs"]:
                path = []
                for step in leg["steps"]:
                    for lon, lat in step["geometry"]["coordinates"]:
                        pt = [round(lat, 5), round(lon, 5)]
                        if not path or path[-1] != pt:
                            path.append(pt)
                legs.append(_decimate(path))
            return legs
        except (urllib.error.URLError, ValueError, KeyError, TimeoutError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"OSRM не ответил: {last}")


def collect_routes(region: str, with_baseline: bool) -> list:
    """-> список маршрутов, каждый — список ключей точек от депо до последней."""
    problem = build_problem(region)
    engs = {e.id: e for e in problem.engineers}
    names = ["plan"] + (["baseline"] if with_baseline else [])
    out = []
    for name in names:
        path = ARTIFACTS / f"{name}_{region}.json"
        if not path.is_file():
            print(f"  нет {path.name}, пропускаю")
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        for r in d.get("routes", []):
            eng = engs.get(r["engineer_id"])
            if eng is None or not r.get("stops"):
                continue
            keys = [f"depot:{eng.depot}"] + [f"job:{s['job_id']}"
                                             for s in r["stops"]]
            out.append(keys)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="включая плечи базового варианта")
    ap.add_argument("--region", default=None)
    args = ap.parse_args()

    cache = json.loads(OUT.read_text(encoding="utf-8")) if OUT.is_file() else {}
    before = len(cache)
    regions = [args.region] if args.region else list(REGIONS)

    for region in regions:
        problem = build_problem(region)
        coord = {f"depot:{k}": (d.lat, d.lon)
                 for k, d in problem.rd.depots.items()}
        coord.update({f"job:{j.id}": (j.lat, j.lon) for j in problem.rd.jobs})

        routes = collect_routes(region, args.all)
        print(f"{region}: маршрутов {len(routes)}")
        for n, keys in enumerate(routes, 1):
            # Плечо в кэше, но его концы не у нынешних координат — координаты
            # точки поправили после того, как форма была снята. Снимаем заново.
            need = [i for i in range(len(keys) - 1)
                    if leg_key(keys[i], keys[i + 1]) not in cache
                    or not _ends_match(cache[leg_key(keys[i], keys[i + 1])],
                                       coord[keys[i]], coord[keys[i + 1]])]
            if not need:
                continue
            try:
                legs = osrm_route([coord[k] for k in keys])
            except (RuntimeError, KeyError) as exc:
                print(f"  маршрут {n}: {exc}")
                continue
            if len(legs) != len(keys) - 1:
                print(f"  маршрут {n}: OSRM вернул {len(legs)} участков "
                      f"вместо {len(keys) - 1}, пропускаю")
                continue
            for i, path in enumerate(legs):
                cache[leg_key(keys[i], keys[i + 1])] = path
            print(f"  маршрут {n}/{len(routes)}: участков {len(legs)}")
            time.sleep(PAUSE_S)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"\nПлечей в кэше: {before} -> {len(cache)}; {OUT} ({size_kb:.0f} КБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
