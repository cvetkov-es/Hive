# -*- coding: utf-8 -*-
"""Кэш тяжёлых объектов для тестов.

Сборка задачи читает CSV, геокэш и дорожную матрицу, а прогон солвера занимает
секунды. Без кэша набор тестов вырастает до минут, и его перестают запускать.
Кэш безопасен ровно потому, что ни один тест не меняет задачу на месте: там,
где нужен изменённый план, тесты работают с copy.deepcopy готового плана.
"""
from __future__ import annotations
import functools

from app.solver.engine import build_problem, solve, solve_with_fleet

SOLVER_TIME_S = 10


@functools.lru_cache(maxsize=None)
def problem(region: str):
    return build_problem(region)


@functools.lru_cache(maxsize=None)
def solver_plan(region: str, time_limit_s: int = SOLVER_TIME_S):
    return solve(problem(region), time_limit_s=time_limit_s)


@functools.lru_cache(maxsize=None)
def baseline_plan(region: str):
    from app.solver.baseline import solve_baseline
    return solve_baseline(problem(region))


@functools.lru_cache(maxsize=None)
def short_fleet_plan(region: str, keep: int, time_limit_s: int = SOLVER_TIME_S):
    return solve_with_fleet(problem(region), keep, time_limit_s=time_limit_s)


def make_emergency_job(job_id: str = "AVARIA-1", at_min: int = 15 * 60 + 40,
                       lat: float = 55.6450, lon: float = 37.7600,
                       cluster: str = "Москва"):
    """Срочная заявка по адресу, которого НЕТ в дорожной матрице.

    Так и будет на демонстрации: авария приходит в середине дня по адресу,
    которого не было в выгрузке. Если бы тест брал координаты уже известной
    точки, он молча обошёл бы весь механизм достройки матрицы в рантайме.
    """
    from app.config import PRIO_EMERGENCY
    from app.io.normatives import SRV_EMERGENCY
    from app.models import Job
    return Job(id=job_id, address="ул. Новая, 1 (введён диспетчером)",
               lat=lat, lon=lon, geo_level=0, district="", bk="Авария", hd="Авария",
               skill="emergency", service_min=SRV_EMERGENCY, equipment={},
               priority=PRIO_EMERGENCY, win_start=at_min, win_end=at_min + 120,
               floating_window=False, cluster=cluster, requires_transport="car",
               release_min=at_min)


def find_route(plan, engineer_id):
    return next((r for r in plan.routes if r.engineer_id == engineer_id), None)
