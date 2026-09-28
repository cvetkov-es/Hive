# -*- coding: utf-8 -*-
"""Офлайн-предрасчёт дорожных матриц. В рантайме НЕ вызывается.

Один запрос к OSRM /table на регион отдаёт матрицу расстояний и времени
целиком (до 90 точек). Результат коммитится, сервис работает без сети.

Точки записываются ЯВНЫМ списком с ключами, чтобы индекс матрицы нельзя было
перепутать с порядком строк в CSV.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import DATA, REGIONS                      # noqa: E402
from app.io.csv_loader import load_region                 # noqa: E402
from app.geo.travel import haversine_km                   # noqa: E402

OSRM = "https://router.project-osrm.org/table/v1/driving/"
UA = {"User-Agent": "LCT2026-hive/1.0 (+https://github.com/cvetkov-es/Hive)"}


def points_of(rd):
    pts = []
    for key, d in rd.depots.items():
        pts.append(dict(key=f"depot:{key}", kind="depot", id=key,
                        name=d.name, lat=d.lat, lon=d.lon))
    for j in rd.jobs:
        pts.append(dict(key=f"job:{j.id}", kind="job", id=j.id,
                        name=j.address, lat=j.lat, lon=j.lon))
    return pts


def osrm_table(pts, tries=3):
    coords = ";".join(f"{p['lon']:.6f},{p['lat']:.6f}" for p in pts)
    url = f"{OSRM}{coords}?annotations=distance,duration"
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read().decode())
            if d.get("code") == "Ok":
                return d
            last = d.get("code")
        except Exception as e:                              # noqa: BLE001
            last = e
        sys.stderr.write(f"  OSRM повтор {k + 1}/{tries}: {last}\n")
        time.sleep(5)
    raise RuntimeError(f"OSRM недоступен: {last}")


def verify(pts, resp):
    """OSRM возвращает привязанные к дороге координаты — проверяем, что они
    рядом с нашими. Это ловит рассинхрон порядка точек."""
    bad = []
    for i, src in enumerate(resp.get("sources", [])):
        lon, lat = src["location"]
        d = haversine_km(pts[i]["lat"], pts[i]["lon"], lat, lon)
        if d > 1.0:
            bad.append((i, pts[i]["name"][:50], round(d, 2)))
    return bad


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Дорожные матрицы OSRM по регионам.")
    # Один регион — когда поправлены координаты только в нём. Пересчёт всех
    # трёх заодно подтянул бы свежие данные OSRM и в чужие регионы, и их
    # эталонные планы разошлись бы с матрицей.
    ap.add_argument("--region", default=None, help="только один регион")
    args = ap.parse_args()
    out_dir = DATA / "matrices"
    out_dir.mkdir(parents=True, exist_ok=True)
    for region in ([args.region] if args.region else REGIONS):
        rd = load_region(region)
        pts = points_of(rd)
        print(f"{region}: {len(pts)} точек ({len(rd.depots)} депо + {len(rd.jobs)} заявок)")
        resp = osrm_table(pts)
        bad = verify(pts, resp)
        if bad:
            print(f"  ВНИМАНИЕ: {len(bad)} точек привязались далеко от исходных:")
            for i, nm, d in bad[:5]:
                print(f"    [{i}] {nm} — {d} км")
        payload = dict(region=region, points=pts,
                       distances=resp["distances"], durations=resp["durations"],
                       source="OSRM router.project-osrm.org, профиль driving",
                       note="distances в метрах, durations в секундах (свободный поток)")
        (out_dir / f"{region}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        n = len(pts)
        print(f"  сохранено {n}x{n}, снято {len(bad)} замечаний")
        time.sleep(2)


if __name__ == "__main__":
    main()
