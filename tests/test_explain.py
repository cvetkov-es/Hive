# -*- coding: utf-8 -*-
"""Объяснение решения и цена вопроса.

ТЗ 2.1.7 требует объяснить, «почему заявка назначена конкретному инженеру», и
выносит объяснение в отдельный сдаваемый артефакт. Постановщик отдельно назвал
антипаттерном «портянку» — поэтому ограничена длина, а не наличие (Р3).

Самый дорогой баг в этом месте — расхождение между тем, что сделал солвер, и
тем, что интерфейс про это рассказывает. Объяснение, которое противоречит плану,
хуже отсутствующего: оно выглядит достоверно.
"""
from app.solver.explain import why_assigned, why_unassigned
from app.solver.feasibility import check_static

from helpers import problem, short_fleet_plan, solver_plan


def test_every_assignment_is_explainable():
    """Для каждой назначенной заявки предикат допустимости обязан подтвердить
    её фактического исполнителя. Расхождение здесь — самый дорогой баг."""
    p, plan = problem("Юго-восток"), solver_plan("Юго-восток")
    jobs = {j.id: j for j in p.rd.jobs}
    engs = {e.id: e for e in p.engineers}
    for r in plan.routes:
        for s in r.stops:
            assert check_static(jobs[s.job_id], engs[r.engineer_id]).ok


def test_assignment_explanation_is_short_and_complete():
    """Одна-три строки по умолчанию (Р3), но со всеми обязательными числами."""
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    for r in plan.routes:
        for s in r.stops:
            e = why_assigned(s.job_id, plan, p)
            assert e.headline
            assert r.engineer_id in e.headline, "в заголовке нет исполнителя"
            assert 1 <= len(e.reasons) <= 3, e.reasons
            assert len(e.rejected) <= 3
            assert set(e.travel_by_mode) == {"car", "bike", "transit", "foot"}
            assert e.caveat, "оговорка про неизменные маршруты остальных обязательна"


def test_rejected_alternatives_have_concrete_reasons():
    """«Не подошёл» без причины диспетчеру бесполезно."""
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    stop = plan.routes[0].stops[0]
    e = why_assigned(stop.job_id, plan, p)
    for r in e.rejected:
        assert r.engineer_id and r.reason and r.code


def test_unassigned_has_remedy():
    """Причина без действия бесполезна диспетчеру."""
    p = problem("Югоцентр")
    plan = short_fleet_plan("Югоцентр", 4)      # заведомо мало бригад
    assert plan.unassigned, "при четырёх бригадах обязаны появиться неназначенные"
    for u in plan.unassigned:
        exp = why_unassigned(u.job_id, plan, p)
        assert exp.reason
        assert exp.remedies, f"{u.job_id}: причина есть, а что делать — нет"
        for r in exp.remedies:
            assert r.action and r.effect


def test_remedy_for_time_names_a_window_and_an_engineer():
    """Р5: окно двигать может только человек, но система обязана показать, что
    именно это даст — какое окно закроет заявку и кто тогда её возьмёт."""
    p = problem("Югоцентр")
    plan = short_fleet_plan("Югоцентр", 4)
    with_time = [u for u in plan.unassigned if u.code == "NO_TIME_SLOT"]
    assert with_time, "при урезанном парке обязаны быть отказы по времени"
    found = False
    for u in with_time:
        for r in why_unassigned(u.job_id, plan, p).remedies:
            if r.kind == "window":
                assert r.engineer_id, "сдвиг окна без имени исполнителя бесполезен"
                found = True
    assert found, "ни для одной заявки не показано, какое окно её закроет"


def test_explanation_does_not_contradict_the_plan():
    """Объяснение обязано называть того исполнителя, который стоит в плане, и
    то же время прибытия. Иначе интерфейс и карта расходятся."""
    p, plan = problem("Восток"), solver_plan("Восток")
    from app.models import hhmm
    for r in plan.routes:
        for s in r.stops:
            e = why_assigned(s.job_id, plan, p)
            assert e.engineer_id == r.engineer_id
            assert hhmm(s.arrive_min) in e.headline


