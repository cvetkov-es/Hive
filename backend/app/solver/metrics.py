# -*- coding: utf-8 -*-
"""Пересчёт маршрута и метрики плана.

ОДНА функция replay_route() считает прибытие / ожидание / начало / окончание /
опоздание, и её используют все: постобработка солвера, baseline, объяснялка и
независимый аудитор. Читать эти числа из CumulVar нельзя: при transit =
service(from) + travel(from,to) переменная CumulVar означает НАЧАЛО РАБОТ, а не
прибытие, а ожидание сидит в slack предыдущего узла. ТЗ требует показать и
прибытие, и начало — если вывести одно из другого неверно, в интерфейсе поедут
числа, и заметит это уже диспетчер.
"""
from __future__ import annotations

from ..models import Route, Stop


def replay_route(eng, job_seq, travel, depots, start_key=None,
                 earliest=None, finish_by=None) -> Route:
    """Прогон маршрута вперёд по времени. job_seq — список Job в порядке визитов.

    Выезд из депо рассчитывается так, чтобы приехать к открытию окна первой заявки,
    а не «как можно раньше». Иначе бригада числится выехавшей в начало смены и часами
    стоит у офиса: длительность рабочего дня раздувается, ограничение на неё начинает
    срабатывать вхолостую, а в интерфейсе появляется ожидание, которого нет.

    `start_key` и `earliest` нужны перепланированию: после события бригада стоит
    не в депо, а в точке последней выполненной заявки, и раньше момента события
    тронуться не может. По умолчанию — депо и начало смены, то есть обычный день.

    `finish_by` — крайний срок окончания дня. Тем же числом ограничен солвер, и
    разойтись они не имеют права: обратный проход двигает выезд «как можно
    позже», и без этой границы он честно уложился бы в смену, но вылез за
    двенадцатичасовой норматив, отсчитываемый от утреннего выезда.
    """
    cur_key = start_key or f"depot:{depots[eng.depot].key}"
    floor = eng.shift_start if earliest is None else earliest
    stops = []
    if job_seq:
        first_leg = travel.minutes(cur_key, f"job:{job_seq[0].id}", eng.transport)
        # Обратный проход: самое позднее начало каждой работы, при котором маршрут
        # ещё выполним. Отсюда самый поздний допустимый выезд — и, значит,
        # минимальная длительность рабочего дня. Без этого бригада числится
        # выехавшей с рассветом, день раздувается, а ограничение на его длину
        # начинает срабатывать на простое у депо.
        n = len(job_seq)
        latest = [0] * n
        last = job_seq[-1]
        ceiling = eng.shift_end if finish_by is None else min(eng.shift_end, finish_by)
        latest[-1] = min(last.win_end, ceiling - last.service_min)
        for i in range(n - 2, -1, -1):
            leg = travel.minutes(f"job:{job_seq[i].id}", f"job:{job_seq[i + 1].id}",
                                 eng.transport)
            latest[i] = min(job_seq[i].win_end,
                            latest[i + 1] - leg - job_seq[i].service_min)
        t = max(floor, latest[0] - first_leg)
    else:
        t = floor
    depart = t
    for seq, job in enumerate(job_seq, 1):
        jk = f"job:{job.id}"
        leg_min = travel.minutes(cur_key, jk, eng.transport)
        leg_km = travel.km(cur_key, jk)
        arrive = t + leg_min
        start = max(arrive, job.win_start)
        wait = start - arrive
        late = max(0, start - job.win_end)
        end = start + job.service_min
        stops.append(Stop(job_id=job.id, seq=seq, arrive_min=arrive, start_min=start,
                          end_min=end, wait_min=wait, late_min=late,
                          leg_km=round(leg_km, 3), leg_min=leg_min))
        t = end
        cur_key = jk
    return Route(engineer_id=eng.id, stops=stops, depart_min=depart)


def route_violations(eng, route, jobs_by_id) -> list:
    """Независимая проверка готового маршрута. Используется аудитором."""
    bad = []
    carried = {}
    for s in route.stops:
        j = jobs_by_id[s.job_id]
        if j.skill not in eng.skills:
            bad.append(f"{j.id}: нет навыка {j.skill}")
        if j.cluster != eng.cluster:
            bad.append(f"{j.id}: чужая зона {j.cluster}")
        if j.requires_transport and eng.transport != j.requires_transport:
            bad.append(f"{j.id}: нужен транспорт {j.requires_transport}")
        if s.start_min < j.win_start:
            bad.append(f"{j.id}: старт {s.start_min} раньше окна {j.win_start}")
        for k, v in j.equipment.items():
            carried[k] = carried.get(k, 0) + v
    for k, v in carried.items():
        if v > eng.equipment.get(k, 0):
            bad.append(f"{eng.id}: перебор «{k}»: нужно {v}, запас {eng.equipment.get(k, 0)}")
    if route.stops:
        if route.depart_min < eng.shift_start:
            bad.append(f"{eng.id}: выезд {route.depart_min} раньше начала смены {eng.shift_start}")
        if route.stops[-1].end_min > eng.shift_end:
            bad.append(f"{eng.id}: работа заканчивается в {route.stops[-1].end_min} "
                       f"после смены {eng.shift_end}")
    return bad


def summarize(plan, region_data) -> dict:
    """Обязательные метрики ТЗ плюс полезные для диспетчера."""
    jobs = {j.id: j for j in region_data.jobs}
    per_engineer = []
    for r in plan.routes:
        if not r.stops:
            continue
        per_engineer.append(dict(
            engineer_id=r.engineer_id, jobs=r.n_jobs, km=r.km,
            travel_min=sum(s.leg_min for s in r.stops),
            work_min=sum(jobs[s.job_id].service_min for s in r.stops),
            wait_min=sum(s.wait_min for s in r.stops),
            late_min=sum(s.late_min for s in r.stops),
            start=r.stops[0].arrive_min, end=r.stops[-1].end_min,
        ))
    return dict(
        region=plan.region, algo=plan.algo,
        used_engineers=plan.used_engineers,          # обязательная метрика №1
        total_km=plan.total_km,                      # обязательная метрика №2
        assigned=plan.assigned, total_jobs=len(region_data.jobs),
        unassigned=len(plan.unassigned),
        late_jobs=plan.late_jobs,
        travel_min=plan.travel_min,
        per_engineer=sorted(per_engineer, key=lambda x: -x["jobs"]),
    )
