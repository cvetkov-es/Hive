# -*- coding: utf-8 -*-
"""Почему у бригады такой маршрут — объяснение дня целиком.

ТЗ 2.4.2: «Для выбранного маршрута — краткое объяснение, какие ограничения и
факторы повлияли на решение»; ТЗ 2.1.7: «…почему выбран именно такой
маршрут». Карточка заявки отвечает, почему заявка у этой бригады. Здесь —
почему день бригады выглядит так: откуда порядок, откуда ожидание, какие
правила его держат и почему бригада без заявок стоит в резерве.

Правила те же, что у карточки заявки: объяснение читает готовый план и
проверяет его теми же правилами допустимости, которыми пользовался
планировщик. Всё, что здесь утверждается про альтернативы («переставить
соседние заявки нельзя», «никто, кроме неё, не может взять»), не угадано, а
проверено перестановкой или той же проверкой бригады.
"""
from __future__ import annotations

from ..config import EQUIPMENT_RU, MAX_WORKDAY_MIN, SKILL_RU, TRANSPORT_RU
from ..models import Route, hhmm
from . import weights as W
from .explain import (ASAP_TEXT, CAVEAT, EPS_KM, SKILL_ORDER, TRANSPORT_HAS,
                      TRANSPORT_NEED, Day, _asap_starts, _count, _dur, _fits,
                      _ids, _join, _km, _plural, _replay, _shifts, plan_days,
                      window_str, work_kind)
from .feasibility import Code, check_static

TIGHT_MIN = 30          # запас до предела, при котором ограничение «держит» день
LONG_WAIT_MIN = 30      # ожидание, о котором стоит сказать отдельной строкой