def test_checks_table_agrees_with_the_plan():
    """Строка про фактического исполнителя обязана говорить «берёт», а не
    «не помещается».

    Маршрут владельца проверяется БЕЗ этой же заявки: иначе это проверка на
    двойное бронирование, и таблица заявит «не помещается по времени» про
    бригаду, которая заявку выполняет. Это первое, что читают, открыв таблицу.
    """
    p, plan = problem("Югоцентр"), solver_plan("Югоцентр")
    for r in plan.routes[:3]:
        for s in r.stops[:3]:
            rows = why_assigned(s.job_id, plan, p).checks
            mine = next(x for x in rows if x["engineer_id"] == r.engineer_id)
            assert mine["is_current"], f"{s.job_id}: владелец не помечен"
            assert mine["time_ok"], \
                f"{s.job_id}: таблица отрицает назначение, которое есть в плане"
            assert mine["added_km"] is not None
            assert sum(1 for x in rows if x["is_current"]) == 1


# --- объяснение, понятное без кода (ТЗ 8.1), на эталонных планах ---------------
#
# Эталонные планы из artifacts/ — те самые 205 назначений, которые показывает
# интерфейс. Они детерминированы и считаются мгновенно, поэтому по ним
# проверяется каждая карточка, а не выборка.

import functools
import re

from app.api.artifacts import load_plan
from app.config import REGIONS
from app.models import hhmm

JARGON = re.compile(r"солвер|предикат|депо|плеч", re.I)


@functools.lru_cache(maxsize=None)
def reference(region):
    p = problem(region)
    return p, load_plan(region, "solver", p)


@functools.lru_cache(maxsize=None)
def cards(region):
    """[(заявка, остановка, бригада, объяснение)] по всему эталонному плану."""
    p, plan = reference(region)
    jobs = {j.id: j for j in p.rd.jobs}
    return [(jobs[s.job_id], s, r.engineer_id, why_assigned(s.job_id, plan, p))
            for r in plan.routes for s in r.stops]


def all_cards():
    return [c for region in REGIONS for c in cards(region)]


def test_passport_names_the_job():
    """Карточка называет саму заявку: вид работ, окно, длительность, приоритет
    словами ТЗ 2.4.1, оборудование по-русски."""
    for job, _, _, e in all_cards():
        pp = e.passport
        assert pp["work_kind"] and pp["skill"] and pp["address"] and pp["zone"]
        assert re.fullmatch(r"\d\d:\d\d–\d\d:\d\d", pp["window"]), pp["window"]
        assert pp["duration_min"] == job.service_min > 0
        assert pp["priority"] in ("Обычная", "Срочная")
        assert pp["priority_level"] in ("авария", "подключение", "ремонт и дозаказ")
        assert (pp["priority"] == "Срочная") == (job.skill == "emergency")
        for item in pp["equipment"]:
            assert re.search("[а-яА-Я]", item["name"]), item
        assert pp["window"] in pp["summary"]


def test_headline_always_names_the_window_and_the_wait():
    """Окно клиента названо в каждом заголовке. Ожидание — словами и с
    длительностью; без ожидания время начала второй раз не печатается."""
    for job, s, eng_id, e in all_cards():
        h = e.headline
        assert f"{hhmm(job.win_start)}–{hhmm(job.win_end)}" in h, h
        assert eng_id in h and hhmm(s.arrive_min) in h
        if s.wait_min > 0:
            assert "ждать открытия окна" in h, h
            assert hhmm(s.start_min) in h
            h_min = s.wait_min % 60
            assert (f"{h_min} мин" in h) if h_min else (f"{s.wait_min // 60} ч" in h), h
        else:
            assert "сразу начнёт" in h and "начнёт в" not in h, h


def test_headline_and_table_measure_the_same_km():
    """Для текущей бригады заголовок и таблица «Все проверки» дают одно число:
    прирост пробега на ФАКТИЧЕСКОМ месте заявки в маршруте, а не на лучшей
    вставке: иначе выйдет 0.26 в заголовке против 0.21 в таблице."""
    for _, _, eng_id, e in all_cards():
        mine = next(r for r in e.checks if r["engineer_id"] == eng_id)
        assert mine["is_current"] and mine["time_ok"] is True
        assert mine["added_km"] == e.added_km
        assert f"{abs(e.added_km):.2f} км" in e.headline


