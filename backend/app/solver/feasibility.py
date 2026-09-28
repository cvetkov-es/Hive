# -*- coding: utf-8 -*-
"""ЕДИНЫЙ предикат допустимости «бригада может взять заявку».

Один источник правды для: предфильтра солвера, карточки объяснения, валидации
ручного переназначения диспетчером и причин неназначения. Если развести эти
четыре места по разным реализациям, они разойдутся, и интерфейс начнёт говорить
«нельзя» про то, что солвер уже сделал.

Статическая часть (Code.SKILL/TRANSPORT/CLUSTER/SHIFT_WINDOW) не зависит от
маршрута — по ней солвер сразу запрещает назначение. Динамическая
(EQUIPMENT/TIME) зависит от того, что бригада уже везёт и где находится.
"""
from __future__ import annotations
from dataclasses import dataclass

from ..config import SKILL_RU, TRANSPORT_RU, EQUIPMENT_RU


class Code:
    OK = "OK"
    SKILL = "NO_SKILL"
    TRANSPORT = "NO_TRANSPORT"
    CLUSTER = "OTHER_CLUSTER"
    SHIFT_WINDOW = "WINDOW_OUTSIDE_SHIFT"
    EQUIPMENT = "NO_EQUIPMENT"
    TIME = "NO_TIME_SLOT"
    NO_ENGINEER = "NO_ENGINEER_AT_ALL"
    WINDOW_PASSED = "WINDOW_CLOSED_BEFORE_EVENT"


REASON_RU = {
    Code.SKILL: "нет требуемой квалификации",
    Code.TRANSPORT: "не тот тип транспорта",
    Code.CLUSTER: "бригада обслуживает другую зону",
    Code.SHIFT_WINDOW: "временное окно не пересекается со сменой",
    Code.EQUIPMENT: "закончилось нужное оборудование",
    Code.TIME: "не помещается в маршрут по времени",
    Code.NO_ENGINEER: "нет ни одного исполнителя, способного выполнить заявку",
    Code.WINDOW_PASSED: "окно клиента закрылось до момента события",
}


@dataclass
class Verdict:
    ok: bool
    code: str
    text: str = ""

    def __bool__(self):
        return self.ok


def check_profile(job, eng) -> Verdict:
    """Кто эта бригада, безотносительно времени: навык, транспорт, зона.

    Отделено от окна сознательно. Окно двигать может человек (Р5), и «цена
    вопроса» для неназначенной заявки как раз перебирает: а если окно другое,
    кто тогда возьмёт? Для такого перебора нужны кандидаты, отброшенные только
    по времени, а не по существу.
    """
    if job.skill not in eng.skills:
        return Verdict(False, Code.SKILL,
                       f"нужен навык «{SKILL_RU[job.skill]}», у бригады "
                       f"{', '.join(SKILL_RU[s] for s in sorted(eng.skills))}")
    if job.requires_transport and eng.transport != job.requires_transport:
        return Verdict(False, Code.TRANSPORT,
                       f"нужен транспорт «{TRANSPORT_RU[job.requires_transport]}», "
                       f"у бригады «{TRANSPORT_RU[eng.transport]}»")
    if job.cluster != eng.cluster:
        return Verdict(False, Code.CLUSTER,
                       f"заявка в зоне «{job.cluster}», бригада работает в «{eng.cluster}»")
    return Verdict(True, Code.OK)


def check_static(job, eng) -> Verdict:
    """Проверки, не зависящие от текущего маршрута. Их применяет солвер как
    жёсткий запрет назначения — это и есть «ограничения реально проверяются»."""
    v = check_profile(job, eng)
    if not v.ok:
        return v
    # окно прибытия должно хотя бы начаться внутри смены
    if job.win_start > eng.shift_end or job.win_end < eng.shift_start:
        return Verdict(False, Code.SHIFT_WINDOW,
                       f"окно {job.window_str} вне смены {eng.shift_str}")
    if job.win_start + job.service_min > eng.shift_end:
        return Verdict(False, Code.SHIFT_WINDOW,
                       f"работа {job.service_min} мин с {job.window_str} "
                       f"не заканчивается до конца смены {eng.shift_str}")
    return Verdict(True, Code.OK)


def check_equipment(job, eng, carried: dict | None = None) -> Verdict:
    """Оборудование выдаётся в офисе на весь день, поэтому это ограничение
    по ёмкости: сумма потребности назначенных заявок <= запас бригады."""
    carried = carried or {}
    for item, need in job.equipment.items():
        left = eng.equipment.get(item, 0) - carried.get(item, 0)
        if left < need:
            return Verdict(False, Code.EQUIPMENT,
                           f"нужен «{EQUIPMENT_RU.get(item, item)}», "
                           f"у бригады осталось {max(0, left)}")
    return Verdict(True, Code.OK)


def candidates(job, engineers) -> tuple[list, list]:
    """-> (подходящие по статике, отклонённые с причинами)"""
    ok, rejected = [], []
    for e in engineers:
        v = check_static(job, e)
        (ok if v.ok else rejected).append(e if v.ok else (e, v))
    return ok, rejected


def route_fits(route, eng) -> Verdict:
    """Укладывается ли ГОТОВЫЙ маршрут в границы дня бригады.

    Здесь же, а не в каждом алгоритме по отдельности: этими же правилами
    ограничен солвер, и стоит базовому варианту или проверке ручного
    переназначения разойтись с ними хоть на минуту — сравнение планов
    перестаёт быть честным, а интерфейс начинает запрещать то, что солвер
    уже сделал.
    """
    from ..config import HARD_SHIFT_END, MAX_WORKDAY_MIN
    from ..models import hhmm

    if not route.stops:
        return Verdict(True, Code.OK)
    late = [s for s in route.stops if s.late_min > 0]
    if late:
        return Verdict(False, Code.TIME,
                       f"{late[0].job_id}: начало {hhmm(late[0].start_min)} "
                       f"позже закрытия окна")
    last = route.stops[-1]
    if HARD_SHIFT_END and last.end_min > eng.shift_end:
        return Verdict(False, Code.TIME,
                       f"работа закончится {hhmm(last.end_min)}, "
                       f"смена до {hhmm(eng.shift_end)}")
    if route.depart_min < eng.shift_start:
        return Verdict(False, Code.TIME,
                       f"выезд {hhmm(route.depart_min)} раньше начала смены "
                       f"{hhmm(eng.shift_start)}")
    if route.span_min > MAX_WORKDAY_MIN:
        return Verdict(False, Code.TIME,
                       f"рабочий день {route.span_min // 60}ч{route.span_min % 60:02d} "
                       f"больше норматива {MAX_WORKDAY_MIN // 60} ч")
    return Verdict(True, Code.OK)