def explain_route(engineer_id: str, plan, problem) -> dict:
    """-> объяснение дня бригады. KeyError, если бригады нет в задаче плана."""
    engs = {e.id: e for e in problem.engineers}
    eng = engs[engineer_id]
    jobs = {j.id: j for j in problem.rd.jobs}
    routes = {r.engineer_id: r for r in plan.routes}
    route = routes.get(eng.id) or Route(engineer_id=eng.id, stops=[],
                                        depart_min=eng.shift_start)
    days = plan_days(plan, problem)
    day = days.get(eng.id, Day())
    start_point = problem.rd.depots[eng.depot].name

    header = dict(
        engineer_id=eng.id, name=eng.name, transport=TRANSPORT_RU[eng.transport],
        skills=[SKILL_RU[s] for s in SKILL_ORDER if s in eng.skills],
        zone=eng.cluster,
        shift=f"{hhmm(eng.shift_start)}–{hhmm(eng.shift_end)}",
        start_point=start_point,
        equipment=[dict(item=k, name=EQUIPMENT_RU.get(k, k), qty=n)
                   for k, n in eng.equipment.items()],
        equipment_text=", ".join(f"{EQUIPMENT_RU.get(k, k)} ×{n}"
                                 for k, n in eng.equipment.items()) or "не выдано")

    out = dict(engineer_id=eng.id, header=header, caveat=CAVEAT)
    who = f"{eng.id} ({TRANSPORT_RU[eng.transport]}, зона «{eng.cluster}»)"

    if not route.stops:
        out.update(status="в резерве", headline=f"{who} сегодня в резерве: "
                                                f"заявок нет",
                   day=None, stops=[], order=None, constraints=[],
                   reserve=_reserve(eng, plan, problem, days, jobs))
        if day.blocked:
            out["status"] = "выбыла"
            out["headline"] = (f"{who} выбыла в {hhmm(day.at)} и новых заявок не "
                               f"берёт")
        out["summary"] = [out["headline"]] + ([out["reserve"]["text"]]
                                              if out.get("reserve") else [])
        return out

    stops = route.stops
    travel = sum(s.leg_min for s in stops)
    wait = sum(s.wait_min for s in stops)
    work = sum(jobs[s.job_id].service_min for s in stops)
    span = route.span_min
    n = len(stops)
    day_block = dict(
        depart=hhmm(route.depart_min), finish=hhmm(stops[-1].end_min),
        span_min=span, span_text=_dur(span), limit_min=MAX_WORKDAY_MIN,
        limit_text=f"{MAX_WORKDAY_MIN // 60} ч", left_min=MAX_WORKDAY_MIN - span,
        jobs=n, km=route.km, travel_min=travel, wait_min=wait, work_min=work,
        text=(f"Выезд в {hhmm(route.depart_min)} ({start_point}), последняя работа "
              f"закончится в {hhmm(stops[-1].end_min)}: день {_dur(span)} из "
              f"{MAX_WORKDAY_MIN // 60} ч по нормативу. "
              f"{_count(n, 'заявка', 'заявки', 'заявок')}, {_km(route.km)} км, в дороге "
              f"{_dur(travel)}, на месте {_dur(work)}"
              + (f", ожидание окон {_dur(wait)}" if wait else ", без ожиданий")
              + "."))

    at = day.at
    head = {s.job_id for s in day.head}
    stop_rows = [dict(
        seq=s.seq, job_id=s.job_id, work_kind=work_kind(jobs[s.job_id]),
        window=window_str(jobs[s.job_id]), arrive=hhmm(s.arrive_min),
        start=hhmm(s.start_min), end=hhmm(s.end_min), wait_min=s.wait_min,
        leg_km=s.leg_km, leg_min=s.leg_min,
        frozen=s.job_id in head) for s in stops]

    order = _order(eng, route, day, problem, jobs, start_point)
    constraints = _constraints(eng, route, plan, problem, days, jobs)
    status = "выбыла" if day.blocked else "в работе"
    headline = (f"{who}: {_count(n, 'заявка', 'заявки', 'заявок')}, {_km(route.km)} км, "
                f"день {_dur(span)} из {MAX_WORKDAY_MIN // 60} ч")
    if day.blocked:
        headline += f"; выбыла в {hhmm(at)} — дальше заявок не берёт"

    # Краткое объяснение — три-пять строк, как у карточки заявки: порядок,
    # правило аварий, самое заметное ожидание, самый длинный переезд и два
    # самых жёстких ограничения. Остальное — в разделах ниже.
    summary = [order["text"][0]]
    summary += [t for t in order["text"][1:] if ASAP_TEXT in t]
    summary += [w["text"] for w in order["waits"] if w["wait_min"] >= LONG_WAIT_MIN][:1]
    if order.get("longest_leg"):
        summary.append(order["longest_leg"]["text"])
    summary += [c["text"] for c in sorted(constraints, key=lambda c: c["rank"])
                if c.get("binding")][:2]
    out.update(status=status, headline=headline, day=day_block, stops=stop_rows,
               order=order, constraints=constraints, reserve=None, summary=summary)
    if at is not None:
        frozen = [s.job_id for s in stops if s.job_id in head]
        if frozen:
            out["frozen_text"] = (f"На момент события в {hhmm(at)} зафиксированы "
                                  f"{_count(len(frozen), 'визит', 'визита', 'визитов')} "
                                  f"({_ids(frozen, 5)}): начатое не бросают, и эта "
                                  f"часть дня не перепланировалась.")
    return out


# --- почему такой порядок ----------------------------------------------------

def _windows_chain(route, jobs) -> str:
    """«10:00–12:00 ×2 → 12:00–14:00 → весь день → 20:00–22:00»."""
    chain = []
    for s in route.stops:
        j = jobs[s.job_id]
        w = "весь день" if j.floating_window else window_str(j)
        if chain and chain[-1][0] == w:
            chain[-1][1] += 1
        else:
            chain.append([w, 1])
    return " → ".join(w if k == 1 else f"{w} ×{k}" for w, k in chain)


