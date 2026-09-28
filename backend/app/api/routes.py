# -*- coding: utf-8 -*-
"""REST API диспетчера.

| Метод | Путь                          | Отдаёт                                  |
|-------|-------------------------------|-----------------------------------------|
| GET   | /api/regions                  | регионы с числом заявок и бригад        |
| POST  | /api/plan                     | план по региону (solver или baseline)   |
| GET   | /api/plan/{id}                | сохранённый план                        |
| GET   | /api/compare                  | наш план, базовый, контрольный день     |
| GET   | /api/explain/{plan}/{job}     | объяснение либо причина с ценой вопроса |
| GET   | /api/explain_route/{plan}/{e} | почему у бригады такой маршрут          |
| POST  | /api/event                    | перепланирование, новый план и разница  |
| POST  | /api/validate_move            | чем обойдётся ручное переназначение     |
| POST  | /api/move                     | применить ручное переназначение         |
| GET   | /api/audit/{id}               | отчёт независимого аудитора             |
| GET   | /api/export/{id}              | план в CSV                              |

Ручное переназначение здесь не потому, что так удобнее реализовать.
Постановщик сказал прямо: «такую функциональность необходимо заложить», и
диспетчер обязан иметь возможность поступить по-своему. Но не вслепую: сначала
validate_move показывает цену по каждой бригаде, и только потом move.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Query, Response

from ..config import REGIONS, SKILL_RU, TRANSPORT_RU
from ..models import hhmm
from ..solver.audit import audit_plan
from ..solver.baseline import solve_baseline
from ..solver.compare import compare, control_column
from ..solver.engine import solve, solve_with_fleet
from ..solver.explain import (plan_days, remedy_line, why_assigned,
                               why_unassigned)
from ..solver.explain_route import explain_route
from ..solver.feasibility import check_equipment, check_static, route_fits
from ..solver.metrics import replay_route
from ..solver.replan import (MODE_RU, Event, apply_event, blocked_of,
                             cancelled_of, problem_with_event)
from . import artifacts
from .schemas import (ADDRESS_MAX, JOB_ID, MAX_TIME_LIMIT_S, EventRequest,
                      MoveRequest, PlanRequest)
from .state import get_problem, registry

router = APIRouter(prefix="/api")

# Сколько живых расчётов идёт одновременно. Решатель в одном процессе берёт не
# больше одного ядра (замерено: два разом делят его пополам), а стенд открыт
# в интернет: без предела любой, кто нажмёт «Пересчитать» в цикле, положит
# стенд всем, кто смотрит его в этот момент. Заранее посчитанные планы,
# объяснения и карта под предел не попадают — они не считают.
HEAVY_MAX = 2
HEAVY = threading.BoundedSemaphore(HEAVY_MAX)


@contextmanager
def _heavy():
    """Место под живой расчёт или отказ сразу. Очередь хуже отказа: запрос в
    ней держит соединение, а человек не знает, ждать ли."""
    if not HEAVY.acquire(blocking=False):
        raise HTTPException(503, "Сервер занят: сейчас идут другие расчёты. "
                                 "Повторите через полминуты — заранее "
                                 "посчитанные планы доступны и сейчас.")
    try:
        yield
    finally:
        HEAVY.release()


# --- сборка ответов ----------------------------------------------------------

def _hhmm_to_min(s: str) -> int:
    """ЧЧ:ММ -> минуты от полуночи. Формат уже проверила схема запроса
    (schemas.HHMM); здесь последняя страховка, чтобы ошибка ввода не стала
    ответом 500 и не превратилась молча в 6039-ю минуту суток."""
    h, sep, m = (s or "").partition(":")
    if not (sep and h.isdigit() and m.isdigit() and int(h) < 24 and int(m) < 60):
        raise HTTPException(422, f"время «{s}» — ожидался формат ЧЧ:ММ "
                                 f"от 00:00 до 23:59")
    return int(h) * 60 + int(m)


def _plan_out(plan_id: str, plan, problem) -> dict:
    jobs = {j.id: j for j in problem.rd.jobs}
    engs = {e.id: e for e in problem.engineers}
    travel = problem.travel

    routes = []
    for r in plan.routes:
        if not r.stops:
            continue
        e = engs[r.engineer_id]
        routes.append(dict(
            engineer_id=r.engineer_id, engineer_name=e.name,
            transport=TRANSPORT_RU[e.transport], cluster=e.cluster,
            depart=hhmm(r.depart_min), km=r.km, jobs=r.n_jobs,
            travel_min=sum(s.leg_min for s in r.stops),
            work_min=sum(jobs[s.job_id].service_min for s in r.stops),
            span_min=r.span_min,
            stops=[dict(
                job_id=s.job_id, seq=s.seq, address=jobs[s.job_id].address,
                window=jobs[s.job_id].window_str, arrive=hhmm(s.arrive_min),
                start=hhmm(s.start_min), end=hhmm(s.end_min),
                wait_min=s.wait_min, late_min=s.late_min, km=s.leg_km,
                travel_min=s.leg_min, lat=jobs[s.job_id].lat,
                lon=jobs[s.job_id].lon, skill=SKILL_RU[jobs[s.job_id].skill],
                priority=jobs[s.job_id].priority,
                estimated=travel.is_estimated(f"job:{s.job_id}"),
            ) for s in r.stops]))

    # Первое «что можно сделать» едет прямо в список: без него «Не назначено»
    # показывает только отказ, а средство прячется за кликом. Строка та же,
    # что первая в карточке заявки, — её строит одна функция.
    unassigned, days = [], None
    for u in plan.unassigned:
        j = jobs.get(u.job_id)
        if j is not None and days is None:
            days = plan_days(plan, problem)
        unassigned.append(dict(job_id=u.job_id, code=u.code, reason=u.reason,
                               detail=u.detail,
                               remedy=remedy_line(j, plan, problem, days) if j else "",
                               address=j.address if j else "",
                               window=j.window_str if j else "",
                               lat=j.lat if j else 0.0, lon=j.lon if j else 0.0))

    # Бригады без заявок: в `routes` их нет, а объяснить, почему бригада в
    # резерве, можно только если её видно (GET /api/explain_route).
    busy = {r.engineer_id for r in plan.routes if r.stops}
    reserve = [dict(engineer_id=e.id, engineer_name=e.name,
                    transport=TRANSPORT_RU[e.transport], cluster=e.cluster,
                    skills=[SKILL_RU[s] for s in ("emergency", "connect", "local")
                            if s in e.skills])
               for e in problem.engineers if e.id not in busy]

    return dict(
        plan_id=plan_id, region=plan.region, algo=plan.algo,
        used_engineers=plan.used_engineers, total_km=plan.total_km,
        assigned=plan.assigned, total_jobs=len(problem.rd.jobs),
        late_jobs=plan.late_jobs, travel_min=plan.travel_min,
        routes=routes, unassigned=unassigned, reserve=reserve,
        depots=[dict(key=d.key, name=d.name, lat=d.lat, lon=d.lon,
                     cluster=d.cluster, is_office=d.is_office)
                for d in problem.rd.depots.values()],
        meta=dict(plan.meta))


def _fetch(plan_id: str):
    try:
        return registry.get(plan_id)
    except KeyError:
        raise HTTPException(404, f"план «{plan_id}» не найден; "
                                 f"постройте новый через POST /api/plan")


# --- эндпоинты ---------------------------------------------------------------

@router.get("/regions")
def regions():
    out = []
    for name in REGIONS:
        p = get_problem(name)
        clusters: dict = {}
        for j in p.rd.jobs:
            clusters[j.cluster] = clusters.get(j.cluster, 0) + 1
        # Контрольный день идёт вместе с регионом: иначе интерфейсу пришлось бы
        # держать эти числа у себя, а число, вписанное в код экрана, рано или
        # поздно разойдётся с файлом, из которого оно взято.
        c = control_column(name)
        out.append(dict(
            name=name, jobs=len(p.rd.jobs), engineers=len(p.engineers),
            depots=[dict(key=d.key, name=d.name, lat=d.lat, lon=d.lon,
                         cluster=d.cluster, is_office=d.is_office)
                    for d in p.rd.depots.values()],
            clusters=clusters,
            live_day=dict(engineers=c["used_engineers"], assigned=c["assigned"],
                          late=c["late_jobs"], note=c["note"], title=c["title"],
                          total=c["total_jobs"], cancelled=c["cancelled"],
                          not_sent=c["unassigned"], breakdown=c["breakdown"])))
    return out


def _problem_or_400(region: str):
    try:
        return get_problem(region)
    except KeyError:
        raise HTTPException(400, f"неизвестный регион «{region}»; "
                                 f"есть: {', '.join(REGIONS)}")


def _reference_or_live(region: str, algo: str, time_limit_s: int, problem):
    """-> (план, источник, предупреждение).

    По умолчанию отдаётся эталон из artifacts/: мгновенно и одинаково на всех
    машинах. Нет артефакта — считаем живьём и ГОВОРИМ об этом в ответе, а не
    падаем: отсутствие заранее посчитанного плана не повод оставить диспетчера
    без плана вообще.
    """
    try:
        plan = artifacts.load_plan(region, algo, problem)
    except ValueError as exc:
        plan = None
        broken = str(exc)
    else:
        broken = ""
    if plan is not None:
        return plan, "эталон", ""
    if algo == "baseline":
        live = solve_baseline(problem)
    else:
        with _heavy():
            live = solve(problem, time_limit_s=time_limit_s)
    warning = (f"эталонный план непригоден ({broken}), посчитан живьём"
               if broken else
               "эталонного плана нет, посчитан живьём — числа могут отличаться "
               "от слайдов; соберите artifacts через tools/build_reference.py")
    return live, "живой расчёт", warning


@router.post("/plan")
def make_plan(req: PlanRequest):
    problem = _problem_or_400(req.region)
    warning, source = "", "живой расчёт"

    if req.keep:
        # Урезанный парк — это отдельный сценарий, эталона на него нет и быть
        # не должно: размеров парка много, а демонстрируется один.
        with _heavy():
            plan = solve_with_fleet(problem, req.keep, time_limit_s=req.time_limit_s)
    elif req.recompute and req.algo == "baseline":
        plan = solve_baseline(problem)
    elif req.recompute:
        with _heavy():
            plan = solve(problem, time_limit_s=req.time_limit_s)
    else:
        plan, source, warning = _reference_or_live(
            req.region, req.algo, req.time_limit_s, problem)

    # Урезанный парк меняет состав задачи, и объяснения обязаны считаться по
    # ней же, иначе «отклонённые бригады» будут включать несуществующих.
    stored = problem
    if req.keep:
        from ..solver.engine import Problem, subset_fleet
        stored = Problem(problem.rd, subset_fleet(problem.engineers, req.keep),
                         problem.travel)
    plan_id = registry.put(plan, stored)
    out = _plan_out(plan_id, plan, stored)
    out["source"] = source
    out["warning"] = warning
    return out


@router.get("/plan/{plan_id}")
def get_plan(plan_id: str):
    plan, problem, _ = _fetch(plan_id)
    return _plan_out(plan_id, plan, problem)


@router.get("/compare")
def compare_plans(region: str = Query(...), time_limit_s: int = Query(20, ge=1, le=MAX_TIME_LIMIT_S),
                  recompute: bool = Query(False)):
    problem = _problem_or_400(region)
    if recompute:
        with _heavy():
            ours = solve(problem, time_limit_s=time_limit_s)
        base = solve_baseline(problem)
        sources, warnings = {"solver": "живой расчёт", "baseline": "живой расчёт"}, []
    else:
        ours, s1, w1 = _reference_or_live(region, "solver", time_limit_s, problem)
        base, s2, w2 = _reference_or_live(region, "baseline", time_limit_s, problem)
        sources = {"solver": s1, "baseline": s2}
        warnings = [w for w in (w1, w2) if w]
    data = compare(problem, [ours, base])
    data["plan_ids"] = {"solver": registry.put(ours, problem),
                        "baseline": registry.put(base, problem)}
    data["sources"] = sources
    data["warnings"] = warnings
    return data


@router.get("/explain/{plan_id}/{job_id}")
def explain(plan_id: str, job_id: str):
    plan, problem, _ = _fetch(plan_id)
    if not any(j.id == job_id for j in problem.rd.jobs):
        raise HTTPException(404, f"заявки «{job_id}» нет в регионе {plan.region}")
    if job_id in set(plan.meta.get("cancelled") or ()):
        # Отменённая событием заявка не назначена и не «не назначена»: её нет в
        # работе, и why_unassigned к ней неприменим.
        events = plan.meta.get("events") or []
        return dict(status="отменена", job_id=job_id,
                    headline=f"Заявка {job_id} отменена и в плане больше не участвует",
                    reason="отменена событием дня",
                    detail=("события плана: " + "; ".join(events)) if events else "",
                    remedies=[], remedy="", reasons=[], rejected=[], checks=[])
    if any(s.job_id == job_id for r in plan.routes for s in r.stops):
        e = why_assigned(job_id, plan, problem)
        return dict(status="назначена", job_id=job_id, engineer_id=e.engineer_id,
                    headline=e.headline, reasons=e.reasons,
                    rejected=[dict(engineer_id=x.engineer_id, code=x.code,
                                   reason=x.reason) for x in e.rejected],
                    rejected_summary=e.rejected_summary,
                    travel_by_mode=e.travel_by_mode, used_mode=e.used_mode,
                    caveat=e.caveat, checks=e.checks,
                    passport=e.passport, checked=e.checked, schedule=e.schedule,
                    added_km=e.added_km, position=e.position_text,
                    frozen=e.frozen)
    u = why_unassigned(job_id, plan, problem)
    return dict(status="не назначена", job_id=job_id, code=u.code,
                reason=u.reason, detail=u.detail,
                remedies=[dict(kind=r.kind, action=r.action, effect=r.effect,
                               engineer_id=r.engineer_id,
                               window=[hhmm(r.window[0]), hhmm(r.window[1])]
                               if r.window else None, text=r.text)
                          for r in u.remedies],
                remedy=u.remedy, passport=u.passport, blocked_by=u.blocked_by)


@router.get("/explain_route/{plan_id}/{engineer_id}")
def explain_route_of(plan_id: str, engineer_id: str):
    """Почему у бригады такой маршрут (ТЗ 2.1.7, 2.4.2): день целиком,
    порядок объезда, ожидания, ограничения, а у бригады без заявок — почему
    она в резерве."""
    plan, problem, _ = _fetch(plan_id)
    if not any(e.id == engineer_id for e in problem.engineers):
        raise HTTPException(404, f"бригады «{engineer_id}» нет в плане "
                                 f"{plan_id} ({plan.region})")
    out = explain_route(engineer_id, plan, problem)
    out["plan_id"] = plan_id
    return out


@router.post("/event")
def apply_replan_event(req: EventRequest):
    """Событие дня. Новая обычная заявка встаёт в свободный интервал, не
    трогая остальной план (mode="insert"); авария, отмена и выбытие
    перепланируют остаток дня (mode="replan"). Режим назван и в поле `mode`,
    и в заголовке разницы.

    Всё событие — под пределом живых расчётов: оно запускает решатель, а для
    нового адреса ещё и ходит к геокодеру и OSRM. Предел заодно держит
    частоту этих запросов в рамках правил публичных сервисов."""
    plan, problem, _ = _fetch(req.plan_id)
    with _heavy():
        return _event_result(req, plan, problem)


def _event_result(req: EventRequest, plan, problem) -> dict:
    at = _hhmm_to_min(req.at)
    _validate_time(at, plan)
    job, geocode_note = None, None
    if req.kind == "urgent":
        job, geocode_note = _job_from_request(req, problem)
    ev = Event(kind=req.kind, at_min=at, job=job,
               job_id=req.job_id, engineer_id=req.engineer_id)
    _validate_event(ev, plan, problem)
    try:
        new_plan, diff = apply_event(plan, problem, ev,
                                     time_limit_s=req.time_limit_s)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    new_problem = problem_with_event(problem, ev)
    if geocode_note is not None and job is not None:
        geocode_note = dict(geocode_note, **_roads_note(new_problem, job))
    new_id = registry.put(new_plan, new_problem, dict(parent=req.plan_id))
    return dict(
        plan=_plan_out(new_id, new_plan, new_problem),
        previous_plan_id=req.plan_id,
        mode=diff.mode,
        geocode=geocode_note,
        diff=dict(event=diff.event, mode=diff.mode, mode_text=MODE_RU[diff.mode],
                  frozen=diff.frozen, en_route=diff.en_route, moved=diff.moved,
                  resequenced=diff.resequenced, dropped=diff.dropped,
                  added=diff.added, rejected=diff.rejected,
                  cancelled=diff.cancelled, metrics=diff.metrics,
                  is_quiet=diff.is_quiet))


def _validate_time(at: int, plan) -> None:
    """События идут по порядку. Событие раньше предыдущего разморозило бы то,
    что предыдущее уже объявило случившимся."""
    last = plan.meta.get("at_min")
    if last is not None and at < last:
        raise HTTPException(400, f"событие в {hhmm(at)} раньше предыдущего "
                                 f"({hhmm(last)}): время назад не идёт — выберите "
                                 f"момент не раньше {hhmm(last)}")


def _validate_event(ev: Event, plan, problem) -> None:
    if ev.kind == "cancel":
        if not ev.job_id:
            raise HTTPException(400, "для отмены нужен job_id")
        if not any(j.id == ev.job_id for j in problem.rd.jobs):
            raise HTTPException(404, f"заявки «{ev.job_id}» нет в регионе")
        if ev.job_id in cancelled_of(plan):
            raise HTTPException(400, f"заявка «{ev.job_id}» уже отменена")
    if ev.kind == "unavailable":
        if not ev.engineer_id:
            raise HTTPException(400, "для недоступности нужен engineer_id")
        if not any(e.id == ev.engineer_id for e in problem.engineers):
            raise HTTPException(404, f"бригады «{ev.engineer_id}» нет в регионе")
        if ev.engineer_id in blocked_of(plan):
            raise HTTPException(400, f"бригада «{ev.engineer_id}» уже выбыла")


def _roads_note(problem, job) -> dict:
    """Как посчитаны расстояния до новой точки. Известно только после сборки
    задачи: геокодер отвечает раньше, чем спрошены дороги."""
    from ..geo.roads import ROADS_NOTE, remembered_legs
    from ..geo.travel import DETOUR_FACTOR
    key = f"job:{job.id}"
    if key not in problem.travel.index:
        return {}
    if not problem.travel.is_estimated(key):
        return dict(roads="по дорогам", note=ROADS_NOTE)
    _, why = remembered_legs(problem.rd.name, key, problem.travel.points[:-1],
                             job.lat, job.lon)
    return dict(roads="по прямой",
                note=f"дороги до этой точки взять не удалось ({why or 'нет ответа'}): "
                     f"расстояния считаются по прямой с замеренным коэффициентом "
                     f"извилистости {DETOUR_FACTOR}")


def _geocoded(address: str, problem):
    """Адрес -> (точка, как её получили). Отказ геокодера — это 400 с причиной
    для человека: «нет сети» и «система сломалась» — не одно и то же.

    В ответ едет и название найденного места, и расстояние до стартовой
    точки зоны: диспетчер должен видеть, куда встала точка, до того как по ней
    перестроят день."""
    from ..geo.geocode import GeocodeError, area_for, resolve
    area = area_for(problem.rd.name, problem.rd.depots.values(), address)
    try:
        point = resolve(address, area=area)
    except GeocodeError as exc:
        raise HTTPException(400, f"новая заявка: {exc}")
    return point, dict(address=address, lat=point.lat, lon=point.lon,
                       source=point.source, note=point.note, name=point.name,
                       km_from_depot=point.km, depot=point.anchor)


def _check_job_text(payload: dict) -> None:
    """Номер и адрес новой заявки уходят в подсказки карты как HTML: разметку
    и заведомо не адресную длину отсекаем до геокодера и расчёта."""
    if not JOB_ID.fullmatch(str(payload.get("id") or "")):
        raise HTTPException(400, "новая заявка: номер — до 40 букв, цифр, точек, "
                                 "дефисов и подчёркиваний")
    address = str(payload.get("address") or "")
    if len(address) > ADDRESS_MAX or "<" in address or ">" in address:
        raise HTTPException(400, f"новая заявка: адрес — обычный текст до "
                                 f"{ADDRESS_MAX} знаков, без угловых скобок")


def _job_from_request(req: EventRequest, problem):
    """Новая заявка -> (Job, как получены координаты).

    Либо полный набор полей от диспетчера (формат ТЗ 2.4, приоритет читается:
    «Срочная» делает заявку аварией, и она перепланирует остаток дня), либо
    демонстрационная авария. Адрес может быть новым: координат в выгрузке
    нет, и тогда работает штатная деградация — кэш, один запрос к геокодеру,
    отказ словами. Матрица после этого достраивается по прямой, и плечи до
    этой точки помечены оценкой.
    """
    from ..solver.replan import make_demo_emergency
    if not req.job:
        # Показательную аварию можно добавить и второй раз в цепочке событий.
        # С одним и тем же номером вторая упиралась бы в отказ «заявка с таким
        # номером уже есть», поэтому номер берётся первый свободный.
        taken = {j.id for j in problem.rd.jobs}
        demo_id, n = "AVARIA-DEMO", 1
        while demo_id in taken:
            n += 1
            demo_id = f"AVARIA-DEMO-{n}"
        job = make_demo_emergency(problem, _hhmm_to_min(req.at), job_id=demo_id)
        if not (req.address or "").strip():
            return job, None
        # Диспетчеру достаточно адреса: длительность берётся из норматива,
        # окно — из времени события, а координаты ищутся так же, как для
        # полного набора полей. Набирать широту с долготой в аварийной
        # ситуации никто не будет.
        point, note = _geocoded(req.address, problem)
        from dataclasses import replace
        from ..geo.cluster import classify_cluster
        job = replace(job, address=req.address, lat=point.lat, lon=point.lon,
                      cluster=classify_cluster(req.address))
        return job, note

    payload = dict(req.job)
    _check_job_text(payload)
    note = None
    if payload.get("lat") in (None, "") or payload.get("lon") in (None, ""):
        point, note = _geocoded(str(payload.get("address") or ""), problem)
        payload["lat"], payload["lon"] = point.lat, point.lon

    from ..io.spec_format import load_events
    import json
    import tempfile
    from pathlib import Path
    body = [dict(type="urgent", at=req.at, job=payload)]
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "e.json"
        f.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        try:
            return load_events(f)[0].job, note
        except KeyError as exc:
            raise HTTPException(400, f"новая заявка: нет поля «{exc.args[0]}»")
        except ValueError as exc:
            raise HTTPException(400, f"новая заявка: {exc}")


# --- ручное переназначение ---------------------------------------------------

def _locked(job, ds) -> str | None:
    """Почему заявку нельзя отдать никому. None — можно."""
    at = ds["at_min"]
    if job.id in ds["cancelled"]:
        return "заявка отменена клиентом — переназначать нечего"
    rec = ds["frozen"].get(job.id)
    if rec is not None:
        if rec.get("state") == "en_route":
            return (f"бригада {rec.get('engineer_id')} уже едет к клиенту и начнёт "
                    f"в {hhmm(rec.get('start_min'))} — переназначать поздно")
        return f"работа начата в {hhmm(rec.get('start_min'))} — переназначать поздно"
    if at is not None and job.win_end < at:
        return (f"окно клиента {job.window_str} закрылось до события в "
                f"{hhmm(at)} — после события заявку не выполнить")
    return None


def _move_options(job, plan, problem) -> dict:
    """Цена переназначения по КАЖДОЙ бригаде парка, включая неподходящие.

    Диспетчеру нужен не отфильтрованный список, а ответ «почему нельзя» по
    каждому, кого он видит на экране. Скрытая строка читается как «система
    что-то от меня прячет».

    Один расчёт на оба эндпоинта: validate_move его показывает, move
    применяет. Два расчёта рано или поздно разошлись бы, и диспетчер видел бы
    «возьмёт», а получал отказ.

    После события действуют правила заморозки (plan.meta): начатое и то, к
    чему бригада уже едет, не переносится; у получателя меняется только часть
    дня после замороженной, и не раньше момента события; выбывшая бригада
    новых заявок не берёт. Без этих правил заявку, законченную в 10:30, можно
    было бы в 15:40 отдать другой бригаде «на 11:13».
    """
    from ..solver.replan import (best_insertion, carried_by, day_state,
                                 near_miss, route_without)
    ds = day_state(plan, problem)
    at, frozen_ids = ds["at_min"], set(ds["frozen"])
    jobs = {j.id: j for j in problem.rd.jobs}
    engs = {e.id: e for e in problem.engineers}
    routes = {r.engineer_id: r for r in plan.routes}
    current = next((r.engineer_id for r in plan.routes
                    for s in r.stops if s.job_id == job.id), None)
    locked = _locked(job, ds)

    # Прежний владелец теряет заявку. Его день пересчитывается тем же
    # правилом, и он может перестать быть допустимым: дорожная матрица не
    # обязана выполнять неравенство треугольника.
    owner_after, owner_bad = None, None
    if current and not locked:
        owner_after = route_without(engs[current], routes[current], job.id,
                                    problem, at, frozen_ids)
        v = route_fits(owner_after, engs[current])
        if not v.ok:
            owner_bad = (f"у {current} после изъятия заявки маршрут перестаёт "
                         f"быть допустимым: {v.text}")

    rows = {}
    for e in problem.engineers:
        base = owner_after if e.id == current else routes.get(e.id)
        static = check_static(job, e)
        eq = check_equipment(job, e, carried_by(base, jobs))
        # Бригада без единой заявки сегодня не задействована. Отдать ей заявку
        # значит поднять обязательную метрику «задействовано исполнителей» на
        # единицу — это дороже любых километров, и молчать об этом нельзя:
        # диспетчер увидел бы «+12.67 км» и не понял, почему счётчик вырос.
        idle = e.id not in routes or not routes[e.id].stops
        fit = None
        if locked:
            verdict = f"сейчас у неё; {locked}" if e.id == current else locked
        elif e.id in ds["blocked"]:
            verdict = "выбыл — новых заявок не берёт"
        elif not static.ok:
            verdict = static.text
        elif not eq.ok:
            verdict = eq.text
        elif owner_bad and e.id != current:
            verdict = owner_bad
        else:
            fit = best_insertion(e, base, job, problem, at, frozen_ids)
            if fit is None:
                verdict = ("не помещается по времени: "
                           + near_miss(e, base, job, problem, at, frozen_ids))
            elif e.id == current:
                verdict = "сейчас у неё"
            elif idle:
                verdict = (f"возьмёт, +{fit[0]} км, но выйдет на смену — "
                           f"исполнителей станет на одного больше")
            else:
                verdict = f"возьмёт, +{fit[0]} км"
        rows[e.id] = dict(
            engineer_id=e.id, engineer_name=e.name,
            transport=TRANSPORT_RU[e.transport],
            is_current=e.id == current,
            idle=idle,
            allowed=fit is not None,
            added_km=fit[0] if fit else None,
            added_min=(fit[2].span_min - (base.span_min if base else 0))
            if fit else None,
            position=fit[1] + 1 if fit else None,
            skill_ok=job.skill in e.skills,
            transport_ok=not job.requires_transport
            or e.transport == job.requires_transport,
            cluster_ok=job.cluster == e.cluster,
            equipment_ok=eq.ok,
            verdict=verdict,
            route=fit[2] if fit else None)
    return dict(current=current, locked=locked, owner_after=owner_after,
                rows=rows)


@router.post("/validate_move")
def validate_move(req: MoveRequest):
    """Чем обойдётся перенос, по каждой бригаде. `locked` — причина, по
    которой заявку нельзя отдать никому (работа начата, бригада уже в пути,
    окно закрылось до события, заявка отменена); иначе null."""
    plan, problem, _ = _fetch(req.plan_id)
    job = next((j for j in problem.rd.jobs if j.id == req.job_id), None)
    if job is None:
        raise HTTPException(404, f"заявки «{req.job_id}» нет в регионе")
    opts = _move_options(job, plan, problem)
    return dict(job_id=req.job_id, locked=opts["locked"],
                candidates=[{k: v for k, v in row.items() if k != "route"}
                            for row in opts["rows"].values()])


@router.post("/move")
def move(req: MoveRequest):
    """Ручное переназначение. Маршруты обеих бригад пересчитываются целиком,
    а не «вставляется куда попало»: иначе на экране появится маршрут, которого
    не может быть. Вердикт — тот же, что показал validate_move."""
    plan, problem, _ = _fetch(req.plan_id)
    job = next((j for j in problem.rd.jobs if j.id == req.job_id), None)
    if job is None:
        raise HTTPException(404, f"заявки «{req.job_id}» нет в регионе")
    target = next((e for e in problem.engineers if e.id == req.engineer_id), None)
    if target is None:
        raise HTTPException(400, f"бригады «{req.engineer_id}» нет в регионе")

    opts = _move_options(job, plan, problem)
    if opts["locked"]:
        raise HTTPException(400, f"{job.id}: {opts['locked']}")
    row = opts["rows"][target.id]
    if not row["allowed"]:
        raise HTTPException(400, f"{target.id}: {row['verdict']}")

    new_routes = []
    for r in plan.routes:
        if r.engineer_id == target.id:
            new_routes.append(row["route"])
        elif r.engineer_id == opts["current"]:
            new_routes.append(opts["owner_after"])
        else:
            new_routes.append(r)
    if all(r.engineer_id != target.id for r in plan.routes):
        new_routes.append(row["route"])

    from ..models import Plan
    # Пометка заморозки и момент события едут дальше вместе с meta: по ним
    # аудитор проверит, что перенос прошлого не тронул.
    moved = Plan(region=plan.region, algo="manual", routes=new_routes,
                 unassigned=[u for u in plan.unassigned if u.job_id != job.id],
                 meta=dict(plan.meta, manual_move=f"{job.id} -> {target.id}"))
    new_id = registry.put(moved, problem, dict(parent=req.plan_id))
    return dict(plan=_plan_out(new_id, moved, problem),
                previous_plan_id=req.plan_id,
                added_km=row["added_km"], position=row["position"])


# --- экраны обоснования ------------------------------------------------------

CURVE_CAVEAT = (
    "Тот же день при меньшем штате. Первыми из штата уходят бригады без заявок "
    "в основном плане, потом наименее загруженные; зона не остаётся без "
    "бригады. Пока в штате есть все работающие, точка — сам основной план: "
    "аудит проверяет его на урезанном составе. Меньшие штаты считались по "
    "{seconds} с от основного плана без выбывших бригад, поэтому с ним они "
    "сравнимы лишь примерно.")
# Кривая, посчитанная прежним способом (урезание по списку, без учёта загрузки),
# в summary.json не помечена curve_built_at — к ней прежняя оговорка.
CURVE_CAVEAT_BY_LIST = ("Каждая точка — отдельный расчёт того же дня. Сколько его "
                        "считали, указано в таблице: основной план искали дольше "
                        "остальных точек, поэтому с ними он сравним лишь примерно.")
SENSITIVITY_CAVEAT = (
    "Первая строка — основной план дня. Остальные — тот же день с одним "
    "изменённым правилом. Если правило только ослаблено, основной план "
    "остаётся допустимым, и в строке — лучшее из двух: нового расчёта и "
    "основного плана. Поэтому ослабление правила не показывает лишней бригады "
    "из-за разброса поиска.")
# Таблица, посчитанная прежним способом (все строки — отдельные прогоны), не
# помечена from_main_plan — к ней прежняя оговорка.
SENSITIVITY_CAVEAT_SEPARATE = (
    "Строки таблицы сравнимы между собой: все посчитаны одинаково. С числами "
    "плана дня их сравнивать нельзя: таблицу считали несколько расчётов "
    "одновременно, и разница в одну бригаду бывает просто разбросом между "
    "расчётами, а не ценой правила.")


@router.get("/summary")
def summary():
    """Кривая «парк -> результат» и сводка эталонных прогонов. Солвер не
    вызывается: экран отвечает на вопрос «откуда взялось число бригад»,
    и отвечать на него лотереей нельзя."""
    data = artifacts.load_summary()
    if data is None:
        raise HTTPException(503, "artifacts/summary.json не собран; "
                                 "запустите tools/build_reference.py --curve")
    data["caveat"] = (CURVE_CAVEAT.format(seconds=data.get("curve_seconds", "?"))
                      if data.get("curve_built_at") else CURVE_CAVEAT_BY_LIST)
    return data


@router.get("/sensitivity")
def sensitivity():
    """Цена каждого ограничения и допущения.

    Оговорка о сопоставимости едет ВМЕСТЕ с данными, а не остаётся в устном
    комментарии: без неё первая же строка таблицы будет сопоставлена с числом
    на слайде, и разница в одну бригаду прочитается как цена ограничения,
    хотя это эффект бюджета времени.
    """
    data = artifacts.load_sensitivity()
    if data is None:
        raise HTTPException(503, "artifacts/sensitivity.json не собран; "
                                 "запустите tools/sensitivity.py")
    data["caveat"] = (SENSITIVITY_CAVEAT if data.get("from_main_plan")
                      else SENSITIVITY_CAVEAT_SEPARATE)
    data["soft_windows_note"] = (
        "Здесь мы разрешили опоздание до 30 минут. Это уже другая задача: "
        "километры этой строки с остальными не сопоставимы, а проверка планов "
        "честно показывает нарушение жёсткого правила.")
    return data


CONVERGENCE_CAVEAT = (
    "Один прогон на участок: решатель сообщает о каждом найденном плане, и "
    "значение в момент t — лучший план, найденный к этому моменту. Время — по "
    "настенным часам той машины, что указана выше: на более медленной кривая "
    "сдвигается вправо. В начале бригад может становиться больше — чтобы "
    "закрыть больше заявок, нужно больше людей.")


@router.get("/convergence")
def convergence():
    """Кривая «время счёта -> результат»: что даёт каждая лишняя минута
    поиска. Решатель не вызывается — это заранее посчитанный замер."""
    data = artifacts.load_convergence()
    if data is None:
        raise HTTPException(503, "artifacts/convergence.json не собран; "
                                 "запустите tools/convergence.py")
    data["caveat"] = CONVERGENCE_CAVEAT
    return data


# --- аудит и выгрузка --------------------------------------------------------

@router.get("/geometry/{plan_id}")
def geometry(plan_id: str, live: bool = False):
    """Форма маршрутов по дорогам. Отдельным запросом от плана: полилинии весят
    сотни килобайт и нужны только карте. live=1 — добрать у OSRM форму
    участков, которых нет в сохранённых картах (после «Пересчитать» и
    событий); без сети они остаются прямыми."""
    from ..geo.geometry import plan_geometry
    plan, problem, _ = _fetch(plan_id)
    return plan_geometry(plan, problem, live=live)


@router.get("/audit/{plan_id}")
def audit(plan_id: str):
    plan, problem, _ = _fetch(plan_id)
    rep = audit_plan(plan, problem)
    return dict(plan_id=plan_id, region=rep.region, algo=rep.algo,
                headline=rep.headline, passed=rep.passed, total=rep.total,
                ok=rep.ok, violations=rep.violations,
                checks=[dict(code=c.code, title=c.title, ok=c.ok,
                             problems=c.problems) for c in rep.checks])


@router.get("/export/{plan_id}")
def export(plan_id: str):
    from ..io.export import plan_to_csv_bytes
    plan, problem, _ = _fetch(plan_id)
    from urllib.parse import quote
    body = plan_to_csv_bytes(plan, problem)
    # Заголовки HTTP — latin-1. Имя региона кириллическое, поэтому оно едет
    # процентным кодированием по RFC 5987, а рядом кладётся ascii-запасной
    # вариант для старых клиентов.
    name = f"{plan.region}-{plan.algo}-{plan_id}.csv"
    return Response(
        content=body, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f"attachment; filename=\"plan-{plan_id}.csv\"; "
                 f"filename*=UTF-8''{quote(name)}"})