def test_reasons_do_not_contradict_themselves():
    """Ни «единственная» рядом с «остальные подходящие», ни сравнения числа с
    самим собой, ни «выбрана ради плана целиком» без причины."""
    for job, _, _, e in all_cards():
        text = " | ".join(e.reasons)
        assert "ради плана целиком" not in text, text
        if "только эта бригада" in text:
            assert "подходящ" not in text.replace("только эта бригада подходит", ""), text
        for r in e.reasons:
            kms = re.findall(r"(\d+\.\d\d) км", r)
            if len(kms) != len(set(kms)):
                assert "столько же" in r, r
        # соперник дешевле — значит названа настоящая причина
        for r in e.reasons:
            if "км короче" in r:
                assert ("нет ни одной заявки" in r or "авари" in r
                        or "не нашёл" in r), r


def test_single_candidate_says_who_was_filtered_and_why():
    """Если по профилю подходит одна бригада, карточка говорит, сколько бригад
    отсеялось и почему, — сгруппированно, с суммой, равной остальному парку."""
    seen = 0
    for job, _, eng_id, e in all_cards():
        if not any("только эта бригада" in r for r in e.reasons):
            continue
        seen += 1
        p, _ = reference(next(reg for reg in REGIONS
                              if any(c[3] is e for c in cards(reg))))
        total = len(p.engineers) - 1
        assert e.rejected_summary.startswith(f"остальные {total} "), e.rejected_summary
        counts = [int(n) for n in re.findall(r"(?:у |, |: )(\d+) ", e.rejected_summary)]
        assert sum(counts) == total, e.rejected_summary
    assert seen, "в эталонах есть заявки, которые может взять только одна бригада"


def test_unchecked_cells_are_none_not_false():
    """Проверка, которую не выполняли, — None: бригада отсеялась раньше по
    навыку, транспорту или зоне, и ни окно, ни время по ней не считались.
    False дал бы красное «нет» в 525 таких клетках."""
    for _, _, _, e in all_cards():
        for row in e.checks:
            profile = row["skill_ok"] and row["transport_ok"] and row["cluster_ok"]
            if not profile:
                assert row["window_ok"] is None and row["time_ok"] is None, row
            if row["window_ok"] is False or not row["equipment_ok"]:
                assert row["time_ok"] is None, row
            if row["time_ok"] is None:
                assert row["added_km"] is None or row["is_current"], row


def test_checked_list_covers_every_mandatory_rule():
    """«Что проверено» — навык, транспорт, зона, окно, смена, оборудование и
    длина дня против норматива. У назначенной бригады всё выполнено."""
    for _, s, _, e in all_cards():
        keys = [c["key"] for c in e.checked]
        assert keys == ["skill", "transport", "zone", "window", "shift",
                        "equipment", "workday"], keys
        assert all(c["ok"] for c in e.checked), e.checked
        assert hhmm(s.start_min) in next(c["text"] for c in e.checked
                                         if c["key"] == "window")
        assert "12 ч" in next(c["text"] for c in e.checked if c["key"] == "workday")


def test_texts_have_no_developer_jargon():
    """«Солвер», «предикат», «депо», «плечо» понятны только тому, кто читал
    код. Экран обязан отвечать на ТЗ 8.1 без него."""
    for _, _, _, e in all_cards():
        texts = [e.headline, e.position_text, e.rejected_summary, *e.reasons,
                 *(r.reason for r in e.rejected), *(c["text"] for c in e.checked),
                 *(row["verdict"] for row in e.checks)]
        for t in texts:
            assert not JARGON.search(t), t


# --- причины неназначения -------------------------------------------------------

def test_unassigned_reasons_are_specific():
    """ТЗ 2.2: причина «по каждой из них». Не одна строка на все отказы, а у
    кого и чего не хватило: бригада, окно, время, оборудование."""
    plan = short_fleet_plan("Югоцентр", 4)
    assert plan.unassigned
    for u in plan.unassigned:
        assert u.reason != "не помещается в маршрут по времени", u
        assert "BR-" in u.detail or re.search(r"\d\d:\d\d", u.detail), u
    assert len({u.detail for u in plan.unassigned}) > 1


