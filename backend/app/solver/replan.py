# -*- coding: utf-8 -*-
"""Перепланирование после события (ТЗ 2.1.6, Р4).

Три события: новая заявка, отмена заявки, недоступность исполнителя.

Всё держится на одном свойстве: ПРОШЛОЕ НЕ ПЕРЕПИСЫВАЕТСЯ. Пересчитать день
целиком умеет обычный вызов солвера, но тогда бригада, которая к 15:40 уже
отработала пять заявок, окажется в новом плане утром в другом районе. Такой
«план» не просто бесполезен — он врёт о том, что уже произошло.

Поэтому событие делит день на две части:

  * заморожено всё, к чему бригада УЖЕ ПРИСТУПИЛА (начало работ не позже
    момента события), и визит, к которому она УЖЕ ЕДЕТ. Работу у клиента не
    отбирают на полпути, а бригаду в дороге не разворачивают: где именно она
    сейчас, модель не знает, зато знает, где она окажется;
  * остальное планируется заново: бригада трогается не раньше момента события
    и не из депо, а из точки последнего замороженного визита, с тем остатком
    рабочего дня, который у неё есть.

Какие визиты заморожены, план помнит сам: plan.meta["frozen"]. По этой пометке
независимый аудитор проверяет, что прошлое не тронуто (A11), а ручной перенос
отказывается трогать начатое.

Новая заявка встаёт в план одним из двух способов. Куратор, 22.09: «Если новая
обычная заявка помещается в свободный интервал инженера между уже
запланированными работами, она может быть добавлена в расписание. При этом
такая заявка не должна полностью перестраивать уже сформированный план. Для
аварии действует другая логика: аварийная заявка может привести к
перепланированию оставшейся части рабочего дня.»

  * обычная — ВСТАВКА (mode="insert"): все назначения и их порядок остаются,
    заявка занимает самое дешёвое допустимое место; места нет — остаётся
    неназначенной с причиной;
  * авария — навык «Аварийные работы» или приоритет «Срочная» — ПЕРЕПЛАНИРОВАНИЕ
    остатка дня солвером (mode="replan").

Отмена и выбытие исполнителя — всегда перепланирование.

Второе свойство — читаемость разницы. Штраф за отход от уже озвученного
клиенту назначения (W.STABILITY_PENALTY) заметно больше типичного плеча, иначе
после отмены одной заявки по региону поедет половина назначений, и диспетчер
новому плану просто не поверит.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from ..config import MAX_WORKDAY_MIN, PRIO_EMERGENCY
from ..models import Plan, Route, Stop, Unassigned, hhmm
from .engine import Problem, solve
from .feasibility import (Code, REASON_RU, Verdict, check_equipment, check_static,
                          route_fits)
from .metrics import replay_route

KINDS = ("urgent", "cancel", "unavailable")
KIND_RU = {"urgent": "срочная заявка", "cancel": "отмена заявки",
           "unavailable": "исполнитель выбыл"}

MODES = ("insert", "replan")
MODE_RU = {"insert": "вставка в свободный интервал",
           "replan": "перепланирование остатка дня"}

# Что значит пометка у замороженного визита.
STARTED, EN_ROUTE = "started", "en_route"

# Кто из отказавших ближе всех к «взял бы»: причина неназначения называет
# самый близкий промах, а не первый попавшийся. Близость — сколько проверок
# бригада уже прошла; проверки идут в порядке навык -> транспорт -> зона ->
# смена -> оборудование -> время, и отказ по зоне значит, что навык и
# транспорт у бригады есть. «Нет навыка» верно, но бесполезно — таких бригад
# в регионе половина.
MISS_ORDER = {Code.TIME: 0, Code.EQUIPMENT: 1, Code.SHIFT_WINDOW: 2,
              Code.CLUSTER: 3, Code.TRANSPORT: 4, Code.SKILL: 5}


def is_emergency(job) -> bool:
    """Авария — по навыку или по приоритету «Срочная» (ТЗ 2.4.1). Приоритет
    при чтении уже сведён к внутреннему уровню: «Срочная» = уровень аварии."""
    return job is not None and (job.skill == "emergency"
                                or job.priority == PRIO_EMERGENCY)


@dataclass
class Event:
    kind: str                      # urgent | cancel | unavailable
    at_min: int                    # момент события, минуты от полуночи
    job: object = None             # для urgent: полная заявка
    job_id: str | None = None      # для cancel
    engineer_id: str | None = None # для unavailable

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"неизвестное событие «{self.kind}», есть: {KINDS}")

    @property
    def mode(self) -> str:
        """Вставка — только для обычной новой заявки. Всё остальное вправе
        перестроить остаток дня."""
        if self.kind == "urgent" and self.job is not None and not is_emergency(self.job):
            return "insert"
        return "replan"

    @property
    def title(self) -> str:
        what = {"urgent": getattr(self.job, "id", ""), "cancel": self.job_id,
                "unavailable": self.engineer_id}[self.kind]
        noun = "новая заявка" if self.mode == "insert" else KIND_RU[self.kind]
        return f"{hhmm(self.at_min)} · {noun} {what} · {MODE_RU[self.mode]}"


@dataclass
class Diff:
    """Что именно изменилось. Без этого «новый план» — просто другая картинка."""
    event: str
    frozen: int = 0
    mode: str = "replan"                              # insert | replan
    en_route: int = 0                                 # из замороженных — «уже в пути»
    moved: list = field(default_factory=list)        # сменился исполнитель
    resequenced: list = field(default_factory=list)  # тот же, другой порядок
    dropped: list = field(default_factory=list)      # вытеснено, с причиной
    added: list = field(default_factory=list)        # появилось в плане
    rejected: list = field(default_factory=list)     # новая заявка не принята
    cancelled: list = field(default_factory=list)    # отменена клиентом
    metrics: dict = field(default_factory=dict)

    @property
    def is_quiet(self) -> bool:
        """Изменение порядка объезда — тоже изменение: у бригады сдвигаются
        времена прибытия, и клиентам звонят заново. И отказ принять новую
        заявку — изменение: «план не изменился» рядом с непринятой аварией
        читается как «система промолчала». И отмена: заявка ушла из плана,
        даже если больше ничего не сдвинулось."""
        return not (self.moved or self.dropped or self.added or self.resequenced
                    or self.rejected or self.cancelled)


# --- задача с учётом события -------------------------------------------------

def problem_with_event(problem, event: Event) -> Problem:
    """Та же задача, но со срочной заявкой в наборе. Исходная не меняется:
    её держит кэш, и порча общего объекта проявилась бы через три вызова
    в четвёртом месте."""
    if event.kind != "urgent" or event.job is None:
        return problem
    job = event.job
    if any(j.id == job.id for j in problem.rd.jobs):
        raise ValueError(f"{job.id}: заявка с таким номером уже есть в регионе")

    key = f"job:{job.id}"
    travel = problem.travel
    if key not in travel.index:
        # Адреса нет в офлайновой матрице — дороги до него спрашиваем у OSRM.
        # Нет ответа — достраиваем по прямой с явной пометкой оценки: это
        # штатная деградация, а не аварийный случай.
        from ..geo.roads import remembered_legs
        legs, _ = remembered_legs(problem.rd.name, key, travel.points,
                                  job.lat, job.lon)
        travel = travel.with_point(key, job.lat, job.lon, legs=legs)

    rd = _shallow_region(problem.rd, list(problem.rd.jobs) + [job])
    return Problem(rd, problem.engineers, travel)


def _shallow_region(rd, jobs):
    from ..models import RegionData
    out = RegionData(name=rd.name, office_address=rd.office_address,
                     depots=rd.depots, jobs=jobs, engineers=rd.engineers)
    return out


# --- заморозка ---------------------------------------------------------------

def freeze(plan, problem, at_min: int, blocked=(), release=()) -> dict:
    """-> {engineer_id: {stops, marks, last_key, free_at, end_by, used}}

    Заморожено всё, к чему бригада приступила до момента события. Граница по
    НАЧАЛУ работ, а не по окончанию: заявку, которую делают прямо сейчас,
    отбирать нельзя — исполнитель уже у клиента.

    Заморожен и визит, к которому бригада уже едет и который не отложить:
    выехав заново в момент события, к началу работ она бы не успела. Такую
    бригаду не разворачивают — где именно она на дороге, модель не знает, а
    «выехать заново в 15:40» значило бы переписать то, что уже случилось.
    Бригада, которая приехала бы рано и ждала у двери, не заморожена: к своему
    клиенту она успевает и после события, а до того её можно направить на
    аварию. Замерено на Юго-востоке: единственный в Домодедово аварийщик в
    15:39 едет две минуты к клиенту с окном 20:00, и заморозка «всех, кто в
    пути» оставила бы аварию без исполнителя.

    Исключения: выбывшая бригада (`blocked`) до клиента не доедет, отменённый
    визит (`release`) ждать некому.

    Незамороженная бригада стоит в точке последнего замороженного визита (или
    в депо) и трогается не раньше момента события, а не «с начала смены»:
    иначе бригада без замороженных визитов получила бы в новом плане выезд
    14:35 при событии в 15:40.
    """
    jobs = {j.id: j for j in problem.rd.jobs}
    routes = {r.engineer_id: r for r in plan.routes}
    state = {}
    for eng in problem.engineers:
        r = routes.get(eng.id)
        done, marks = [], {}
        for s in (r.stops if r else []):
            if s.start_min <= at_min:
                marks[s.job_id] = STARTED
            elif (at_min + s.leg_min > s.start_min and eng.id not in blocked
                  and s.job_id not in release):
                marks[s.job_id] = EN_ROUTE
            else:
                break                  # визиты идут по времени: дальше только будущее
            done.append(s)
        if not done:
            state[eng.id] = dict(stops=[], marks={}, last_key=None,
                                 free_at=max(eng.shift_start, at_min),
                                 end_by=eng.shift_end, used={})
            continue
        last = done[-1]
        carried: dict = {}
        for s in done:
            job = jobs.get(s.job_id)
            for item, n in (job.equipment if job else {}).items():
                carried[item] = carried.get(item, 0) + n
        state[eng.id] = dict(
            stops=done, marks=marks, last_key=f"job:{last.job_id}",
            free_at=max(last.end_min, at_min),
            # Крайний срок — от утреннего выезда, а не от момента события.
            end_by=r.depart_min + MAX_WORKDAY_MIN,
            used=carried)
    return state


def frozen_marker(state) -> dict:
    """Пометка для плана: какой визит заморожен, у кого и в какое время. По
    ней аудитор сверяет, что прошлое не тронуто, а перенос — что не трогает
    начатое. Время в минутах, как at_min рядом."""
    return {s.job_id: dict(engineer_id=eid, start_min=s.start_min,
                           end_min=s.end_min, state=st["marks"][s.job_id])
            for eid, st in state.items() for s in st["stops"]}


def blocked_of(plan) -> set:
    """Выбывшие за день. Цепочка событий обязана их помнить: иначе второе
    событие вернёт в работу бригаду, выбывшую в первом."""
    out = set(plan.meta.get("blocked_engineers") or ())
    if plan.meta.get("blocked_engineer"):
        out.add(plan.meta["blocked_engineer"])
    return out


def cancelled_of(plan) -> set:
    """Отменённые за день: в плане их нет, в неназначенных тоже, и без этой
    памяти следующее событие спланировало бы их заново."""
    return set(plan.meta.get("cancelled") or ())


def day_state(plan, problem) -> dict:
    """Что в плане уже нельзя трогать: момент последнего события, замороженные
    визиты, выбывшие бригады и отменённые заявки. Для плана без события всё
    пусто, и правила заморозки ручной перенос не ограничивают."""
    at = plan.meta.get("at_min")
    frozen = plan.meta.get("frozen")
    if at is not None and frozen is None:
        frozen = frozen_marker(freeze(plan, problem, at, blocked_of(plan)))
    return dict(at_min=at, frozen=dict(frozen or {}), blocked=blocked_of(plan),
                cancelled=cancelled_of(plan))


# --- маршрут одной бригады после события --------------------------------------

def carried_by(route, jobs) -> dict:
    """Сколько оборудования уходит на визиты маршрута за весь день."""
    out: dict = {}
    for s in (route.stops if route else []):
        for item, n in jobs[s.job_id].equipment.items():
            out[item] = out.get(item, 0) + n
    return out


def rebuild_route(eng, route, frozen_ids, jobs_after, problem, at_min=None) -> Route:
    """Замороженная голова маршрута как есть, после неё — `jobs_after`.

    Голова не пересчитывается никогда: это уже случившееся. Хвост едет КАК
    МОЖНО РАНЬШЕ от конца головы, но не раньше момента события — так визиты,
    которых вставка не коснулась, сохраняют своё время, и клиентам не звонят
    зря. Бригада без головы (ещё не выехала) строится как обычный день: выезд
    рассчитывается к первому визиту, но не раньше события.

    Без события (at_min=None) и без головы это ровно тот же пересчёт, что в
    плане на утро, — так же считается и ручной перенос до событий.
    """
    head = [s for s in (route.stops if route else []) if s.job_id in frozen_ids]
    if not head:
        floor = eng.shift_start if at_min is None else max(eng.shift_start, at_min)
        return replay_route(eng, list(jobs_after), problem.travel,
                            problem.rd.depots, earliest=floor)
    travel = problem.travel
    stops = [_copy_stop(s) for s in head]
    cur = f"job:{head[-1].job_id}"
    t = head[-1].end_min if at_min is None else max(head[-1].end_min, at_min)
    for job in jobs_after:
        key = f"job:{job.id}"
        leg_min = travel.minutes(cur, key, eng.transport)
        arrive = t + leg_min
        start = max(arrive, job.win_start)
        stops.append(Stop(job_id=job.id, seq=0, arrive_min=arrive, start_min=start,
                          end_min=start + job.service_min, wait_min=start - arrive,
                          late_min=max(0, start - job.win_end),
                          leg_km=round(travel.km(cur, key), 3), leg_min=leg_min))
        t, cur = start + job.service_min, key
    for i, s in enumerate(stops, 1):
        s.seq = i
    return Route(engineer_id=eng.id, stops=stops, depart_min=route.depart_min)


def _tail_jobs(route, frozen_ids, jobs, without=None) -> list:
    return [jobs[s.job_id] for s in (route.stops if route else [])
            if s.job_id not in frozen_ids and s.job_id != without]


def route_without(eng, route, job_id, problem, at_min=None, frozen_ids=()) -> Route:
    """Маршрут бригады, у которой заявку забрали. Может оказаться недопустимым:
    дорожная матрица не обязана выполнять неравенство треугольника, и прямой
    проезд бывает длиннее, чем через изъятую точку. Проверяет вызывающий."""
    jobs = {j.id: j for j in problem.rd.jobs}
    return rebuild_route(eng, route, frozen_ids,
                         _tail_jobs(route, frozen_ids, jobs, without=job_id),
                         problem, at_min)


def insertion_attempts(eng, route, job, problem, at_min=None, frozen_ids=()):
    """Все позиции после замороженной головы: -> [(позиция, маршрут, вердикт)]."""
    jobs = {j.id: j for j in problem.rd.jobs}
    jobs[job.id] = job
    tail = _tail_jobs(route, frozen_ids, jobs, without=job.id)
    head_len = sum(1 for s in (route.stops if route else []) if s.job_id in frozen_ids)
    out = []
    for k in range(len(tail) + 1):
        trial = rebuild_route(eng, route, frozen_ids, tail[:k] + [job] + tail[k:],
                              problem, at_min)
        out.append((head_len + k, trial, route_fits(trial, eng)))
    return out


def best_insertion(eng, route, job, problem, at_min=None, frozen_ids=()):
    """-> (прирост пробега, позиция в маршруте, маршрут) либо None.

    Порядок остальных визитов не меняется: вставка отвечает на вопрос «куда
    эта заявка встанет сейчас», а не «как выглядел бы день заново». При
    равном приросте берётся первая позиция — как и в объяснении решения.
    """
    base_km = route.km if route else 0.0
    best = None
    for pos, trial, verdict in insertion_attempts(eng, route, job, problem,
                                                  at_min, frozen_ids):
        if not verdict.ok:
            continue
        added = round(trial.km - base_km, 2)
        if best is None or added < best[0]:
            best = (added, pos, trial)
    return best


def near_miss(eng, route, job, problem, at_min=None, frozen_ids=()) -> str:
    """Почему заявка не встаёт к этой бригаде: вердикт той позиции, где
    работы начались бы раньше всего. Одна конкретная строка вместо
    «не помещается по времени» без подробностей."""
    tries = insertion_attempts(eng, route, job, problem, at_min, frozen_ids)
    if not tries:
        return REASON_RU[Code.TIME]
    pos, trial, verdict = min(tries, key=lambda x: x[1].stops[x[0]].start_min)
    start = trial.stops[pos].start_min
    if start > job.win_end:
        return (f"раньше {hhmm(start)} к клиенту не успеть, "
                f"а окно {job.window_str}")
    return verdict.text or REASON_RU[Code.TIME]


# --- применение события ------------------------------------------------------

def apply_event(plan, problem, event: Event, time_limit_s: int = 10):
    """-> (новый план, разница). Исходный план не меняется."""
    at = event.at_min
    new_problem = problem_with_event(problem, event)
    blocked = blocked_of(plan)
    cancelled = cancelled_of(plan)
    release = set()
    if event.kind == "unavailable":
        blocked.add(event.engineer_id)
    elif event.kind == "cancel":
        started = next((s for r in plan.routes for s in r.stops
                        if s.job_id == event.job_id and s.start_min <= at), None)
        if started is not None:
            raise ValueError(f"{event.job_id}: работа уже начата в "
                             f"{hhmm(started.start_min)}, отмена поздно")
        release.add(event.job_id)
        cancelled.add(event.job_id)

    state = freeze(plan, new_problem, at, blocked, release)
    marker = frozen_marker(state)

    if event.mode == "insert":
        new = _insert(plan, new_problem, event, state, blocked)
    else:
        new = _replan(plan, new_problem, event, state, blocked,
                      set(marker) | cancelled, time_limit_s)

    meta = dict(fleet_size=len(new_problem.engineers))
    if "fleet_kept" in plan.meta:
        meta["fleet_kept"] = plan.meta["fleet_kept"]
    meta.update(new.meta)
    meta.update(event=event.title, mode=event.mode, at_min=at,
                frozen_jobs=len(marker), frozen=marker,
                events=list(plan.meta.get("events") or []) + [event.title])
    if event.kind == "unavailable":
        meta["blocked_engineer"] = event.engineer_id
    elif plan.meta.get("blocked_engineer"):
        meta["blocked_engineer"] = plan.meta["blocked_engineer"]
    if blocked:
        meta["blocked_engineers"] = sorted(blocked)
    if cancelled:
        meta["cancelled"] = sorted(cancelled)
    new.meta = meta
    return new, _diff(plan, new, event, marker, new_problem)


def _replan(plan, problem, event, state, blocked, skip, time_limit_s) -> Plan:
    """Режим перепланирования: остаток дня заново, солвером."""
    frozen_ids = {s.job_id for st in state.values() for s in st["stops"]}

    # Прежнее назначение — то, от чего отход платный. Замороженное сюда не
    # входит: оно и так неприкосновенно, а лишний штраф исказил бы объектив.
    stability = {s.job_id: r.engineer_id for r in plan.routes for s in r.stops
                 if s.job_id not in frozen_ids}

    # Хвост прежнего плана как стартовое решение: то, что уже обещано клиентам,
    # менять можно, но солвер должен начать с «ничего не меняем». Поездки,
    # начатые до события, заморожены, поэтому хвост остаётся допустимым и при
    # новой границе «не трогаться раньше события» — засев не отбрасывается.
    seed = {r.engineer_id: [s.job_id for s in r.stops if s.job_id not in skip]
            for r in plan.routes if r.engineer_id not in blocked}

    tail = solve(
        problem, time_limit_s=time_limit_s, skip_jobs=skip, seed_routes=seed,
        start_at={k: v["last_key"] for k, v in state.items() if v["last_key"]},
        available_from={k: v["free_at"] for k, v in state.items()},
        end_by={k: v["end_by"] for k, v in state.items()},
        capacity_used={k: v["used"] for k, v in state.items()},
        blocked_engineers=blocked, stability=stability,
        not_before=event.at_min,
        free_vehicles={k for k, v in state.items() if v["stops"]}, algo="replan")
    return _merge(plan, tail, state, problem)


def _insert(plan, problem, event, state, blocked) -> Plan:
    """Режим вставки: заявка занимает самое дешёвое допустимое место в
    свободных интервалах, остальной план не трогается вовсе.

    Цена места — как в целевой функции солвера: сначала не поднимать число
    задействованных исполнителей, потом пробег. Последующие визиты той же
    бригады могут сдвинуться по времени, но только в пределах своих окон и
    рабочего дня — это проверяет тот же предикат, что у солвера.
    """
    job, at = event.job, event.at_min
    frozen_ids = {s.job_id for st in state.values() for s in st["stops"]}
    routes = {r.engineer_id: r for r in plan.routes}
    jobs = {j.id: j for j in problem.rd.jobs}

    best, misses, rejected = None, [], None
    if job.win_end < at:
        rejected = Unassigned(
            job_id=job.id, code=Code.WINDOW_PASSED,
            reason=REASON_RU[Code.WINDOW_PASSED],
            detail=f"окно {job.window_str} закончилось раньше события в "
                   f"{hhmm(at)}: после события заявку не выполнить, новое "
                   f"время согласуется с клиентом")
    else:
        for order, e in enumerate(problem.engineers):
            if e.id in blocked:
                continue
            v = check_static(job, e)
            if not v.ok:
                misses.append((e, v))
                continue
            route = routes.get(e.id)
            eq = check_equipment(job, e, carried_by(route, jobs))
            if not eq.ok:
                misses.append((e, eq))
                continue
            fit = best_insertion(e, route, job, problem, at, frozen_ids)
            if fit is None:
                misses.append((e, Verdict(False, Code.TIME, near_miss(
                    e, route, job, problem, at, frozen_ids))))
                continue
            added, pos, trial = fit
            idle = not (route and route.stops)
            key = (idle, added, trial.stops[pos].start_min, order)
            if best is None or key < best[0]:
                best = (key, e.id, trial)
        if best is None:
            rejected = _why_not_inserted(job, misses)

    new_routes = []
    for r in plan.routes:
        if best and r.engineer_id == best[1]:
            new_routes.append(best[2])
        else:
            new_routes.append(_retimed(r, frozen_ids, at))
    if best and best[1] not in routes:
        new_routes.append(best[2])
    unassigned = [Unassigned(u.job_id, u.code, u.reason, u.detail)
                  for u in plan.unassigned]
    if rejected is not None:
        unassigned.append(rejected)
    return Plan(region=plan.region, algo="insert", routes=new_routes,
                unassigned=unassigned, meta={})


def _retimed(route, frozen_ids, at_min) -> Route:
    """Маршрут, который вставка не трогает, с одной поправкой времени.

    Бригада, которая к моменту события уже тронулась к клиенту, но с запасом
    (приехала бы рано и ждала), по правилу заморозки свободна — значит, в новом
    плане её поездка к этому визиту начинается в момент события, а не раньше.
    Начало работ при этом не меняется: запас, из-за которого визит не
    заморожен, ровно это и гарантирует. Меняются только приезд и ожидание."""
    stops = [_copy_stop(s) for s in route.stops]
    depart = route.depart_min
    for i, s in enumerate(stops):
        if s.job_id in frozen_ids:
            continue
        if s.arrive_min - s.leg_min < at_min <= s.start_min - s.leg_min:
            s.arrive_min = at_min + s.leg_min
            s.wait_min = s.start_min - s.arrive_min
            if i == 0:
                depart = at_min
        break
    return Route(engineer_id=route.engineer_id, stops=stops, depart_min=depart)


def _why_not_inserted(job, misses) -> Unassigned:
    """Причина по самому близкому промаху, с именем бригады."""
    if not misses:
        return Unassigned(job_id=job.id, code=Code.NO_ENGINEER,
                          reason=REASON_RU[Code.NO_ENGINEER],
                          detail="все бригады, которые могли бы её взять, выбыли")
    e, v = min(misses, key=lambda m: MISS_ORDER.get(m[1].code, 9))
    near = [m for m in misses if m[1].code == v.code]
    if v.code == Code.TIME:
        detail = (f"подходящих бригад {len(near)}, но ни в один маршрут заявка "
                  f"не встаёт, не сдвинув уже назначенные визиты за их окна или "
                  f"за конец дня; ближе всех {e.id}: {v.text}")
    elif v.code == Code.EQUIPMENT:
        detail = f"по профилю подходят {len(near)}, но у всех кончился запас; {e.id}: {v.text}"
    else:
        detail = f"ни одна бригада не подходит по профилю; ближе всех {e.id}: {v.text}"
    return Unassigned(job_id=job.id, code=v.code, reason=REASON_RU[v.code],
                      detail=detail)


def _merge(old_plan, tail, state, problem) -> Plan:
    """Замороженный кусок дня + заново спланированный хвост."""
    routes = []
    for r in tail.routes:
        head = [_copy_stop(s) for s in state.get(r.engineer_id, {}).get("stops", [])]
        stops = head + [_copy_stop(s) for s in r.stops]
        for i, s in enumerate(stops, 1):
            s.seq = i
        depart = (next((o.depart_min for o in old_plan.routes
                        if o.engineer_id == r.engineer_id), r.depart_min)
                  if head else r.depart_min)
        routes.append(Route(engineer_id=r.engineer_id, stops=stops,
                            depart_min=depart))
    return Plan(region=tail.region, algo="replan", routes=routes,
                unassigned=list(tail.unassigned), meta=dict(tail.meta))


def _copy_stop(s) -> Stop:
    return Stop(job_id=s.job_id, seq=s.seq, arrive_min=s.arrive_min,
                start_min=s.start_min, end_min=s.end_min, wait_min=s.wait_min,
                late_min=s.late_min, leg_km=s.leg_km, leg_min=s.leg_min)


# --- разница -----------------------------------------------------------------

def _owner_map(plan) -> dict:
    return {s.job_id: (r.engineer_id, s.seq) for r in plan.routes for s in r.stops}


def _lcs(a: list, b: list) -> set:
    """Наибольшая общая подпоследовательность: визиты, чей взаимный порядок
    сохранился. Остальные и есть «сменили порядок»."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = (dp[i + 1][j + 1] + 1 if a[i] == b[j]
                        else max(dp[i + 1][j], dp[i][j + 1]))
    keep, i, j = set(), 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            keep.add(a[i])
            i, j = i + 1, j + 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return keep


