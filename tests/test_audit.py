# -*- coding: utf-8 -*-
"""Независимый аудит плана.

Аудитор — вторая правда о плане. Он пересчитывает расписание и пробег с нуля
по дорожной матрице и не знает ни про солвер, ни про предикат допустимости.
Смысл именно в независимости: если план и метрика посчитаны одним кодом, они
согласованы даже когда оба неверны.

Тесты устроены как подделка: берём заведомо корректный план, ломаем в нём
одно число руками и требуем, чтобы аудитор это увидел. Тест «аудитор
пропускает хороший план» ничего не доказывает — его прошла бы и функция,
возвращающая «всё хорошо».
"""
import copy

import pytest

from app.solver.audit import audit_plan

from helpers import baseline_plan, problem, solver_plan


@pytest.fixture(scope="module")
def clean():
    p = problem("Югоцентр")
    return p, solver_plan("Югоцентр")


def test_audit_passes_a_correct_plan(clean):
    p, plan = clean
    rep = audit_plan(plan, p)
    assert rep.passed == rep.total, rep.violations
    assert not rep.violations


def test_audit_catches_window_violation(clean):
    p, plan = clean
    bad = copy.deepcopy(plan)
    bad.routes[0].stops[0].start_min -= 120
    rep = audit_plan(bad, p)
    assert rep.passed < rep.total
    # Сверяем код проверки, а не падеж: «раньше открытия окнА» — это A5, и
    # привязывать тест к форме слова значит ломать его при правке формулировки.
    assert any(v.startswith("[A5]") and "окн" in v for v in rep.violations), rep.violations


def test_audit_catches_mileage_mismatch():
    p, plan = problem("Восток"), solver_plan("Восток")
    bad = copy.deepcopy(plan)
    bad.routes[0].stops[-1].leg_km += 5.0
    rep = audit_plan(bad, p)
    assert any(v.startswith("[A8]") and "пробег" in v for v in rep.violations), rep.violations


def test_audit_catches_teleportation(clean):
    """Сдвиг начала работ вперёд без сдвига приезда — бригада успевает
    приехать раньше, чем выехала. Это ловит проверка независимого пересчёта
    времени, а не проверка окон."""
    p, plan = clean
    bad = copy.deepcopy(plan)
    route = next(r for r in bad.routes if len(r.stops) >= 2)
    route.stops[1].arrive_min = route.stops[0].end_min       # плечо стало нулевым
    rep = audit_plan(bad, p)
    assert any(v.startswith("[A7]") for v in rep.violations), rep.violations


def test_audit_catches_duplicate_assignment(clean):
    p, plan = clean
    bad = copy.deepcopy(plan)
    stolen = bad.routes[0].stops[0]
    victim = next(r for r in bad.routes if r.stops and r is not bad.routes[0])
    victim.stops.append(copy.deepcopy(stolen))
    rep = audit_plan(bad, p)
    assert any(v.startswith("[A1]") and "дважды" in v for v in rep.violations), rep.violations


def test_audit_catches_equipment_overdraft(clean):
    """Оборудование выдаётся в офисе на весь день. Взять больше, чем взял
    с собой, нельзя — проверка идёт нарастающим итогом по маршруту.

    Ломаем не план, а запас бригады: план остаётся тем же самым, но железа
    под него больше нет. Аудитор обязан это заметить, даже не видя солвера.
    """
    p, plan = clean
    jobs = {j.id: j for j in p.rd.jobs}
    route = next(r for r in plan.routes
                 if any(jobs[s.job_id].equipment for s in r.stops))
    item = next(k for s in route.stops for k in jobs[s.job_id].equipment)
    starved = copy.deepcopy(p)
    for e in starved.engineers:
        if e.id == route.engineer_id:
            e.equipment = dict(e.equipment, **{item: 0})
    rep = audit_plan(plan, starved)
    assert any(v.startswith("[A4]") and "оборудован" in v for v in rep.violations), rep.violations


def test_audit_passes_baseline_too():
    """Базовый вариант обязан проходить тот же аудит — доказательство, что мы
    его не ослабили ради выигрышного сравнения."""
    rep = audit_plan(baseline_plan("Восток"), problem("Восток"))
    assert rep.passed == rep.total, rep.violations