def test_equipment_shortage_is_its_own_reason():
    """Нет роутеров ни у одной подходящей бригады — это не «не помещается по
    времени», а NO_EQUIPMENT, и диспетчеру нужно везти роутер, а не людей."""
    from dataclasses import replace
    from app.solver.engine import Problem, solve
    p = problem("Югоцентр")
    bare = [replace(e, equipment={k: n for k, n in e.equipment.items()
                                  if k != "router"}) for e in p.engineers]
    plan = solve(Problem(p.rd, bare, p.travel), time_limit_s=3)
    jobs = {j.id: j for j in p.rd.jobs}
    router = [u for u in plan.unassigned if "router" in jobs[u.job_id].equipment]
    assert router, "без роутеров заявки с роутером закрыть нельзя"
    for u in router:
        assert u.code == "NO_EQUIPMENT", u
        assert "Роутер" in u.reason and "Роутер" in u.detail


def test_window_closed_before_the_event():
    """После события в 15:40 заявка с окном 12:00–14:00 не «не помещается», а
    её окно уже закрылось: начать работу в прошлом нельзя. Отдельный код."""
    from app.solver.feasibility import Code
    from app.solver.replan import Event, apply_event
    p, plan = problem("Югоцентр"), short_fleet_plan("Югоцентр", 4)
    at = 15 * 60 + 40
    victim = next(r.engineer_id for r in plan.routes if r.stops)
    new, _ = apply_event(plan, p, Event(kind="unavailable", at_min=at,
                                        engineer_id=victim), time_limit_s=3)
    jobs = {j.id: j for j in p.rd.jobs}
    closed = [u for u in new.unassigned if jobs[u.job_id].win_end < at]
    assert closed, "у урезанного парка до события оставались незакрытые утренние заявки"
    for u in closed:
        assert u.code == Code.WINDOW_PASSED == "WINDOW_CLOSED_BEFORE_EVENT", u
        rem = why_unassigned(u.job_id, new, p).remedies[0]
        times = [t for t in re.findall(r"\b(\d\d):(\d\d)\b", rem.text)]
        if rem.kind in ("window", "fleet"):
            assert all(int(h) * 60 + int(m) >= at for h, m in times[:1]), rem.text


def test_remedy_in_the_list_is_the_first_one_in_the_card():
    """Список «Не назначено» показывает первое средство без клика; это ровно
    первая строка средств в карточке — одна функция, а не две."""
    from app.solver.explain import plan_days, remedy_line
    p = problem("Югоцентр")
    plan = short_fleet_plan("Югоцентр", 4)
    from app.solver.engine import Problem, subset_fleet
    reduced = Problem(p.rd, subset_fleet(p.engineers, 4), p.travel)
    jobs = {j.id: j for j in p.rd.jobs}
    days = plan_days(plan, reduced)
    for u in plan.unassigned:
        card = why_unassigned(u.job_id, plan, reduced)
        line = remedy_line(jobs[u.job_id], plan, reduced, days)
        assert line and line == card.remedies[0].text == card.remedy
        assert not JARGON.search(line) and not JARGON.search(card.detail)


# --- объяснение маршрута целиком (ТЗ 2.1.7, 2.4.2) ------------------------------

def test_route_explanation_matches_the_plan():
    """День бригады, порядок окон и ограничения выводятся из плана: числа
    совпадают с маршрутом, бригада без заявок объяснена как резерв."""
    from app.solver.explain_route import explain_route
    for region in REGIONS:
        p, plan = reference(region)
        jobs = {j.id: j for j in p.rd.jobs}
        for r in plan.routes:
            x = explain_route(r.engineer_id, plan, p)
            assert x["header"]["shift"] and x["header"]["skills"]
            assert x["summary"] and x["headline"].startswith(r.engineer_id)
            if not r.stops:
                assert x["status"] == "в резерве" and x["reserve"]["text"]
                continue
            d = x["day"]
            assert d["jobs"] == len(r.stops) and d["km"] == r.km
            assert d["span_min"] == r.span_min and d["limit_min"] == 12 * 60
            assert d["depart"] == hhmm(r.depart_min)
            assert d["wait_min"] == sum(s.wait_min for s in r.stops)
            for s in r.stops:
                j = jobs[s.job_id]
                if not j.floating_window:
                    assert f"{hhmm(j.win_start)}–{hhmm(j.win_end)}" in x["order"]["windows"]
            waits = {w["job_id"]: w for w in x["order"]["waits"]}
            assert set(waits) == {s.job_id for s in r.stops if s.wait_min > 0}
            for c in x["constraints"]:
                assert c["text"] and "rank" in c
            for t in _strings(x):
                assert not JARGON.search(t), t


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def test_waiting_is_explained_in_the_route():
    """Четырёхчасовой простой без слов читается как ошибка плана. В объяснении
    маршрута он назван: когда приедет, когда откроется окно, что было до."""
    from app.solver.explain_route import explain_route
    p, plan = reference("Юго-восток")
    r = max(plan.routes, key=lambda r: max((s.wait_min for s in r.stops), default=0))
    s = max(r.stops, key=lambda s: s.wait_min)
    assert s.wait_min >= 60, "в эталоне Юго-востока есть долгое ожидание окна"
    x = explain_route(r.engineer_id, plan, p)
    w = next(w for w in x["order"]["waits"] if w["job_id"] == s.job_id)
    assert hhmm(s.arrive_min) in w["text"] and hhmm(s.start_min) in w["text"]
    assert any(w["text"] == t for t in x["summary"])


