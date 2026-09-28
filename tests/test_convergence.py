# -*- coding: utf-8 -*-
"""Кривая «время счёта -> результат».

Кривая строится из одного прогона: решатель сообщает о каждом найденном плане.
Если сообщение разойдётся с тем, что решатель вернёт в конце, кривая будет
рассказывать про другой план — поэтому последний отклик сверяется с итогом.
"""
import importlib.util
import pathlib

from app.solver.engine import build_problem, solve

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "convergence.py"


def _tool():
    spec = importlib.util.spec_from_file_location("convergence", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_last_reported_plan_is_the_returned_plan():
    problem = build_problem("Югоцентр")
    seen = []
    plan = solve(problem, time_limit_s=3, on_solution=seen.append)
    assert seen, "решатель не сообщил ни об одном плане"
    best = min(seen, key=lambda s: s["cost"])
    assert best["engineers"] == plan.used_engineers
    assert best["assigned"] == plan.assigned
    assert abs(best["km"] - plan.total_km) < 0.05
    assert all(a["seconds"] <= b["seconds"] for a, b in zip(seen, seen[1:]))


def test_value_at_a_moment_is_the_best_found_by_then():
    tool = _tool()
    steps = [dict(seconds=0.1, engineers=12, km=150.0, assigned=45),
             dict(seconds=4.0, engineers=9, km=210.0, assigned=66),
             dict(seconds=50.0, engineers=8, km=205.0, assigned=66)]
    assert tool.at(steps, 0.05) is None
    assert tool.at(steps, 1) == steps[0]
    assert tool.at(steps, 10) == steps[1]
    assert tool.at(steps, 600) == steps[2]
    rows = tool.table({"А": dict(steps=steps), "Б": dict(steps=steps)}, 60)
    assert [r["seconds"] for r in rows] == [1, 2, 5, 10, 20, 30, 60]
    assert rows[3]["total"] == dict(engineers=18, km=420.0, assigned=132)
    assert rows[3]["mark"].startswith("событие дня")