def _order(eng, route, day, problem, jobs, start_point) -> dict:
    stops = route.stops
    fixed = [s for s in stops if not jobs[s.job_id].floating_window]
    follows = all(jobs[a.job_id].win_start <= jobs[b.job_id].win_start
                  for a, b in zip(fixed, fixed[1:]))
    chain = _windows_chain(route, jobs)
    texts = []
    if follows:
        texts.append(f"Порядок задан окнами клиентов: {chain}.")
    else:
        texts.append(f"Окна клиентов по порядку объезда: {chain}.")

    # Аварии с окном на весь день: их место выбирает не окно, а правило
    # «как можно раньше», и об этом надо сказать, иначе авария первой в
    # маршруте выглядит случайностью.
    asap = [s for s in stops if jobs[s.job_id].skill == "emergency"
            and jobs[s.job_id].floating_window]
    if asap:
        first = ", ".join(f"{s.job_id} в {hhmm(s.start_min)}" for s in asap[:3])
        texts.append(f"{_plural(len(asap), 'Авария', 'Аварии', 'Аварии')} с окном на "
                     f"весь день ({first}) {_plural(len(asap), 'стоит', 'стоят', 'стоят')} "
                     f"не по окну: {ASAP_TEXT}.")

    swaps = _swaps(eng, route, day, problem, jobs)
    if swaps["pairs"]:
        texts.append(swaps["text"])

    waits = []
    for i, s in enumerate(stops):
        if s.wait_min <= 0:
            continue
        j = jobs[s.job_id]
        prev = (f"предыдущая работа (заявка {stops[i - 1].job_id}) закончится в "
                f"{hhmm(stops[i - 1].end_min)}" if i else
                f"выехать раньше начала смены в {hhmm(eng.shift_start)} нельзя "
                f"({start_point})")
        waits.append(dict(
            job_id=s.job_id, wait_min=s.wait_min,
            text=(f"Перед заявкой {s.job_id} — ожидание {_dur(s.wait_min)}: бригада "
                  f"приедет в {hhmm(s.arrive_min)}, а окно клиента "
                  f"{window_str(j)} открывается в {hhmm(j.win_start)}; {prev}.")))
    if waits and max(w["wait_min"] for w in waits) >= LONG_WAIT_MIN:
        texts.append("Простой планировщик не сокращает: он экономит сначала "
                     "бригады, потом километры, а часы ожидания в оценку плана "
                     "не входят.")

    longest = max(stops, key=lambda s: s.leg_km)
    i = stops.index(longest)
    frm = f"заявка {stops[i - 1].job_id}" if i else start_point
    leg = dict(from_job=stops[i - 1].job_id if i else None, to_job=longest.job_id,
               km=longest.leg_km, min=longest.leg_min,
               text=(f"Самый длинный переезд — {_km(longest.leg_km)} км, "
                     f"{_dur(longest.leg_min)}: {frm} → заявка {longest.job_id}."))
    return dict(windows=chain, follows_windows=follows, text=texts, waits=waits,
                longest_leg=leg, swaps=swaps)


def _swaps(eng, route, day, problem, jobs) -> dict:
    """Проверка порядка перестановкой соседних заявок. Только то, что ещё
    можно переставлять: после события прошлое не трогается."""
    seq = day.seq
    base = _replay(eng, seq, problem, day)
    infeasible, longer, better = 0, 0, []
    for i in range(len(seq) - 1):
        trial = seq[:i] + [seq[i + 1], seq[i]] + seq[i + 2:]
        r = _replay(eng, trial, problem, day)
        if not _fits(r, eng, day).ok:
            infeasible += 1
            continue
        d = round(r.km - base.km, 2)
        if d >= -EPS_KM:
            longer += 1
            continue
        before = _asap_starts(base.stops, jobs)
        after = _asap_starts(r.stops, jobs)
        shifts = _shifts(before, after)
        net = sum(shifts.values())
        better.append(dict(a=seq[i].id, b=seq[i + 1].id, saving=-d,
                           asap=net > 0 and
                           net * W.EMERGENCY_ASAP_PENALTY_PER_MIN >= -d * 1000,
                           delay=net))
    pairs = len(seq) - 1
    if pairs <= 0:
        return dict(pairs=0, infeasible=0, longer=0, better=[], text="")
    parts = []
    if infeasible:
        parts.append(f"{infeasible} из {pairs} "
                     f"{_plural(infeasible, 'нарушает', 'нарушают', 'нарушают')} "
                     f"окно клиента или длину дня")
    if longer:
        parts.append(f"{longer} из {pairs} "
                     f"{_plural(longer, 'удлиняет', 'удлиняют', 'удлиняют')} маршрут")
    text = ("Поменять местами соседние заявки без потерь нельзя: "
            + "; ".join(parts) + "." if not better else
            "Перестановка соседних заявок: " + "; ".join(parts) + ("; " if parts else ""))
    if better:
        b = better[0]
        extra = (f"авария начнётся на {_dur(b['delay'])} позже" if b["asap"] else
                 "этот вариант за время счёта планировщик не нашёл")
        text += (f"поменять местами {b['a']} и {b['b']} — на {_km(b['saving'])} км "
                 f"короче, но {extra}"
                 + (f" (ещё {_count(len(better) - 1, 'такая пара', 'такие пары', 'таких пар')})"
                    if len(better) > 1 else "") + ".")
    return dict(pairs=pairs, infeasible=infeasible, longer=longer, better=better,
                text=text)


