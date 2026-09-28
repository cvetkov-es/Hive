# -*- coding: utf-8 -*-
"""Базовый вариант распределения — буквально по ТЗ 2.3.

«Заявки назначаются по порядку первому доступному исполнителю, удовлетворяющему
обязательным ограничениям.» Никакой глобальной оптимизации: заявка идёт первому
подходящему, в конец его маршрута, и обратно уже не забирается.

Зачем он нужен. Без точки отсчёта фраза «наш план экономит бригады и километры»
не проверяема. Базовый вариант даёт честное «столько же входных данных, столько
же ограничений, но без оптимизации» — и разница между ним и солвером и есть
вклад решения.

Честность сравнения держится на одном: ОБА плана проверяются одними и теми же
правилами. Допустимость — только через feasibility, расписание — только через
metrics.replay_route, границы дня — те же константы из config. Ослабить правила
для базового варианта означает нарисовать себе выигрыш.
"""
from __future__ import annotations

from ..models import Plan, Unassigned
from .feasibility import (Code, REASON_RU, candidates, check_equipment,
                          check_static, route_fits)
from .metrics import replay_route


def _reason(job, engineers, carried: dict) -> Unassigned:
    """Причина отказа в терминах диспетчера. Порядок разбора — от «никто и
    никогда» к «сегодня уже не помещается»: первое требует изменить состав
    парка, второе — добавить смену или подвинуть окно."""
    ok, rejected = candidates(job, engineers)
    if not ok:
        top = rejected[0][1] if rejected else None
        code = top.code if top is not None else Code.NO_ENGINEER   # Verdict ложен при ok=False
        return Unassigned(job_id=job.id, code=code,
                          reason=REASON_RU.get(code, REASON_RU[Code.NO_ENGINEER]),
                          detail=top.text if top is not None else "")
    blocked = [check_equipment(job, e, carried[e.id]) for e in ok]
    if all(not v.ok and v.code == Code.EQUIPMENT for v in blocked):
        return Unassigned(job_id=job.id, code=Code.EQUIPMENT,
                          reason=REASON_RU[Code.EQUIPMENT],
                          detail=blocked[0].text)
    return Unassigned(job_id=job.id, code=Code.TIME, reason=REASON_RU[Code.TIME],
                      detail=f"подходящих бригад {len(ok)}, но заявка не помещается "
                             f"в их маршруты по времени")


def solve_baseline(problem) -> Plan:
    """Порядок строк файла × порядок бригад во входных данных. Без возвратов."""
    engineers = problem.engineers
    seqs = {e.id: [] for e in engineers}
    carried = {e.id: {} for e in engineers}
    routes_by_eng = {e.id: replay_route(e, [], problem.travel, problem.rd.depots)
                     for e in engineers}
    unassigned = []

    for job in problem.rd.jobs:                    # порядок строк файла
        placed = False
        for eng in engineers:                      # порядок входных данных
            if not check_static(job, eng).ok:
                continue
            if not check_equipment(job, eng, carried[eng.id]).ok:
                continue
            trial = seqs[eng.id] + [job]           # только в конец маршрута
            route = replay_route(eng, trial, problem.travel, problem.rd.depots)
            if not route_fits(route, eng).ok:
                continue
            seqs[eng.id] = trial
            routes_by_eng[eng.id] = route
            for item, n in job.equipment.items():
                carried[eng.id][item] = carried[eng.id].get(item, 0) + n
            placed = True
            break
        if not placed:
            unassigned.append(_reason(job, engineers, carried))

    routes = [routes_by_eng[e.id] for e in engineers]
    return Plan(region=problem.rd.name, algo="baseline", routes=routes,
                unassigned=unassigned,
                meta=dict(fleet_size=len(engineers),
                          rule="ТЗ 2.3: первый подходящий исполнитель по порядку"))
