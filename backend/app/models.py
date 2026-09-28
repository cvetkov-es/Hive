# -*- coding: utf-8 -*-
"""Сущности предметной области. Время везде — минуты от полуночи планового дня."""
from __future__ import annotations
from dataclasses import dataclass, field


def hhmm(m: int) -> str:
    if m is None:
        return "--:--"
    return "%02d:%02d" % (int(m) // 60 % 24, int(m) % 60)


@dataclass
class Job:
    """Заявка."""
    id: str
    address: str
    lat: float
    lon: float
    geo_level: int            # 0 = дом, >0 = улица/город (погрешность растёт)
    district: str             # поле «Район» из выгрузки (иногда технология, не география)
    bk: str                   # тип заявки в Beekeeper
    hd: str                   # тип заявки в HelpDesk (сторонняя система)
    skill: str                # требуемый навык: local | connect | emergency
    service_min: int          # чистое время на объекте (норматив минус дорога)
    equipment: dict           # {'router': 1}
    priority: int             # 1 авария, 2 подключение, 3 обычная
    win_start: int
    win_end: int              # окно ПРИБЫТИЯ, не окончания работ
    floating_window: bool     # окно на все сутки (аварии)
    cluster: str              # географическая зона внутри региона
    requires_transport: str | None = None   # жёсткое требование к типу транспорта
    release_min: int | None = None          # для аварий: момент фактического поступления

    @property
    def norm_min(self) -> int:
        from .config import NORM_ROAD_MIN
        return self.service_min + NORM_ROAD_MIN

    @property
    def window_str(self) -> str:
        return f"{hhmm(self.win_start)}-{hhmm(self.win_end)}"


@dataclass
class Depot:
    """Стартовая точка. Офис региона либо «дом» бригады в удалённом городе."""
    key: str
    name: str
    lat: float
    lon: float
    cluster: str
    is_office: bool = False


@dataclass
class Engineer:
    """Бригада. По уточнению постановщика бригада = один человек."""
    id: str
    name: str
    depot: str
    cluster: str
    skills: set
    transport: str
    shift_start: int
    shift_end: int
    equipment: dict = field(default_factory=dict)

    def has_skill(self, s: str) -> bool:
        return s in self.skills

    @property
    def shift_str(self) -> str:
        return f"{hhmm(self.shift_start)}-{hhmm(self.shift_end)}"


@dataclass
class RegionData:
    """Один регион = одна независимая задача планирования."""
    name: str
    office_address: str
    depots: dict           # key -> Depot
    jobs: list             # list[Job]
    engineers: list = field(default_factory=list)

    def job_by_id(self, jid: str):
        for j in self.jobs:
            if j.id == jid:
                return j
        return None


@dataclass
class Stop:
    """Один визит в маршруте бригады."""
    job_id: str
    seq: int
    arrive_min: int        # приезд (может быть раньше окна — тогда ждём)
    start_min: int         # начало работ, не раньше win_start
    end_min: int
    wait_min: int
    late_min: int          # опоздание относительно win_end
    leg_km: float          # плечо от предыдущей точки
    leg_min: int


@dataclass
class Route:
    engineer_id: str
    stops: list            # list[Stop]
    depart_min: int = 0    # фактический выезд из депо, а не начало окна доступности

    @property
    def km(self) -> float:
        return round(sum(s.leg_km for s in self.stops), 2)

    @property
    def n_jobs(self) -> int:
        return len(self.stops)

    @property
    def span_min(self) -> int:
        """Длительность рабочего дня: от выезда до окончания последней работы."""
        return (self.stops[-1].end_min - self.depart_min) if self.stops else 0


@dataclass
class Unassigned:
    job_id: str
    code: str              # машинный код причины
    reason: str            # формулировка для диспетчера
    detail: str = ""


@dataclass
class Plan:
    region: str
    algo: str              # 'solver' | 'baseline' | 'control' | 'greedy'
    routes: list           # list[Route]
    unassigned: list       # list[Unassigned]
    meta: dict = field(default_factory=dict)

    @property
    def used_engineers(self) -> int:
        """Обязательная метрика №1: уникальные исполнители с >=1 заявкой."""
        return sum(1 for r in self.routes if r.stops)

    @property
    def total_km(self) -> float:
        """Обязательная метрика №2: суммарный пробег по плану."""
        return round(sum(r.km for r in self.routes), 2)

    @property
    def assigned(self) -> int:
        return sum(r.n_jobs for r in self.routes)

    @property
    def late_jobs(self) -> int:
        return sum(1 for r in self.routes for s in r.stops if s.late_min > 0)

    @property
    def travel_min(self) -> int:
        return sum(s.leg_min for r in self.routes for s in r.stops)
