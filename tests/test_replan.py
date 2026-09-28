# -*- coding: utf-8 -*-
"""Перепланирование после события (ТЗ 2.1.6, Р4).

Одно событие обязательно, делаем три: срочная заявка, отмена, недоступность
исполнителя.

Главное свойство здесь не «план пересчитался», а «прошлое не переписалось».
Пересчитать день с нуля умеет и первый попавшийся вызов солвера — но тогда
бригада, которая в 15:40 уже отработала пять заявок, в новом плане окажется
утром в другом районе. Это не план, это его подмена.
"""
import pytest

from app.config import PRIO_EMERGENCY, PRIO_ROUTINE, REGIONS
from app.solver.audit import audit_plan
from app.solver.replan import Event, apply_event, make_demo_emergency, problem_with_event

from helpers import (find_route, make_emergency_job, problem, short_fleet_plan,
                     solver_plan)

AT = 15 * 60 + 40          # событие в 15:40


def _frozen_stops(plan, at_min):
    """Всё, к чему бригада уже приступила: переносу не подлежит."""
    return [(r.engineer_id, s) for r in plan.routes for s in r.stops
            if s.start_min <= at_min]


def test_completed_jobs_are_frozen():
    p, plan = problem("Юго-восток"), solver_plan("Юго-восток")
    ev = Event(kind="urgent", at_min=AT, job=make_emergency_job(at_min=AT))
    new, diff = apply_event(plan, p, ev, time_limit_s=10)
    for eng_id, s in _frozen_stops(plan, ev.at_min):
        r_new = find_route(new, eng_id)
        same = [t for t in r_new.stops if t.job_id == s.job_id]
        assert same, f"{s.job_id}: выполненная заявка исчезла из плана"
        assert same[0].start_min == s.start_min, \
            "выполненная заявка переехала — бригада телепортировалась"
        assert same[0].leg_km == s.leg_km


def test_urgent_job_is_actually_placed():
    """Аварию нельзя просто «принять к сведению»: она либо в маршруте, либо
    в неназначенных с причиной."""
    p, plan = problem("Юго-восток"), solver_plan("Юго-восток")
    job = make_emergency_job(at_min=AT)
    new, diff = apply_event(plan, p, Event(kind="urgent", at_min=AT, job=job),
                            time_limit_s=10)
    placed = [s for r in new.routes for s in r.stops if s.job_id == job.id]
    if placed:
        assert placed[0].start_min >= job.win_start
        assert placed[0].start_min <= job.win_end
    else:
        assert any(u.job_id == job.id and u.reason for u in new.unassigned)


def test_engineer_unavailable_reassigns_only_future():
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    victim = max(plan.routes, key=lambda r: len(r.stops)).engineer_id
    ev = Event(kind="unavailable", at_min=AT, engineer_id=victim)
    new, diff = apply_event(plan, p, ev, time_limit_s=10)
    r_new = find_route(new, victim)
    for s in r_new.stops:
        assert s.start_min <= ev.at_min, \
            "выбывшей бригаде оставили работу после момента события"
    old = find_route(plan, victim)
    lost = {s.job_id for s in old.stops} - {s.job_id for s in r_new.stops}
    seen = {s.job_id for r in new.routes for s in r.stops} | \
           {u.job_id for u in new.unassigned}
    assert lost <= seen, "заявки выбывшей бригады просто исчезли"


def test_cancelled_job_frees_capacity():
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    victim = max(plan.routes, key=lambda r: len(r.stops))
    target = next(s.job_id for s in victim.stops if s.start_min > AT)
    ev = Event(kind="cancel", at_min=AT, job_id=target)
    new, diff = apply_event(plan, p, ev, time_limit_s=10)
    assert all(s.job_id != target for r in new.routes for s in r.stops), \
        "отменённая заявка осталась в маршруте"
    assert all(u.job_id != target for u in new.unassigned), \
        "отменённая заявка попала в неназначенные — её никто не ждёт"
    assert new.total_km <= plan.total_km + 0.01


