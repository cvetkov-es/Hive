# -*- coding: utf-8 -*-
"""Выгрузка результата по ТЗ 2.4.2.

Четыре обязательных разреза, все четыре в одном файле:

  * по исполнителю — упорядоченный список заявок, плановое время прибытия и
    начала работ, пробег по маршруту;
  * по заявке — назначенный исполнитель либо статус «не назначена»;
  * для неназначенной — явная причина понятным диспетчеру языком;
  * по плану — количество задействованных исполнителей, пробег по каждому и
    суммарный.

Разрез по заявкам — не дубль разреза по маршрутам. В маршрутах нет тех, кого
никуда не поставили, а именно они и есть работа диспетчера на ближайший час.

Рабочий день диспетчера заканчивается выгрузкой, а не любованием картой:
постановщик сказал, что инженеру «достаточно получать маршрут вне системы».
Поэтому CSV открывается в Excel как есть — разделитель «;», BOM для кириллицы.
"""
from __future__ import annotations
import csv
import io

from ..config import SKILL_RU, TRANSPORT_RU
from ..models import hhmm

SEP = ";"
STATUS_ASSIGNED = "назначена"
STATUS_UNASSIGNED = "не назначена"

CSV_FIELDS = ["engineer_id", "engineer_name", "transport", "seq", "job_id",
              "address", "skill", "window", "arrive", "start", "end",
              "duration_min", "travel_min", "km", "status", "reason"]


def plan_to_json(plan, problem) -> dict:
    jobs = {j.id: j for j in problem.rd.jobs}
    engs = {e.id: e for e in problem.engineers}

    routes = []
    for r in plan.routes:
        if not r.stops:
            continue
        e = engs[r.engineer_id]
        routes.append(dict(
            engineer_id=r.engineer_id, engineer_name=e.name,
            transport=TRANSPORT_RU[e.transport], depart=hhmm(r.depart_min),
            km=r.km, jobs=r.n_jobs,
            travel_min=sum(s.leg_min for s in r.stops),
            stops=[dict(job_id=s.job_id, seq=s.seq,
                        address=jobs[s.job_id].address,
                        window=jobs[s.job_id].window_str,
                        arrive=hhmm(s.arrive_min), start=hhmm(s.start_min),
                        end=hhmm(s.end_min), wait_min=s.wait_min,
                        late_min=s.late_min, km=s.leg_km, travel_min=s.leg_min)
                   for s in r.stops]))

    owner = {s.job_id: (r.engineer_id, s) for r in plan.routes for s in r.stops}
    reasons = {u.job_id: u for u in plan.unassigned}
    rows = []
    for j in problem.rd.jobs:
        if j.id in owner:
            eng_id, s = owner[j.id]
            rows.append(dict(job_id=j.id, status=STATUS_ASSIGNED,
                             engineer_id=eng_id, arrive=hhmm(s.arrive_min),
                             start=hhmm(s.start_min), reason=""))
        else:
            u = reasons.get(j.id)
            rows.append(dict(job_id=j.id, status=STATUS_UNASSIGNED,
                             engineer_id=None, arrive=None, start=None,
                             reason=(u.reason if u else "причина не определена"),
                             detail=(u.detail if u else "")))

    return dict(
        region=plan.region, algo=plan.algo,
        used_engineers=plan.used_engineers,          # обязательная метрика №1
        total_km=plan.total_km,                      # обязательная метрика №2
        assigned=plan.assigned, total_jobs=len(problem.rd.jobs),
        late_jobs=plan.late_jobs, travel_min=plan.travel_min,
        routes=routes, jobs=rows,
        unassigned=[dict(job_id=u.job_id, code=u.code, reason=u.reason,
                         detail=u.detail) for u in plan.unassigned],
        meta=dict(plan.meta))


def plan_to_csv(plan, problem) -> str:
    jobs = {j.id: j for j in problem.rd.jobs}
    engs = {e.id: e for e in problem.engineers}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=SEP, lineterminator="\n")
    w.writerow(CSV_FIELDS)

    for r in plan.routes:
        if not r.stops:
            continue
        e = engs[r.engineer_id]
        for s in r.stops:
            j = jobs[s.job_id]
            w.writerow([r.engineer_id, e.name, TRANSPORT_RU[e.transport], s.seq,
                        j.id, j.address, SKILL_RU[j.skill], j.window_str,
                        hhmm(s.arrive_min), hhmm(s.start_min), hhmm(s.end_min),
                        j.service_min, s.leg_min, s.leg_km,
                        STATUS_ASSIGNED, ""])

    for u in plan.unassigned:
        j = jobs.get(u.job_id)
        w.writerow(["", "", "", "", u.job_id, j.address if j else "",
                    SKILL_RU[j.skill] if j else "", j.window_str if j else "",
                    "", "", "", j.service_min if j else "", "", "",
                    STATUS_UNASSIGNED, f"{u.reason}. {u.detail}".strip(". ")])

    # Сводка комментариями: строки данных остаются машиночитаемыми, а человек,
    # открывший файл, сразу видит обе обязательные метрики.
    w.writerow([])
    lines = [f"# регион: {plan.region}, план: {plan.algo}",
             f"# задействовано исполнителей: {plan.used_engineers}",
             f"# суммарный пробег, км: {plan.total_km}",
             f"# распределено заявок: {plan.assigned} из {len(problem.rd.jobs)}",
             f"# не распределено: {len(plan.unassigned)}",
             f"# опозданий: {plan.late_jobs}"]
    for r in sorted(plan.routes, key=lambda x: -x.km):
        if r.stops:
            lines.append(f"# пробег {r.engineer_id}: {r.km} км, "
                         f"заявок {r.n_jobs}")
    return buf.getvalue() + "\n".join(lines) + "\n"


def plan_to_csv_bytes(plan, problem) -> bytes:
    """BOM обязателен: без него Excel открывает кириллицу кракозябрами, и
    выгрузка, ради которой всё делалось, оказывается нечитаемой."""
    return ("﻿" + plan_to_csv(plan, problem)).encode("utf-8")