def _resequenced(old, new) -> list:
    """Сменил порядок — это про ВЗАИМНЫЙ порядок визитов одной бригады, а не
    про номер в списке. Вставка новой заявки третьей сдвигает номера всех
    последующих, но порядок их объезда не меняет; считать их переставленными
    значило бы доложить о перестройке плана, которой не было."""
    before = {r.engineer_id: [s.job_id for s in r.stops] for r in old.routes}
    seq_old = {s.job_id: s.seq for r in old.routes for s in r.stops}
    out = []
    for r in new.routes:
        was = before.get(r.engineer_id, [])
        now = [s.job_id for s in r.stops]
        common = set(was) & set(now)
        a = [j for j in was if j in common]
        b = [j for j in now if j in common]
        if a == b:
            continue
        keep = _lcs(a, b)
        for s in r.stops:
            if s.job_id in common and s.job_id not in keep:
                out.append(dict(job_id=s.job_id, engineer_id=r.engineer_id,
                                old_seq=seq_old[s.job_id], new_seq=s.seq))
    return out


def _diff(old, new, event: Event, marker: dict, problem) -> Diff:
    before, after = _owner_map(old), _owner_map(new)
    d = Diff(event=event.title, frozen=len(marker), mode=event.mode,
             en_route=sum(1 for m in marker.values() if m["state"] == EN_ROUTE))

    for jid, (eng, seq) in after.items():
        if jid not in before:
            d.added.append(dict(job_id=jid, to=eng, seq=seq))
        elif before[jid][0] != eng:
            d.moved.append(dict(job_id=jid, **{"from": before[jid][0], "to": eng}))
    d.resequenced = _resequenced(old, new)

    if event.kind == "cancel":
        d.cancelled.append({"job_id": event.job_id,
                            "from": before.get(event.job_id, (None,))[0]})

    reasons = {u.job_id: (u.reason or REASON_RU.get(u.code, "")) for u in new.unassigned}
    for jid, (eng, _) in before.items():
        if jid in after:
            continue
        if event.kind == "cancel" and jid == event.job_id:
            continue                               # не вытеснена, а отменена
        d.dropped.append(dict(
            job_id=jid, **{"from": eng},
            reason=reasons.get(jid, REASON_RU[Code.TIME])))

    # Новая заявка, которую не удалось принять. Её нет ни среди добавленных
    # (не назначена), ни среди вытесненных (её не было в плане) — без этого
    # поля авария в 20:30 прошла бы как «план не изменился».
    if event.kind == "urgent" and event.job is not None:
        for u in new.unassigned:
            if u.job_id == event.job.id:
                d.rejected.append(dict(job_id=u.job_id, code=u.code,
                                       reason=u.reason, detail=u.detail))

    d.metrics = {
        "used_engineers": dict(before=old.used_engineers, after=new.used_engineers,
                               delta=new.used_engineers - old.used_engineers),
        "total_km": dict(before=old.total_km, after=new.total_km,
                         delta=round(new.total_km - old.total_km, 2)),
        "assigned": dict(before=old.assigned, after=new.assigned,
                         delta=new.assigned - old.assigned),
        "late_jobs": dict(before=old.late_jobs, after=new.late_jobs,
                          delta=new.late_jobs - old.late_jobs),
    }
    return d