def test_audit_does_not_import_the_solver():
    """Независимость — не комментарий, а проверяемое свойство: аудитор не имеет
    права опираться ни на ortools, ни на предикат допустимости, иначе он
    подтверждает план тем же кодом, который его построил."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "backend/app/solver/audit.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported |= {f"{node.module or ''}.{a.name}" for a in node.names}
    # metrics тоже под запретом: replay_route СТРОИТ расписание, и проверять им
    # же построенное — это согласовать код сам с собой. Аудитор не строит
    # расписание заново, он сверяет арифметику записанного по дорожной матрице,
    # поэтому второй реализации replay_route здесь не появляется.
    banned = ("ortools", "feasibility", "engine", "baseline", "metrics")
    for name in imported:
        assert not any(b in name for b in banned), f"аудитор импортирует {name}"


# --- A11: после события ничего не начинается в прошлом ----------------------

AT = 15 * 60 + 40


@pytest.fixture(scope="module")
def after_event():
    """План после события: отмена одной заявки в 15:40. У такого плана есть
    момент события и пометка замороженного — то, по чему аудитор судит о
    прошлом, ничего не зная о том, как план получен."""
    from app.solver.replan import Event, apply_event
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    victim = max(plan.routes, key=lambda r: len(r.stops))
    target = next(s.job_id for s in reversed(victim.stops) if s.start_min > AT)
    new, _ = apply_event(plan, p, Event(kind="cancel", at_min=AT, job_id=target),
                         time_limit_s=2)
    return p, new


def test_audit_has_twelve_checks():
    from app.solver.audit import CHECKS
    assert [code for code, _ in CHECKS] == [f"A{i}" for i in range(1, 13)]


def test_plan_after_event_passes_all_twelve(after_event):
    p, plan = after_event
    assert plan.meta["at_min"] == AT and plan.meta["frozen"]
    rep = audit_plan(plan, p)
    assert rep.passed == rep.total == 12, rep.violations


def test_audit_catches_new_work_put_into_the_past(after_event):
    """Визит после события, поставленный в прошлое: выезд и начало раньше
    момента события."""
    p, plan = after_event
    bad = copy.deepcopy(plan)
    frozen = bad.meta["frozen"]
    s = next(s for r in bad.routes for s in r.stops if s.job_id not in frozen)
    shift = s.start_min - (AT - 60)
    s.arrive_min, s.start_min, s.end_min = (s.arrive_min - shift, s.start_min - shift,
                                            s.end_min - shift)
    rep = audit_plan(bad, p)
    assert any(v.startswith("[A11]") and "раньше события" in v
               for v in rep.violations), rep.violations


def test_audit_catches_the_past_given_to_another_engineer(after_event):
    """Замороженная заявка у другой бригады — прошлое переписано: работу,
    законченную в 10:30, в 15:40 отдали свежей бригаде."""
    p, plan = after_event
    bad = copy.deepcopy(plan)
    frozen = bad.meta["frozen"]
    donor = next(r for r in bad.routes if r.stops and r.stops[0].job_id in frozen)
    taker = next(r for r in bad.routes if r is not donor)
    taker.stops.append(donor.stops.pop(0))
    rep = audit_plan(bad, p)
    assert any(v.startswith("[A11]") and "прошлое переписано" in v
               for v in rep.violations), rep.violations


def test_audit_catches_a_shifted_frozen_visit(after_event):
    p, plan = after_event
    bad = copy.deepcopy(plan)
    s = next(s for r in bad.routes for s in r.stops if s.job_id in bad.meta["frozen"])
    s.start_min, s.end_min, s.wait_min = s.start_min + 5, s.end_min + 5, s.wait_min + 5
    rep = audit_plan(bad, p)
    assert any(v.startswith("[A11]") and "заморожена на" in v
               for v in rep.violations), rep.violations


# --- A12: бригада работает в своей зоне --------------------------------------

def test_audit_catches_a_foreign_zone(clean):
    """Назначение в чужую зону. Ломаем не план, а зону бригады: план тот же,
    но бригада теперь «работает в Кашире». Аудитор обязан это увидеть, не
    спрашивая ни солвер, ни предикат допустимости."""
    p, plan = clean
    route = next(r for r in plan.routes if r.stops)
    moved = copy.copy(p)
    moved.engineers = [copy.copy(e) for e in p.engineers]
    for e in moved.engineers:
        if e.id == route.engineer_id:
            e.cluster = "Кашира-Ступино"
    rep = audit_plan(plan, moved)
    assert any(v.startswith("[A12]") and route.engineer_id in v
               for v in rep.violations), rep.violations