# --- какие ограничения держат день --------------------------------------------

def _constraints(eng, route, plan, problem, days, jobs) -> list:
    out = []
    stops = route.stops
    n = len(stops)
    mine = [jobs[s.job_id] for s in stops]
    alive = [e for e in problem.engineers if not days.get(e.id, Day()).blocked]

    # Заявки, которые кроме этой бригады не может взять никто.
    only = []
    for j in mine:
        fit = [e for e in alive if check_static(j, e).ok]
        if len(fit) == 1 and fit[0].id == eng.id:
            only.append(j)
    if only:
        groups: dict = {}
        for j in only:
            groups.setdefault(_why_only(j, eng, problem), []).append(j.id)
        whys = "; ".join(f"{why} ({_ids(ids, 4)})" for why, ids in groups.items())
        out.append(dict(key="only", rank=0, binding=True, jobs=[j.id for j in only],
                        text=f"Только эта бригада может взять "
                             f"{_count(len(only), 'заявку', 'заявки', 'заявок')}: {whys}."))

    need = [j for j in mine if j.requires_transport]
    if need:
        kinds = sorted({TRANSPORT_NEED[j.requires_transport] for j in need})
        out.append(dict(key="transport", rank=5, binding=False, jobs=[j.id for j in need],
                        text=f"{_count(len(need), 'заявка требует', 'заявки требуют', 'заявок требуют')} "
                             f"{_join(kinds)} — {TRANSPORT_HAS[eng.transport]}."))

    for item, stock in eng.equipment.items():
        used = sum(j.equipment.get(item, 0) for j in mine)
        if not used:
            continue
        tight = used >= stock
        out.append(dict(key=f"equipment:{item}", rank=3, binding=tight,
                        text=(f"«{EQUIPMENT_RU.get(item, item)}»: в заявках маршрута "
                              f"{used} из {stock} выданных утром"
                              + (" — запас исчерпан, ещё одну такую заявку бригада "
                                 "не возьмёт." if tight else "."))))

    span = route.span_min
    left = MAX_WORKDAY_MIN - span
    out.append(dict(key="workday", rank=1, binding=left <= TIGHT_MIN,
                    text=(f"День {_dur(span)} при нормативе {MAX_WORKDAY_MIN // 60} ч: "
                          + ("запаса нет — день упирается в норматив." if left <= 0
                             else f"запас всего {_dur(left)} — день упирается в норматив."
                             if left <= TIGHT_MIN else f"запас {_dur(left)}."))))

    fixed = [j for j in mine if not j.floating_window]
    if fixed:
        lo = min(j.win_start for j in fixed)
        hi = max(j.win_end for j in fixed)
        out.append(dict(key="windows", rank=6, binding=False,
                        text=f"Окна клиентов маршрута — с {hhmm(lo)} до {hhmm(hi)}; "
                             f"первая работа начинается в {hhmm(stops[0].start_min)}, "
                             f"последняя заканчивается в {hhmm(stops[-1].end_min)}."))

    end = stops[-1].end_min
    if eng.shift_end - end <= TIGHT_MIN:
        out.append(dict(key="shift", rank=2, binding=True,
                        text=f"Работа заканчивается в {hhmm(end)} — у самого конца "
                             f"смены {hhmm(eng.shift_end)}."))

    zone_jobs = sum(1 for j in problem.rd.jobs if j.cluster == eng.cluster)
    zone_brigades = [e for e in problem.engineers if e.cluster == eng.cluster]
    out.append(dict(key="zone", rank=4, binding=len(zone_brigades) <= 1,
                    text=f"Работает только в зоне «{eng.cluster}»: в зоне "
                         f"{_count(len(zone_brigades), 'бригада', 'бригады', 'бригад')} "
                         f"на {_count(zone_jobs, 'заявку', 'заявки', 'заявок')}, "
                         f"у этой — {n}."))
    return out