def format_diff(d: Diff) -> str:
    out = [f"\n=== Событие: {d.event} ===",
           f"режим: {MODE_RU[d.mode]}",
           f"заморожено начатых и тех, к кому уже едут: {d.frozen}"
           + (f" (в пути {d.en_route})" if d.en_route else "")]
    if d.is_quiet:
        out.append("план не изменился")
    for a in d.added:
        out.append(f"  + {a['job_id']} -> {a['to']} (визит №{a['seq']})")
    for x in d.rejected:
        out.append(f"  ! {x['job_id']} не принята: {x['reason']}. {x['detail']}")
    for x in d.cancelled:
        out.append(f"  x {x['job_id']} отменена"
                   + (f" (была у {x['from']})" if x["from"] else ""))
    for m in d.moved:
        out.append(f"  ~ {m['job_id']}: {m['from']} -> {m['to']}")
    if d.resequenced:
        out.append(f"  порядок объезда изменился у {len(d.resequenced)} заявок")
    for x in d.dropped:
        out.append(f"  - {x['job_id']} (было у {x['from']}): {x['reason']}")
    for key, title in (("used_engineers", "исполнителей"), ("assigned", "назначено"),
                       ("total_km", "пробег, км"), ("late_jobs", "опозданий")):
        m = d.metrics[key]
        out.append(f"  {title}: {m['before']} -> {m['after']} ({m['delta']:+})")
    return "\n".join(out)


