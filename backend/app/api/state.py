# -*- coding: utf-8 -*-
"""Реестр планов и кэш задач.

Планы ИММУТАБЕЛЬНЫ и хранятся по идентификатору. Это не оптимизация, а условие
работоспособности сравнения «до и после»: перепланирование обязано оставить
предыдущий план на месте, иначе показывать разницу не с чем. И пересчитать тот
же регион заново вместо хранения нельзя — солвер эвристический, второй запуск
даст другой план, и «разница до и после» превратится в разницу между двумя
разными случайностями.

Задачи (регион + парк + матрица) кэшируются: чтение CSV, геокэша и дорожной
матрицы занимает секунды, а меняться внутри процесса они не могут — данные
лежат в файлах и готовятся офлайн.

Дамп каждого плана в runs/<id>.json — чтобы после демонстрации можно было
предъявить ровно тот план, который показывали, а не пересчитанный заново.
Хранятся последние RUNS_MAX: стенд открыт в интернет, и открытие страницы в
цикле иначе заполнило бы его диск.
"""
from __future__ import annotations
import json
import threading
import uuid
from collections import OrderedDict

from ..config import REGIONS, ROOT

RUNS = ROOT / "runs"
MAX_PLANS = 200            # демонстрация живёт минуты; память не резиновая
RUNS_MAX = 1000            # около 40 МБ дампов; старые удаляются


class PlanRegistry:
    def __init__(self, max_plans: int = MAX_PLANS):
        self._plans: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_plans

    def put(self, plan, problem, extra: dict | None = None) -> str:
        plan_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._plans[plan_id] = (plan, problem, dict(extra or {}))
            while len(self._plans) > self._max:
                self._plans.popitem(last=False)
        self._dump(plan_id, plan, problem)
        return plan_id

    def get(self, plan_id: str):
        with self._lock:
            item = self._plans.get(plan_id)
        if item is None:
            raise KeyError(plan_id)
        return item

    def __contains__(self, plan_id: str) -> bool:
        return plan_id in self._plans

    def _dump(self, plan_id: str, plan, problem) -> None:
        from ..io.export import plan_to_json
        try:
            RUNS.mkdir(parents=True, exist_ok=True)
            (RUNS / f"{plan_id}.json").write_text(
                json.dumps(plan_to_json(plan, problem), ensure_ascii=False,
                           indent=2), encoding="utf-8")
            _prune_runs()
        except OSError:
            # Нет прав на запись — это не повод уронить ответ пользователю.
            pass


def _prune_runs() -> None:
    """Оставить в runs/ последние RUNS_MAX дампов. Два запроса могут чистить
    одновременно, поэтому уже удалённый соседом файл — не ошибка."""
    stamped = []
    for f in RUNS.glob("*.json"):
        try:
            stamped.append((f.stat().st_mtime_ns, f))
        except FileNotFoundError:
            continue
    stamped.sort()
    for _, f in stamped[:max(0, len(stamped) - RUNS_MAX)]:
        f.unlink(missing_ok=True)


_problems: dict = {}
_problem_lock = threading.Lock()


def get_problem(region: str):
    if region not in REGIONS:
        raise KeyError(region)
    with _problem_lock:
        if region not in _problems:
            from ..solver.engine import build_problem
            _problems[region] = build_problem(region)
        return _problems[region]


registry = PlanRegistry()
