# -*- coding: utf-8 -*-
"""Базовый вариант распределения из ТЗ 2.3.

ТЗ описывает его дословно: «заявки назначаются по порядку первому доступному
исполнителю, удовлетворяющему обязательным ограничениям». Он нужен не как
запасной алгоритм, а как точка отсчёта: без него утверждение «мы экономим
бригады и километры» не с чем сравнить.
"""
from app.solver.baseline import solve_baseline
from app.solver.feasibility import check_static

from helpers import baseline_plan, problem, solver_plan


def test_baseline_follows_input_order():
    """Заявки назначаются в порядке строк первому подходящему исполнителю."""
    p = problem("Югоцентр")
    plan = solve_baseline(p)
    file_order = [j.id for j in p.rd.jobs]
    # порядок объезда внутри бригады совпадает с порядком назначения
    for r in plan.routes:
        ids = [s.job_id for s in r.stops]
        assert ids == sorted(ids, key=file_order.index)


def test_baseline_respects_hard_constraints():
    p = problem("Восток")
    plan = solve_baseline(p)
    engs = {e.id: e for e in p.engineers}
    jobs = {j.id: j for j in p.rd.jobs}
    for r in plan.routes:
        for s in r.stops:
            assert check_static(jobs[s.job_id], engs[r.engineer_id]).ok
            assert s.start_min >= jobs[s.job_id].win_start
            assert s.late_min == 0


def test_baseline_is_not_better_than_solver():
    """Если базовый не хуже — сравнение нечестное, и это ошибка модели.

    Порядок критериев ровно как в целевой функции и как сказал постановщик:
    СНАЧАЛА число выполненных заявок, ПОТОМ число исполнителей. Сравнивать
    в обратном порядке нельзя: жадный базовый вариант часто берёт МЕНЬШЕ
    бригад именно потому, что бросает заявки, и тест упал бы на корректном коде.
    """
    b, s = baseline_plan("Восток"), solver_plan("Восток")
    assert (-s.assigned, s.used_engineers) <= (-b.assigned, b.used_engineers)


def test_baseline_is_labelled():
    """Метка алгоритма едет в интерфейс и в выгрузку: перепутать планы нельзя."""
    assert baseline_plan("Югоцентр").algo == "baseline"


def test_every_job_is_either_assigned_or_explained():
    """Заявка не может просто исчезнуть: она либо в маршруте, либо в списке
    неназначенных с причиной. Иначе метрика «назначено» ни о чём не говорит."""
    p = problem("Юго-восток")
    plan = solve_baseline(p)
    seen = [s.job_id for r in plan.routes for s in r.stops]
    assert len(seen) == len(set(seen)), "заявка назначена дважды"
    assert set(seen) | {u.job_id for u in plan.unassigned} == {j.id for j in p.rd.jobs}
    for u in plan.unassigned:
        assert u.reason
