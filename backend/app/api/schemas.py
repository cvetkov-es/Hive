# -*- coding: utf-8 -*-
"""Схемы запросов и ответов.

Имена полей результата — ровно как в ТЗ 2.4.2, чтобы эксперт узнавал их без
перевода: по исполнителю упорядоченный список заявок с плановым временем
прибытия и начала работ и пробегом; по заявке — исполнитель либо статус «не
назначена» с явной причиной; по плану — количество задействованных
исполнителей, пробег по каждому и суммарный.

Время наружу всегда HH:MM (рекомендация ТЗ 2.4), внутри — минуты от полуночи.
Граница проходит здесь: дальше в модель минуты, ближе к экрану строки.
"""
from __future__ import annotations
import re

from pydantic import BaseModel, Field, field_validator

from ..config import REGIONS

MAX_TIME_LIMIT_S = 30        # интерфейс просит не больше 20 с
# Текст новой заявки попадает в подсказки карты как HTML. Номер — короткий
# идентификатор, адрес — обычный текст: разметке в них взяться неоткуда.
JOB_ID = re.compile(r"[\w.-]{1,40}")
ADDRESS_MAX = 200
NO_MARKUP = r"^[^<>]*$"

# Время в запросах — строго ЧЧ:ММ от 00:00 до 23:59: иначе «abc» стало бы
# ответом 500, а «99:99» молча превратилось бы в 6039-ю минуту суток и
# заморозило бы весь день.
HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
_HHMM = re.compile(HHMM)
JOB_TIME_FIELDS = ("window_start", "window_end")


class PlanRequest(BaseModel):
    region: str = Field(..., description="один из: " + ", ".join(REGIONS))
    algo: str = Field("solver", pattern="^(solver|baseline)$")
    time_limit_s: int = Field(20, ge=1, le=MAX_TIME_LIMIT_S)
    keep: int = Field(0, ge=0, description="урезать парк до N бригад; 0 — весь")
    recompute: bool = Field(
        False, description="считать живьём вместо эталона из artifacts/")


class EventRequest(BaseModel):
    plan_id: str
    kind: str = Field(..., pattern="^(urgent|cancel|unavailable)$")
    at: str = Field("15:40", pattern=HHMM,
                    description="время события, ЧЧ:ММ от 00:00 до 23:59")
    job_id: str | None = None
    engineer_id: str | None = None
    job: dict | None = Field(None, description="полный набор полей новой заявки "
                                               "в формате ТЗ 2.4; окно — ЧЧ:ММ")
    address: str | None = Field(None, max_length=ADDRESS_MAX, pattern=NO_MARKUP,
                                description="адрес аварии; координаты "
                                            "берутся из геокэша или у геокодера")
    time_limit_s: int = Field(10, ge=1, le=MAX_TIME_LIMIT_S)

    @field_validator("job")
    @classmethod
    def _job_times(cls, job):
        """Окно новой заявки проверяется той же меркой, что и момент события:
        это тоже время в запросе, и «25:00» в нём такая же ошибка ввода."""
        if job is None:
            return job
        for key in JOB_TIME_FIELDS:
            if key in job and not _HHMM.fullmatch(str(job[key])):
                raise ValueError(f"{key}: «{job[key]}» — ожидалось время ЧЧ:ММ "
                                 f"от 00:00 до 23:59")
        start, end = job.get("window_start"), job.get("window_end")
        if start is not None and end is not None and str(end) < str(start):
            raise ValueError(f"окно {start}-{end} заканчивается раньше, "
                             f"чем начинается")
        return job


class MoveRequest(BaseModel):
    plan_id: str
    job_id: str
    engineer_id: str | None = None
    time_limit_s: int = Field(10, ge=1, le=MAX_TIME_LIMIT_S)


class RegionInfo(BaseModel):
    name: str
    jobs: int
    engineers: int
    depots: list
    clusters: dict


class StopOut(BaseModel):
    job_id: str
    seq: int
    address: str
    window: str
    arrive: str            # плановое время прибытия
    start: str             # плановое время начала работ
    end: str
    wait_min: int
    late_min: int
    km: float              # пробег плеча
    travel_min: int
    lat: float
    lon: float
    skill: str
    priority: int
    estimated: bool = False


class RouteOut(BaseModel):
    engineer_id: str
    engineer_name: str
    transport: str
    cluster: str
    depart: str
    km: float              # пробег по маршруту
    jobs: int
    travel_min: int
    work_min: int
    span_min: int
    stops: list[StopOut]


class UnassignedOut(BaseModel):
    job_id: str
    code: str
    reason: str            # явная причина понятным диспетчеру языком
    detail: str = ""
    address: str = ""
    window: str = ""
    lat: float = 0.0
    lon: float = 0.0


class PlanOut(BaseModel):
    plan_id: str
    region: str
    algo: str
    used_engineers: int    # обязательная метрика №1
    total_km: float        # обязательная метрика №2
    assigned: int
    total_jobs: int
    late_jobs: int
    travel_min: int
    routes: list[RouteOut]
    unassigned: list[UnassignedOut]
    depots: list
    source: str = "живой расчёт"   # эталон из artifacts/ либо прогон солвера
    warning: str = ""              # почему эталон не использован, если не использован
    meta: dict = Field(default_factory=dict)
