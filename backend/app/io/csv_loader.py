# -*- coding: utf-8 -*-
"""Чтение выгрузок BK. Файлы в cp1251, разделитель «;».

Особенности формата, которые ломают наивный парсер:
  * адрес офиса лежит ОТДЕЛЬНОЙ строкой в самом низу файла, в первой колонке
    подпись «Адрес офиса», во второй — сам адрес;
  * набор колонок различается между регионами (в Востоке есть лишняя
    «Подключение» с FMC/FTTB) — поэтому читаем по ИМЕНАМ заголовков;
  * между таблицей и строкой офиса есть пустые строки;
  * у аварий Юго-востока окно 00:01-23:59 — это окно, а не длительность.
"""
from __future__ import annotations
import csv
import json
from datetime import datetime
from pathlib import Path

from ..config import DATA, REGIONS
from ..models import Job, Depot, RegionData
from ..geo.cluster import classify_cluster, clean_district, HOME_DEPOTS
from .normatives import classify

SYNTHETIC = "{region} Синтетические данные.csv"
CONTROL = {
    "Восток": "Восток Контрольное распределение..csv",
    "Юго-восток": "Юго-восток Контрольное распределение.csv",
    "Югоцентр": "Югоцентр Контрольное распределение..csv",
}
OFFICE_MARKER = "адрес офис"


def _read_rows(path: Path):
    for enc in ("cp1251", "utf-8-sig", "utf-8"):
        try:
            with open(path, encoding=enc, newline="") as f:
                rows = list(csv.reader(f, delimiter=";"))
            if rows and any("Заявка" in c for c in rows[0]):
                return rows
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"Не удалось прочитать {path.name}: неизвестная кодировка")


def _parse_dt(s: str) -> int:
    """'17.08.2026 20:00' -> минуты от полуночи. Терпит ' 0:01'."""
    s = (s or "").strip()
    if not s:
        raise ValueError("пустое время")
    part = s.split(" ", 1)[1] if " " in s else s
    t = datetime.strptime(part.strip(), "%H:%M")
    return t.hour * 60 + t.minute


def _load_geo():
    geo = json.loads((DATA / "geo" / "geocode.json").read_text(encoding="utf-8"))
    ov = json.loads((DATA / "geo" / "overrides.json").read_text(encoding="utf-8"))
    for k, v in ov.items():
        if k.startswith("_"):
            continue
        geo[k] = {**geo.get(k, {}), **v}
    return geo


def load_region(region: str, raw_dir: Path | None = None) -> RegionData:
    if region not in REGIONS:
        raise ValueError(f"Неизвестный регион: {region}")
    raw_dir = raw_dir or (DATA / "raw")
    rows = _read_rows(raw_dir / SYNTHETIC.format(region=region))
    header = [c.strip() for c in rows[0]]
    geo = _load_geo()

    office_addr = None
    records = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        if r[0].strip().lower().startswith(OFFICE_MARKER):
            office_addr = r[1].strip()
            continue
        rec = dict(zip(header, [c.strip() for c in r]))
        if not rec.get("Заявка"):
            continue
        records.append(rec)

    if not office_addr:
        raise ValueError(f"{region}: не найдена строка «Адрес офиса»")
    if office_addr not in geo:
        raise ValueError(f"{region}: адрес офиса отсутствует в геокэше: {office_addr}")

    og = geo[office_addr]
    depots = {"office": Depot(key="office", name=f"Офис «{region}»",
                              lat=og["lat"], lon=og["lon"],
                              cluster="Москва", is_office=True)}

    jobs, problems = [], []
    for rec in records:
        addr = rec.get("Адрес", "")
        g = geo.get(addr)
        if not g:
            problems.append((rec["Заявка"], "нет геокода", addr))
            continue
        bk, hd = rec.get("Тип заявки BK", ""), rec.get("Тип заявки HD", "")
        gig = (rec.get("Гигабитное подключение", "") or "").strip().lower() == "да"
        cls = classify(bk, hd, gig, rec["Заявка"])
        try:
            ws, we = _parse_dt(rec["Начало"]), _parse_dt(rec["Окончание"])
        except (ValueError, KeyError):
            problems.append((rec["Заявка"], "не разобрано окно", rec.get("Начало", "")))
            continue
        floating = (we - ws) > 10 * 60
        cluster = classify_cluster(addr, rec.get("Район", ""))
        jobs.append(Job(
            id=rec["Заявка"], address=addr,
            lat=g["lat"], lon=g["lon"], geo_level=int(g.get("geo_level", g.get("level", 0)) or 0),
            district=clean_district(rec.get("Район", "")),
            bk=bk, hd=hd, skill=cls["skill"], service_min=cls["service_min"],
            equipment=dict(cls["equipment"]), priority=cls["priority"],
            win_start=ws, win_end=we, floating_window=floating, cluster=cluster,
            # Требование транспорта берётся из характера работ; межгородские
            # расстояния Подмосковья добавляют его независимо от вида работ.
            requires_transport=(cls.get("requires_transport")
                                or ("car" if cluster != "Москва" else None)),
        ))

    for cl, d in HOME_DEPOTS.items():
        if any(j.cluster == cl for j in jobs):
            depots[cl] = Depot(key=cl, name=d["name"], lat=d["lat"], lon=d["lon"], cluster=cl)

    rd = RegionData(name=region, office_address=office_addr, depots=depots, jobs=jobs)
    rd.problems = problems
    return rd


def load_control(region: str, raw_dir: Path | None = None) -> dict:
    """Контрольное распределение. По указанию постановщика в расчётах НЕ участвует —
    используется только как ориентир и для честного сравнения.
    Ключевое: колонка «Статус BK» показывает качество контрольного дня."""
    raw_dir = raw_dir or (DATA / "raw")
    rows = _read_rows(raw_dir / CONTROL[region])
    header = [c.strip() for c in rows[0]]
    recs = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        if r[0].strip().lower().startswith(OFFICE_MARKER):
            continue
        rec = dict(zip(header, [c.strip() for c in r]))
        if rec.get("Заявка"):
            recs.append(rec)
    brigades = sorted({r["Бригада"] for r in recs if r.get("Бригада")})
    statuses = {}
    for r in recs:
        st = r.get("Статус BK", "").strip() or "—"
        statuses[st] = statuses.get(st, 0) + 1
    overdue = statuses.get("Просрочена", 0)
    cancelled = statuses.get("Отменена", 0)
    not_sent = statuses.get("Не отправлена", 0)
    return dict(region=region, rows=len(recs), brigades=brigades,
                n_brigades=len(brigades), statuses=statuses,
                overdue=overdue, cancelled=cancelled, not_sent=not_sent,
                live_jobs=len(recs) - cancelled,
                records=recs)