def make_demo_emergency(problem, at_min: int, job_id: str = "AVARIA-DEMO"):
    """Срочная заявка для демонстрации: адрес, которого НЕТ в матрице.

    Точка ставится в УДАЛЁННОМ кластере, если он в регионе есть. Авария в
    полутора километрах от офиса предсказуема и не доказывает ничего: её возьмёт
    ближайшая из десяти московских бригад. Авария в Домодедово одним событием
    показывает сразу три вещи — что зона обслуживания реальна (московские
    бригады её не возьмут), что требование автомобиля работает, и что уже
    выполненное не переигрывается.

    Координаты смещены от депо на ~1.5 км, чтобы точка гарантированно не совпала
    с известной и прошла через достройку матрицы в рантайме — ровно так, как это
    случится у диспетчера, вводящего новый адрес.
    """
    from ..config import PRIO_EMERGENCY
    from ..io.normatives import SRV_EMERGENCY
    from ..models import Job
    depots = list(problem.rd.depots.values())
    office = next(d for d in depots if d.is_office)
    fleet_of = {}
    for e in problem.engineers:
        fleet_of[e.cluster] = fleet_of.get(e.cluster, 0) + 1
    # Из удалённых кластеров берём самый дефицитный по бригадам: там ограничение
    # туже всего, и событие показывает его нагляднее. Название города в коде не
    # зашито — правило считает его само.
    remote = sorted((d for d in depots if not d.is_office and fleet_of.get(d.cluster)),
                    key=lambda d: (fleet_of[d.cluster], d.cluster))
    base = remote[0] if remote else office
    return Job(id=job_id, address=f"{base.cluster}, адрес введён диспетчером",
               lat=round(base.lat + 0.013, 6), lon=round(base.lon + 0.008, 6),
               geo_level=0, district="", bk="Авария", hd="Авария",
               skill="emergency", service_min=SRV_EMERGENCY, equipment={},
               priority=PRIO_EMERGENCY, win_start=at_min, win_end=at_min + 120,
               floating_window=False, cluster=base.cluster,
               requires_transport="car", release_min=at_min)