def test_replanned_plan_passes_the_same_audit():
    """Аудит не делает скидку на то, что план перестроенный."""
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    ev = Event(kind="urgent", at_min=AT,
               job=make_emergency_job(at_min=AT, lat=55.6300, lon=37.6100))
    new, _ = apply_event(plan, p, ev, time_limit_s=10)
    rep = audit_plan(new, _problem_of(new, p, ev))
    assert rep.passed == rep.total, rep.violations


def _problem_of(new_plan, p, ev):
    """Задача, в терминах которой построен новый план (со срочной заявкой)."""
    from app.solver.replan import problem_with_event
    return problem_with_event(p, ev)


def test_diff_names_what_changed():
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    victim = max(plan.routes, key=lambda r: len(r.stops))
    target = next(s.job_id for s in victim.stops if s.start_min > AT)
    new, diff = apply_event(plan, p, Event(kind="cancel", at_min=AT,
                                           job_id=target), time_limit_s=10)
    assert diff.frozen > 0, "в 15:40 не может быть ноль выполненных заявок"
    assert diff.metrics["total_km"]["before"] == plan.total_km
    assert diff.metrics["total_km"]["after"] == new.total_km
    assert diff.metrics["used_engineers"]["before"] == plan.used_engineers
    for m in diff.moved:
        assert m["job_id"] and m["from"] and m["to"] and m["from"] != m["to"]
    for d in diff.dropped:
        assert d["reason"], "вытесненная заявка без причины"
    # Отменённая заявка ушла из плана — это изменение, даже если больше ничего
    # не сдвинулось: панель не имеет права написать «план не изменился».
    assert diff.cancelled == [{"job_id": target, "from": victim.engineer_id}]
    assert not diff.is_quiet


def test_stability_keeps_most_assignments():
    """Штраф за перетасовку существует затем, чтобы разница «до/после» была
    читаемой. Если после отмены одной заявки переезжает половина региона,
    диспетчер новому плану не поверит."""
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    victim = max(plan.routes, key=lambda r: len(r.stops))
    target = next(s.job_id for s in victim.stops if s.start_min > AT)
    new, diff = apply_event(plan, p, Event(kind="cancel", at_min=AT,
                                           job_id=target), time_limit_s=10)
    movable = sum(1 for r in plan.routes for s in r.stops if s.start_min > AT)
    assert len(diff.moved) <= movable * 0.25, \
        f"переехало {len(diff.moved)} из {movable} — штраф за перетасовку не работает"


# --- прошлое после события ---------------------------------------------------

def _ordinary_job(job_id, lat, lon, window, cluster="Москва", service=40):
    """Обычная новая заявка: локальные работы, приоритет «Обычная»."""
    from app.models import Job
    return Job(id=job_id, address="ул. Новая, 2 (введён диспетчером)", lat=lat,
               lon=lon, geo_level=0, district="", bk="Локальная заявка", hd="",
               skill="local", service_min=service, equipment={},
               priority=PRIO_ROUTINE, win_start=window[0], win_end=window[1],
               floating_window=False, cluster=cluster)


def _nothing_in_the_past(plan, at):
    frozen = plan.meta["frozen"]
    for r in plan.routes:
        for s in r.stops:
            if s.job_id in frozen:
                continue
            assert s.arrive_min - s.leg_min >= at and s.start_min >= at, \
                f"{r.engineer_id} {s.job_id}: выезд {s.arrive_min - s.leg_min}, " \
                f"начало {s.start_min} при событии {at}"
        if r.stops and r.stops[0].job_id not in frozen:
            assert r.depart_min >= at, f"{r.engineer_id}: выезд раньше события"


def test_nobody_departs_before_the_event():
    """Юго-восток, выбыл BR-ЮГО-01 в 15:40: свободная бригада не получает
    выезд 14:35 — поездку, которой не было. Всё незамороженное начинается не
    раньше события."""
    p, plan = problem("Юго-восток"), solver_plan("Юго-восток")
    victim = max(plan.routes, key=lambda r: len(r.stops)).engineer_id
    new, _ = apply_event(plan, p, Event(kind="unavailable", at_min=AT,
                                        engineer_id=victim), time_limit_s=3)
    _nothing_in_the_past(new, AT)
    rep = audit_plan(new, p)
    assert rep.passed == rep.total, rep.violations


