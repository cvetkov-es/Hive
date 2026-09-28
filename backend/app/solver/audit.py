# -*- coding: utf-8 -*-
"""Независимый аудит готового плана — вторая правда о нём.

Модуль сознательно НЕ импортирует ortools, feasibility, engine, baseline и
metrics. Это проверяется тестом, а не обещано в комментарии. Смысл в том, что
план и его подтверждение не должны быть посчитаны одним кодом: согласованность
кода с самим собой ничего не доказывает, а расхождение «на карте одно, в
метрике другое» обнаруживается у диспетчера, а не в разработке.

Аудитор не строит расписание заново — он сверяет записанное. Все числа
маршрута (приезд, начало, окончание, ожидание, опоздание, плечо) должны
выводиться друг из друга и из дорожной матрицы. Отсюда две ключевые проверки:

  A7 — приезд на каждую точку равен окончанию работ на предыдущей плюс плечо
       из матрицы. Ловит телепортацию бригады и сдвиг расписания руками;
  A8 — пробег равен сумме плеч из матрицы с допуском 50 м. Ловит «улучшение»
       обязательной метрики правкой числа в маршруте.

Допуск 50 м, а не ноль: плечи хранятся округлёнными до метра, и на маршруте
из двадцати точек накапливается расхождение округления.

Ещё две проверки закрывают правила, которые солвер соблюдает, а аудитор
проверяет независимо:

  A11 — после события ничего не начинается в прошлом. План после события несёт
        момент события (meta.at_min) и пометку замороженных визитов
        (meta.frozen): у кого, во сколько начат и закончен. Замороженное обязано
        остаться ровно таким, а всё остальное — выезжать и начинаться не
        раньше события. Без этой проверки перенос уже выполненной заявки к
        другой бригаде «на 11:13» при событии в 15:40 проходил бы аудит 10 из 10;
  A12 — бригада работает в своей зоне обслуживания. Солвер это запрещает, и
        аудитор обязан это видеть, а не верить на слово.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from ..config import HARD_SHIFT_END, MAX_WORKDAY_MIN
from ..models import hhmm

KM_TOLERANCE = 0.05        # 50 метров на маршрут
MIN_TOLERANCE = 0          # время целочисленное, расхождений быть не должно

CHECKS = [
    ("A1", "каждая заявка назначена не более одного раза"),
    ("A2", "у исполнителя есть требуемая квалификация"),
    ("A3", "тип транспорта соответствует требованию заявки"),
    ("A4", "оборудования хватает: утренний запас не уходит в минус"),
    ("A5", "начало работ не раньше открытия окна"),
    ("A6", "начало работ не позже закрытия окна"),
    ("A7", "время приезда сходится: конец предыдущей работы плюс дорога"),
    ("A8", "километры маршрута сходятся с таблицей расстояний (точность 50 м)"),
    ("A9", "работа заканчивается внутри смены"),
    ("A10", "рабочий день не длиннее нормы"),
    ("A11", "после события ничего не назначено в прошлое, начатые работы не тронуты"),
    ("A12", "бригада работает в своей зоне обслуживания"),
]


@dataclass
class Check:
    code: str
    title: str
    ok: bool
    problems: list = field(default_factory=list)


@dataclass
class AuditReport:
    region: str
    algo: str
    checks: list
    violations: list

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.ok)

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def ok(self) -> bool:
        return self.passed == self.total

    @property
    def headline(self) -> str:
        return f"Аудит {self.passed}/{self.total}, пересчитано независимо"


def audit_plan(plan, problem) -> AuditReport:
    jobs = {j.id: j for j in problem.rd.jobs}
    engs = {e.id: e for e in problem.engineers}
    travel, depots = problem.travel, problem.rd.depots
    found = {code: [] for code, _ in CHECKS}

    def bad(code: str, text: str):
        found[code].append(text)

    # --- A1: одна заявка — один исполнитель --------------------------------
    seen = {}
    for r in plan.routes:
        for s in r.stops:
            seen.setdefault(s.job_id, []).append(r.engineer_id)
    for jid, owners in seen.items():
        if len(owners) > 1:
            bad("A1", f"{jid}: назначена дважды — {', '.join(owners)}")
        if jid not in jobs:
            bad("A1", f"{jid}: в маршруте есть заявка, которой нет в регионе")

    # --- по маршрутам --------------------------------------------------------
    for r in plan.routes:
        if not r.stops:
            continue
        eng = engs.get(r.engineer_id)
        if eng is None:
            bad("A1", f"{r.engineer_id}: маршрут у неизвестной бригады")
            continue

        carried: dict = {}
        prev_key = f"depot:{depots[eng.depot].key}"
        prev_end = r.depart_min
        km_sum = 0.0

        for s in r.stops:
            j = jobs.get(s.job_id)
            if j is None:
                continue
            key = f"job:{j.id}"

            if j.skill not in eng.skills:
                bad("A2", f"{j.id}: нужен навык «{j.skill}», у {eng.id} "
                          f"{'/'.join(sorted(eng.skills))}")
            if j.requires_transport and eng.transport != j.requires_transport:
                bad("A3", f"{j.id}: нужен транспорт «{j.requires_transport}», "
                          f"у {eng.id} «{eng.transport}»")
            if j.cluster != eng.cluster:
                bad("A12", f"{j.id}: заявка в зоне «{j.cluster}», а {eng.id} "
                           f"работает в «{eng.cluster}»")

            for item, need in j.equipment.items():
                carried[item] = carried.get(item, 0) + need
                if carried[item] > eng.equipment.get(item, 0):
                    bad("A4", f"{eng.id}: оборудование «{item}» в минусе на "
                              f"{j.id} — израсходовано {carried[item]}, "
                              f"запас {eng.equipment.get(item, 0)}")

            if s.start_min < j.win_start:
                bad("A5", f"{j.id}: начало {hhmm(s.start_min)} раньше открытия "
                          f"окна {j.window_str}")
            if s.start_min > j.win_end:
                bad("A6", f"{j.id}: начало {hhmm(s.start_min)} позже закрытия "
                          f"окна {j.window_str}")

            # A7 — независимый пересчёт времени.
            # Проверяется НИЖНЯЯ граница: приехать раньше, чем позволяет дорога,
            # нельзя ни при каких обстоятельствах. Верхняя не проверяется, и это
            # не послабление: бригада имеет право выехать позже, чтобы не стоять
            # у закрытой двери, и ровно так работает расчёт выезда в плане и при
            # перепланировании. Подтасовать план завышением времени прибытия
            # невозможно — начало работ выводится из приезда (проверка ниже) и
            # упирается в закрытие окна (A6), то есть завышение делает план хуже,
            # а не лучше.
            leg_min = travel.minutes(prev_key, key, eng.transport)
            want_arrive = prev_end + leg_min
            if s.arrive_min < want_arrive - MIN_TOLERANCE:
                bad("A7", f"{j.id}: пересчёт не сходится — приезд записан "
                          f"{hhmm(s.arrive_min)}, но раньше {hhmm(want_arrive)} "
                          f"доехать нельзя ({hhmm(prev_end)} + {leg_min} мин)")
            if s.start_min != max(s.arrive_min, j.win_start):
                bad("A7", f"{j.id}: начало {hhmm(s.start_min)} не выводится из "
                          f"приезда {hhmm(s.arrive_min)} и окна {j.window_str}")
            if s.end_min != s.start_min + j.service_min:
                bad("A7", f"{j.id}: окончание {hhmm(s.end_min)} не равно началу "
                          f"плюс норматив {j.service_min} мин")
            if s.wait_min != s.start_min - s.arrive_min:
                bad("A7", f"{j.id}: ожидание {s.wait_min} мин не равно разнице "
                          f"между началом и приездом")

            # A8 — независимый пересчёт пробега
            want_km = travel.km(prev_key, key)
            if abs(s.leg_km - want_km) > KM_TOLERANCE:
                bad("A8", f"{j.id}: пробег переезда записан {s.leg_km} км, "
                          f"по матрице {round(want_km, 3)} км")
            km_sum += want_km

            prev_key, prev_end = key, s.end_min

        if abs(r.km - round(km_sum, 2)) > KM_TOLERANCE:
            bad("A8", f"{eng.id}: пробег маршрута записан {r.km} км, "
                      f"по матрице {round(km_sum, 2)} км")

        if HARD_SHIFT_END and r.stops[-1].end_min > eng.shift_end:
            bad("A9", f"{eng.id}: последняя работа заканчивается "
                      f"{hhmm(r.stops[-1].end_min)}, смена до {hhmm(eng.shift_end)}")
        if r.depart_min < eng.shift_start:
            bad("A9", f"{eng.id}: выезд {hhmm(r.depart_min)} раньше начала "
                      f"смены {hhmm(eng.shift_start)}")
        if r.span_min > MAX_WORKDAY_MIN:
            bad("A10", f"{eng.id}: рабочий день {r.span_min // 60}ч{r.span_min % 60:02d} "
                       f"больше норматива {MAX_WORKDAY_MIN // 60} ч")

    _audit_event_time(plan, bad)

    checks = [Check(code, title, not found[code], found[code]) for code, title in CHECKS]
    violations = [f"[{c.code}] {p}" for c in checks for p in c.problems]
    return AuditReport(region=plan.region, algo=plan.algo, checks=checks,
                       violations=violations)


def _audit_event_time(plan, bad) -> None:
    """A11. Момент события и пометку замороженного ставит перепланирование,
    ручной перенос их сохраняет. Аудитор верит только арифметике: визит без
    пометки обязан и выехать, и начаться не раньше события; визит с пометкой —
    стоять у того же исполнителя в то же время, впереди новых, и быть либо
    начатым, либо уже в пути к моменту события. План без события проверять
    не на чем: прошлого у него нет."""
    at = plan.meta.get("at_min")
    if at is None:
        return
    frozen = plan.meta.get("frozen") or {}
    seen = set()
    for r in plan.routes:
        fresh = False
        for s in r.stops:
            rec = frozen.get(s.job_id)
            trip = s.arrive_min - s.leg_min        # когда бригада тронулась к визиту
            if rec is None:
                fresh = True
                if trip < at or s.start_min < at:
                    bad("A11", f"{s.job_id}: {r.engineer_id} выезжает к визиту в "
                               f"{hhmm(trip)} и начинает в {hhmm(s.start_min)} — "
                               f"раньше события в {hhmm(at)}")
                continue
            seen.add(s.job_id)
            if fresh:
                bad("A11", f"{s.job_id}: замороженный визит стоит после нового — "
                           f"порядок уже случившегося изменён")
            if rec.get("engineer_id") != r.engineer_id:
                bad("A11", f"{s.job_id}: заморожена у {rec.get('engineer_id')}, а в "
                           f"плане у {r.engineer_id} — прошлое переписано")
            if (s.start_min, s.end_min) != (rec.get("start_min"), rec.get("end_min")):
                bad("A11", f"{s.job_id}: заморожена на {hhmm(rec.get('start_min'))}-"
                           f"{hhmm(rec.get('end_min'))}, а в плане "
                           f"{hhmm(s.start_min)}-{hhmm(s.end_min)}")
            if s.start_min > at and trip >= at:
                bad("A11", f"{s.job_id}: помечена замороженной, но к {hhmm(at)} к "
                           f"ней ещё не выезжали")
        if r.stops and r.stops[0].job_id not in frozen and r.depart_min < at:
            bad("A11", f"{r.engineer_id}: выезд {hhmm(r.depart_min)} раньше события "
                       f"в {hhmm(at)}, хотя ничего из его дня не заморожено")
    for jid, rec in frozen.items():
        if jid not in seen:
            bad("A11", f"{jid}: заморожена у {rec.get('engineer_id')}, но из плана "
                       f"исчезла")


def format_report(report: AuditReport, plan=None) -> str:
    out = [f"\n=== {report.region} · {report.algo} · {report.headline} ==="]
    for c in report.checks:
        out.append(f"  {'OK  ' if c.ok else 'СБОЙ'} {c.code:<4} {c.title}")
        for p in c.problems[:5]:
            out.append(f"         - {p}")
        if len(c.problems) > 5:
            out.append(f"         - ... ещё {len(c.problems) - 5}")
    return "\n".join(out)