# --- контрольный день -----------------------------------------------------------

def test_control_day_adds_up():
    """Все строки контрольного файла разложены без остатка: распределено +
    отменено клиентом + не отправлено = всего, а не 60 + 2 при 66. Дата
    в файле обезличена (куратор: «один из реальных дней сентября»), поэтому
    колонка называется «Реальный день», а не по дате."""
    from app.solver.compare import control_column
    for region in REGIONS:
        c = control_column(region)
        assert c["assigned"] + c["unassigned"] + c["cancelled"] == c["total_jobs"]
        assert c["title"] == "Реальный день" and "17.08" not in c["title"]
        assert "сентябр" in c["about"] and str(c["total_jobs"]) in c["breakdown"]
        assert "пробег невычислим" in c["note"]


# --- контракт API для интерфейса --------------------------------------------------

def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_api_explain_carries_passport_and_checked():
    c = _client()
    d = c.post("/api/plan", json={"region": "Юго-восток"}).json()
    stop = next(s for r in d["routes"] for s in r["stops"] if s["wait_min"] > 60)
    e = c.get(f"/api/explain/{d['plan_id']}/{stop['job_id']}").json()
    assert e["status"] == "назначена"
    assert {"passport", "checked", "schedule", "added_km", "position",
            "rejected_summary", "frozen"} <= set(e)
    assert e["schedule"]["wait_min"] == stop["wait_min"]
    assert "ждать открытия окна" in e["headline"]
    assert all(set(c_) == {"key", "label", "ok", "text"} for c_ in e["checked"])


def test_api_explain_route_and_reserve():
    """При выборе бригады правая панель не пустая: есть объяснение маршрута,
    а бригады без заявок перечислены в плане и тоже объясняются."""
    c = _client()
    d = c.post("/api/plan", json={"region": "Восток"}).json()
    busy = d["routes"][0]["engineer_id"]
    x = c.get(f"/api/explain_route/{d['plan_id']}/{busy}")
    assert x.status_code == 200, x.text
    x = x.json()
    assert x["status"] == "в работе" and x["plan_id"] == d["plan_id"]
    assert {"header", "day", "stops", "order", "constraints", "summary",
            "caveat"} <= set(x)
    assert d["reserve"], "в эталоне Востока есть бригады без заявок"
    idle = d["reserve"][0]["engineer_id"]
    y = c.get(f"/api/explain_route/{d['plan_id']}/{idle}").json()
    assert y["status"] == "в резерве" and y["reserve"]["text"]
    assert c.get(f"/api/explain_route/{d['plan_id']}/BR-НЕТ").status_code == 404
    assert c.get("/api/explain_route/нет-плана/BR-ВОС-01").status_code == 404


def test_api_unassigned_list_shows_remedy_without_click():
    c = _client()
    d = c.post("/api/plan", json={"region": "Югоцентр", "keep": 4,
                                  "time_limit_s": 3}).json()
    assert d["unassigned"]
    for u in d["unassigned"][:5]:
        assert u["remedy"], u
        e = c.get(f"/api/explain/{d['plan_id']}/{u['job_id']}").json()
        assert e["remedy"] == u["remedy"] == e["remedies"][0]["text"]
        assert e["passport"]["window"] and isinstance(e["blocked_by"], list)