def test_window_closed_before_the_event_is_refused_not_stretched():
    """Срочная заявка с окном, закрытым к 15:40, не ставится на утро, и её окно
    не растягивается до момента события. Выполнить её после события нельзя, и
    причина обязана сказать именно это."""
    p, plan = problem("Восток"), solver_plan("Восток")
    job = make_emergency_job("SROCH-1", at_min=10 * 60, lat=55.78, lon=37.72)
    new, diff = apply_event(plan, p, Event(kind="urgent", at_min=AT, job=job),
                            time_limit_s=2)
    assert all(s.job_id != job.id for r in new.routes for s in r.stops)
    u = next(u for u in new.unassigned if u.job_id == job.id)
    assert u.code == "WINDOW_CLOSED_BEFORE_EVENT" and "закрылось" in u.reason, u
    assert diff.rejected and diff.rejected[0]["job_id"] == job.id
    assert not diff.is_quiet, "непринятая авария — это изменение, а не тишина"


def test_short_fleet_event_keeps_every_window():
    """Парк урезан до шести, событие в 15:40: каждая заявка начинается внутри
    своего окна. Показательный случай — 40698 с окном 12:00-14:00."""
    from app.solver.engine import Problem, subset_fleet
    p = problem("Юго-восток")
    reduced = Problem(p.rd, subset_fleet(p.engineers, 6), p.travel)
    plan = short_fleet_plan("Юго-восток", 6, 5)
    ev = Event(kind="urgent", at_min=AT, job=make_demo_emergency(reduced, AT))
    new, _ = apply_event(plan, reduced, ev, time_limit_s=3)
    assert new.late_jobs == 0
    _nothing_in_the_past(new, AT)
    rep = audit_plan(new, problem_with_event(reduced, ev))
    assert rep.passed == rep.total, rep.violations


# --- два режима новой заявки -------------------------------------------------

def test_mode_follows_the_curator():
    """Куратор, 22.09: обычная заявка встаёт в свободный интервал, авария вправе
    перепланировать остаток дня. Авария — навык или приоритет «Срочная»."""
    from dataclasses import replace
    ordinary = _ordinary_job("N-1", 55.66, 37.62, (16 * 60, 18 * 60))
    assert Event(kind="urgent", at_min=AT, job=ordinary).mode == "insert"
    assert Event(kind="urgent", at_min=AT,
                 job=replace(ordinary, priority=PRIO_EMERGENCY)).mode == "replan"
    assert Event(kind="urgent", at_min=AT, job=make_emergency_job()).mode == "replan"
    assert Event(kind="cancel", at_min=AT, job_id="1").mode == "replan"
    assert Event(kind="unavailable", at_min=AT, engineer_id="E").mode == "replan"


def test_ordinary_job_is_inserted_without_rebuilding_the_plan():
    """Обычная заявка «не должна полностью перестраивать уже сформированный
    план»: чужие маршруты не меняются вовсе, у своей бригады сохраняется
    порядок и всё, что до события, а новая работа — не раньше события."""
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    job = _ordinary_job("NEW-1", 55.66, 37.62, (16 * 60, 18 * 60))
    ev = Event(kind="urgent", at_min=AT, job=job)
    new, diff = apply_event(plan, p, ev)
    assert diff.mode == "insert" and "вставка" in diff.event
    assert [a["job_id"] for a in diff.added] == [job.id]
    assert not (diff.moved or diff.dropped or diff.resequenced or diff.rejected)

    target = diff.added[0]["to"]
    before = {r.engineer_id: r for r in plan.routes}
    for r in new.routes:
        was = [(s.job_id, s.start_min, s.end_min) for s in before[r.engineer_id].stops]
        now = [(s.job_id, s.start_min, s.end_min) for s in r.stops]
        if r.engineer_id != target:
            assert now == was, f"{r.engineer_id}: вставка тронула чужой маршрут"
            continue
        assert [x[0] for x in now if x[0] != job.id] == [x[0] for x in was], \
            "у бригады, принявшей заявку, сменился порядок"
        for s in r.stops:
            if s.job_id in new.meta["frozen"]:
                assert (s.job_id, s.start_min, s.end_min) in was
        placed = next(s for s in r.stops if s.job_id == job.id)
        assert job.win_start <= placed.start_min <= job.win_end and placed.start_min >= AT
    _nothing_in_the_past(new, AT)
    rep = audit_plan(new, problem_with_event(p, ev))
    assert rep.passed == rep.total == 12, rep.violations


