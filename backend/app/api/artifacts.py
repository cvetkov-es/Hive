# -*- coding: utf-8 -*-
"""Чтение эталонных планов и расчётов из artifacts/.

Зачем это вообще есть. Лимит солвера задан по настенным часам, поэтому качество
плана зависит от того, сколько процессорного времени досталось прогону: замерено,
что при одинаковом лимите прогон с 12.3 процессорными секундами дал 230.1 км,
а с 6.6 — 243.8 км. Считать план на каждый клик значит показывать лотерею,
причём с двадцатью секундами молчания перед каждым результатом.

Поэтому планы считаются заранее с большим бюджетом (tools/build_reference.py),
проверяются аудитором и коммитятся. Интерфейс отдаёт их мгновенно, а кнопка
«Пересчитать» доказывает, что живой прогон приходит к тому же.

Артефакт разворачивается в НАСТОЯЩИЙ объект Plan, а не в готовый JSON для
экрана. Иначе объяснения, аудит, перепланирование и ручное переназначение
пришлось бы писать вторыми реализациями поверх словарей — а это ровно тот
способ, которым план и его подтверждение расходятся.
"""
from __future__ import annotations
import json
from pathlib import Path

from ..config import ROOT
from ..models import Plan, Route, Stop, Unassigned

ARTIFACTS = ROOT / "artifacts"
KM_TOLERANCE = 0.05     # см. комментарий в load_plan


def _path(region: str, algo: str) -> Path:
    prefix = "baseline" if algo == "baseline" else "plan"
    return ARTIFACTS / f"{prefix}_{region}.json"


def has_plan(region: str, algo: str = "solver") -> bool:
    return _path(region, algo).is_file()


def load_plan(region: str, algo: str, problem) -> Plan | None:
    """-> Plan из артефакта либо None, если артефакта нет.

    Маршруты без заявок в артефакт не пишутся, но в объекте плана они должны
    быть: солвер отдаёт запись на каждую бригаду, и код, который ходит по
    plan.routes, вправе на это рассчитывать.
    """
    path = _path(region, algo)
    if not path.is_file():
        return None
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("region") != region:
        raise ValueError(f"{path.name}: внутри регион «{d.get('region')}»")

    by_engineer = {}
    for r in d.get("routes", []):
        by_engineer[r["engineer_id"]] = Route(
            engineer_id=r["engineer_id"], depart_min=r["depart"],
            stops=[Stop(job_id=s["job_id"], seq=s["seq"], arrive_min=s["arrive"],
                        start_min=s["start"], end_min=s["end"],
                        wait_min=s["wait"], late_min=s["late"],
                        leg_km=s["leg_km"], leg_min=s["leg_min"])
                   for s in r.get("stops", [])])

    routes = [by_engineer.get(e.id, Route(engineer_id=e.id, stops=[],
                                          depart_min=e.shift_start))
              for e in problem.engineers]
    unknown = set(by_engineer) - {e.id for e in problem.engineers}
    if unknown:
        raise ValueError(f"{path.name}: маршруты у бригад, которых нет в парке: "
                         f"{', '.join(sorted(unknown))}")

    plan = Plan(region=region, algo=d.get("algo", algo), routes=routes,
                unassigned=[Unassigned(job_id=u["job_id"], code=u["code"],
                                       reason=u["reason"], detail=u.get("detail", ""))
                            for u in d.get("unassigned", [])],
                meta=dict(source="эталон", built=d.get("compute", {}),
                          fleet_size=len(problem.engineers)))

    # Числа в шапке артефакта считаны независимо от маршрутов. Если они
    # разошлись, артефакт собран не тем кодом, который его сейчас читает,
    # и молча отдавать такой план нельзя.
    #
    # Целые сверяются точно. Пробегу дан допуск 0.05 км, и он не «на всякий
    # случай»: суммирование одних и тех же плеч даёт 50.345000000000006 через
    # sum() и ровно 50.345 через fsum(), а round() до сотых разводит эти два
    # результата в 50.35 и 50.34. Замерено на BR-ЮГО-01 в Югоцентре: шапка
    # артефакта говорит 190.68, пересчёт по его же плечам — 190.69. Отвергать
    # из-за этого исправный эталон нельзя, а молчать о расхождении в километр —
    # тем более.
    for field, want in (("used_engineers", d.get("used_engineers")),
                        ("assigned", d.get("assigned"))):
        got = getattr(plan, field)
        if want is not None and got != want:
            raise ValueError(f"{path.name}: {field} в шапке {want}, "
                             f"по маршрутам {got} — пересоберите артефакт")
    want_km = d.get("total_km")
    if want_km is not None and abs(plan.total_km - want_km) > KM_TOLERANCE:
        raise ValueError(f"{path.name}: пробег в шапке {want_km}, по маршрутам "
                         f"{plan.total_km} — пересоберите артефакт")
    return plan


def load_summary() -> dict | None:
    p = ARTIFACTS / "summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def load_sensitivity() -> dict | None:
    p = ARTIFACTS / "sensitivity.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def load_convergence() -> dict | None:
    p = ARTIFACTS / "convergence.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