def _why_only(job, eng, problem) -> str:
    """Какое сочетание требований делает бригаду единственной: словами
    диспетчера, от зоны — «в зоне «Москва» навык «Аварийные работы» есть
    только у неё»."""
    zone = [e for e in problem.engineers if e.cluster == job.cluster]
    skill = SKILL_RU[job.skill]
    if len(zone) == 1:
        return f"в зоне «{job.cluster}» она единственная бригада"
    skilled = [e for e in zone if job.skill in e.skills]
    if len(skilled) == 1:
        return f"навык «{skill}» в зоне «{job.cluster}» есть только у неё"
    need = job.requires_transport
    if need and sum(1 for e in skilled if e.transport == need) == 1:
        return (f"в зоне «{job.cluster}» только у неё и навык «{skill}», и "
                f"{TRANSPORT_NEED[need]}")
    return f"по навыку «{skill}», транспорту, зоне и смене подходит только она"


# --- резерв ------------------------------------------------------------------

def _reserve(eng, plan, problem, days, jobs) -> dict:
    """Почему бригада без заявок. Ответ ищется в двух местах: какие заявки
    она вообще может выполнить и кому они достались."""
    can = [j for j in problem.rd.jobs if check_static(j, eng).ok]
    owner = {s.job_id: r for r in plan.routes for s in r.stops}
    if not can:
        fails: dict = {}
        for j in problem.rd.jobs:
            v = check_static(j, eng)
            fails.setdefault(v.code, 0)
            fails[v.code] += 1
        parts = []
        for code, k in sorted(fails.items(), key=lambda kv: -kv[1]):
            if code == Code.CLUSTER:
                parts.append(f"{_count(k, 'заявка', 'заявки', 'заявок')} в других зонах")
            elif code == Code.SKILL:
                parts.append(f"{k} требуют навыка, которого у неё нет")
            elif code == Code.TRANSPORT:
                parts.append(f"{k} требуют другого транспорта")
            elif code == Code.SHIFT_WINDOW:
                parts.append(f"у {k} окно вне её смены")
        return dict(can_do=0, text="Ни одна заявка дня ей не подходит: "
                                   + ", ".join(parts) + ".")
    taken = [j for j in can if j.id in owner]
    free = [j for j in can if j.id not in owner]
    text = (f"Подходящих ей заявок {len(can)}: "
            f"{_count(len(taken), 'уже стоит', 'уже стоят', 'уже стоят')} в маршрутах "
            f"других бригад")
    cheapest = None
    start = f"depot:{problem.rd.depots[eng.depot].key}"
    for j in taken:
        km = problem.travel.km(start, f"job:{j.id}")
        if cheapest is None or km < cheapest[0]:
            cheapest = (km, j)
    if free:
        text += (f", {_count(len(free), 'не назначена', 'не назначены', 'не назначены')} "
                 f"({_ids([j.id for j in free])}) — их разбор в карточке заявки")
    text += (". Вывести её на линию — плюс один исполнитель: число бригад план "
             "бережёт сразу после числа закрытых заявок и прежде километров.")
    if cheapest:
        km, j = cheapest
        r = owner[j.id]
        text += (f" Ближе всех к её точке выезда заявка {j.id} — {_km(km)} км; её "
                 f"везёт {r.engineer_id}.")
    return dict(can_do=len(can), taken=len(taken), unassigned=[j.id for j in free],
                text=text)