def test_ordinary_job_without_a_place_is_refused_with_a_reason():
    """Места нет — заявка неназначена с конкретной причиной и именем бригады,
    а план не тронут."""
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    job = _ordinary_job("NEW-2", 54.84, 38.17, (16 * 60, 18 * 60),
                        cluster="Кашира-Ступино")
    new, diff = apply_event(plan, p, Event(kind="urgent", at_min=AT, job=job))
    assert diff.mode == "insert" and not diff.is_quiet
    assert [x["job_id"] for x in diff.rejected] == [job.id]
    assert diff.rejected[0]["code"] == "OTHER_CLUSTER"
    assert "BR-" in diff.rejected[0]["detail"], diff.rejected
    assert [[s.job_id for s in r.stops] for r in new.routes] == \
           [[s.job_id for s in r.stops] for r in plan.routes]


def test_insertion_is_not_reported_as_reordering():
    """Вставка третьей сдвигает номера последующих визитов, но не их порядок.
    Считать их переставленными — докладывать о перестройке, которой не было."""
    from app.models import Plan, Route, Stop
    from app.solver.replan import _resequenced

    def day(ids):
        return Plan("R", "x", [Route("E", [Stop(j, i, 0, 0, 0, 0, 0, 0.0, 0)
                                           for i, j in enumerate(ids, 1)])], [])

    assert _resequenced(day("abcd"), day("axbcd")) == []
    assert [m["job_id"] for m in _resequenced(day("abcd"), day("bcda"))] == ["a"]


# --- засев и цепочки событий -------------------------------------------------

@pytest.mark.parametrize("region", REGIONS)
def test_warm_start_is_accepted(region):
    """Засев прежним планом — условие «минимальных изменений». В него идут
    индексы модели, а не номера узлов матрицы: иначе при старте бригад с точки
    последней заявки он молча отбрасывается («Vehicle 1 is not allowed at
    index 62»)."""
    from app.api import artifacts
    p = problem(region)
    plan = artifacts.load_plan(region, "solver", p)
    busiest = max(plan.routes, key=lambda r: len(r.stops))
    later = [s.job_id for s in busiest.stops if s.start_min > AT]
    for ev in (Event(kind="urgent", at_min=AT, job=make_demo_emergency(p, AT)),
               Event(kind="cancel", at_min=AT, job_id=later[-1]),
               Event(kind="unavailable", at_min=AT, engineer_id=busiest.engineer_id)):
        new, _ = apply_event(plan, p, ev, time_limit_s=1)
        assert new.meta.get("warm_start") is True, (region, ev.kind)


def test_chained_events_remember_who_left_and_what_was_cancelled():
    """Второе событие применяется к результату первого. План после события
    помнит выбывших и отменённых: иначе следующее событие вернёт в работу
    бригаду, выбывшую в первом, и воскресит отменённую заявку."""
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    ranked = sorted(plan.routes, key=lambda r: -len(r.stops))
    gone = ranked[0].engineer_id
    cancelled = next(s.job_id for r in ranked[1:] for s in reversed(r.stops)
                     if s.start_min > AT + 60)
    p1, _ = apply_event(plan, p, Event(kind="cancel", at_min=AT, job_id=cancelled),
                        time_limit_s=2)
    p2, _ = apply_event(p1, p, Event(kind="unavailable", at_min=AT + 20,
                                     engineer_id=gone), time_limit_s=2)
    job = _ordinary_job("NEW-3", 55.66, 37.62, (17 * 60, 20 * 60))
    p3, d3 = apply_event(p2, p, Event(kind="urgent", at_min=AT + 40, job=job))
    for later in (p2, p3):
        assert all(s.job_id != cancelled for r in later.routes for s in r.stops)
        assert all(u.job_id != cancelled for u in later.unassigned)
        assert all(s.start_min <= AT + 20 for s in find_route(later, gone).stops)
    assert d3.added and d3.added[0]["to"] != gone
    assert p3.meta["cancelled"] == [cancelled]
    assert gone in p3.meta["blocked_engineers"]
    assert len(p3.meta["events"]) == 3
