# -*- coding: utf-8 -*-
"""Контракт REST API.

Проверяется не «эндпоинт отвечает 200», а то, что в ответе лежит ровно то,
что ТЗ 2.4.2 перечисляет как обязательное. Ответ с кодом 200 и пустым телом
формально работает и не значит ничего.

Планы иммутабельны и живут в реестре по идентификатору. Это не оптимизация:
сравнение «до и после» требует, чтобы предыдущий план никуда не делся, а
пересчёт того же региона дал бы уже другой план, и разница оказалась бы
разницей между двумя разными случайностями.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
REGION = "Югоцентр"


@pytest.fixture(scope="module")
def plan_id():
    r = client.post("/api/plan", json={"region": REGION, "time_limit_s": 10})
    assert r.status_code == 200, r.text
    return r.json()["plan_id"]


def test_regions_are_listed():
    r = client.get("/api/regions")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    for item in data:
        assert {"name", "jobs", "engineers"} <= set(item)
        assert item["jobs"] > 0 and item["engineers"] > 0


def test_plan_endpoint_returns_required_fields():
    """ТЗ 2.4.2 перечисляет, что обязано быть в результате."""
    r = client.post("/api/plan", json={"region": REGION, "time_limit_s": 10})
    assert r.status_code == 200, r.text
    d = r.json()
    assert {"used_engineers", "total_km", "assigned", "unassigned", "routes"} <= set(d)
    for route in d["routes"]:
        assert {"engineer_id", "km", "stops"} <= set(route)
        for s in route["stops"]:
            assert {"job_id", "seq", "arrive", "start", "km"} <= set(s)
    for u in d["unassigned"]:
        assert u["reason"], "неназначенная заявка обязана иметь явную причину"


def test_plan_is_immutable_and_retrievable(plan_id):
    a = client.get(f"/api/plan/{plan_id}").json()
    b = client.get(f"/api/plan/{plan_id}").json()
    assert a == b, "сохранённый план изменился между двумя чтениями"
    assert a["plan_id"] == plan_id


def test_event_without_a_target_is_refused(plan_id):
    """Какая именно бригада выбыла — знает диспетчер, а не система. Угадывать
    здесь значит перепланировать день не тому человеку."""
    r = client.post("/api/event", json={"plan_id": plan_id, "kind": "unavailable",
                                        "at": "15:40"})
    assert r.status_code == 400
    assert "engineer_id" in r.json()["detail"]


def test_unknown_plan_is_a_clean_404():
    r = client.get("/api/plan/нет-такого")
    assert r.status_code == 404
    assert r.json()["detail"]


def test_unknown_region_is_a_clean_400():
    r = client.post("/api/plan", json={"region": "Марс"})
    assert r.status_code in (400, 422)


def test_baseline_is_a_separate_algorithm():
    r = client.post("/api/plan", json={"region": REGION, "algo": "baseline"})
    assert r.status_code == 200, r.text
    assert r.json()["algo"] == "baseline"


def test_compare_gives_three_columns():
    r = client.get("/api/compare", params={"region": REGION, "time_limit_s": 10})
    assert r.status_code == 200, r.text
    d = r.json()
    keys = {c["key"] for c in d["columns"]}
    assert keys == {"solver", "baseline", "control"}
    live = next(c for c in d["columns"] if c["key"] == "control")
    assert live["total_km"] is None, \
        "пробег живого дня невычислим — в контрольном файле нет порядка объезда"
    assert live["note"]


def test_explain_matches_the_plan(plan_id):
    plan = client.get(f"/api/plan/{plan_id}").json()
    stop = plan["routes"][0]["stops"][0]
    r = client.get(f"/api/explain/{plan_id}/{stop['job_id']}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "назначена"
    assert d["engineer_id"] == plan["routes"][0]["engineer_id"]
    assert d["headline"] and d["caveat"]
    assert 1 <= len(d["reasons"]) <= 3
    assert set(d["travel_by_mode"]) == {"car", "bike", "transit", "foot"}


def test_explain_of_unassigned_has_price_tag():
    r = client.post("/api/plan", json={"region": REGION, "keep": 4,
                                       "time_limit_s": 10})
    d = r.json()
    assert d["unassigned"], "при четырёх бригадах обязаны быть неназначенные"
    jid = d["unassigned"][0]["job_id"]
    e = client.get(f"/api/explain/{d['plan_id']}/{jid}").json()
    assert e["status"] == "не назначена"
    assert e["reason"] and e["remedies"]
    for rem in e["remedies"]:
        assert rem["action"] and rem["effect"]


def test_audit_reports_twelve_checks(plan_id):
    r = client.get(f"/api/audit/{plan_id}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] == 12
    assert d["passed"] == 12, d["violations"]
    assert len(d["checks"]) == 12
    assert d["headline"].startswith("Аудит 12/12")


def test_event_returns_new_plan_and_diff(plan_id):
    victim = client.get(f"/api/plan/{plan_id}").json()["routes"][0]["engineer_id"]
    r = client.post("/api/event", json={"plan_id": plan_id, "kind": "unavailable",
                                        "at": "15:40", "engineer_id": victim,
                                        "time_limit_s": 10})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["plan"]["plan_id"] != plan_id, "перепланирование затёрло старый план"
    assert d["diff"]["frozen"] > 0
    assert "used_engineers" in d["diff"]["metrics"]
    assert client.get(f"/api/plan/{plan_id}").status_code == 200, \
        "прежний план обязан остаться — иначе нечего показывать в сравнении"


def test_validate_move_tells_the_price(plan_id):
    plan = client.get(f"/api/plan/{plan_id}").json()
    stop = plan["routes"][0]["stops"][0]
    r = client.post("/api/validate_move",
                    json={"plan_id": plan_id, "job_id": stop["job_id"]})
    assert r.status_code == 200, r.text
    rows = r.json()["candidates"]
    n_engineers = next(x["engineers"] for x in client.get("/api/regions").json()
                       if x["name"] == REGION)
    assert len(rows) == n_engineers, "оценивать надо ВЕСЬ парк, а не подходящих"
    for row in rows:
        assert row["engineer_id"] and row["verdict"]
        if row["allowed"]:
            assert row["added_km"] is not None


def test_move_is_applied_or_refused_with_a_reason(plan_id):
    plan = client.get(f"/api/plan/{plan_id}").json()
    stop = plan["routes"][0]["stops"][0]
    cands = client.post("/api/validate_move",
                        json={"plan_id": plan_id, "job_id": stop["job_id"]}).json()
    allowed = [c for c in cands["candidates"]
               if c["allowed"] and c["engineer_id"] != plan["routes"][0]["engineer_id"]]
    target = allowed[0]["engineer_id"] if allowed else "BR-НЕТ-ТАКОЙ"
    r = client.post("/api/move", json={"plan_id": plan_id,
                                       "job_id": stop["job_id"],
                                       "engineer_id": target})
    if allowed:
        assert r.status_code == 200, r.text
        new = r.json()["plan"]
        assert new["plan_id"] != plan_id
        owner = {s["job_id"]: rt["engineer_id"] for rt in new["routes"]
                 for s in rt["stops"]}
        assert owner[stop["job_id"]] == target
        audit = client.get(f"/api/audit/{new['plan_id']}").json()
        assert audit["passed"] == audit["total"], audit["violations"]
    else:
        assert r.status_code == 400
        assert r.json()["detail"]


def test_export_is_a_csv_file(plan_id):
    r = client.get(f"/api/export/{plan_id}")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    body = r.content.decode("utf-8")
    assert body.startswith("﻿"), "без BOM Excel покажет кракозябры"
    assert "engineer_id" in body and "# суммарный пробег" in body


# --- эталонные планы (задача 6.5) --------------------------------------------

def test_plan_comes_from_the_reference_and_is_instant():
    """Эталон отдаётся мгновенно и совпадает с числами artifacts/summary.json.

    Двадцать секунд молчания на клик — половина беды. Вторая половина в том,
    что лимит солвера задан по настенным часам: под нагрузкой тот же вызов
    даёт другой план, и на экране окажутся не те числа, что на слайдах.
    """
    import json
    import pathlib
    import time

    root = pathlib.Path(__file__).resolve().parents[1]
    summary = json.loads((root / "artifacts" / "summary.json")
                         .read_text(encoding="utf-8"))
    want = next(r for r in summary["regions"] if r["region"] == REGION)

    t0 = time.time()
    d = client.post("/api/plan", json={"region": REGION}).json()
    elapsed = time.time() - t0

    assert elapsed < 1.0, f"эталон отдавался {elapsed:.1f} с"
    assert d["source"] == "эталон", d.get("warning")
    assert not d["warning"]
    assert d["used_engineers"] == want["plan"]["engineers"]
    assert d["assigned"] == want["plan"]["assigned"]
    assert d["late_jobs"] == want["plan"]["late"] == 0
    assert abs(d["total_km"] - want["plan"]["km"]) <= 0.05


def test_reference_plan_passes_the_audit():
    """Эталон разворачивается в настоящий план, а не в картинку: аудитор
    пересчитывает его по матрице с нуля и обязан сойтись."""
    d = client.post("/api/plan", json={"region": REGION}).json()
    a = client.get(f"/api/audit/{d['plan_id']}").json()
    assert a["passed"] == a["total"] == 12, a["violations"]


def test_reference_plan_supports_explain_and_events():
    """На эталоне обязаны работать те же объяснения и то же перепланирование,
    что и на живом прогоне — иначе демонстрация развалится на первом же клике."""
    d = client.post("/api/plan", json={"region": REGION}).json()
    stop = d["routes"][0]["stops"][0]
    e = client.get(f"/api/explain/{d['plan_id']}/{stop['job_id']}").json()
    assert e["engineer_id"] == d["routes"][0]["engineer_id"]

    ev = client.post("/api/event", json={
        "plan_id": d["plan_id"], "kind": "unavailable", "at": "15:40",
        "engineer_id": d["routes"][0]["engineer_id"], "time_limit_s": 10}).json()
    assert ev["diff"]["frozen"] > 0
    a = client.get(f"/api/audit/{ev['plan']['plan_id']}").json()
    assert a["passed"] == a["total"], a["violations"]


def test_recompute_runs_the_solver_live():
    """Кнопка «Пересчитать» — доказательство воспроизводимости, поэтому живой
    прогон обязан приходить к тем же числам, а не к любым."""
    d = client.post("/api/plan", json={"region": REGION, "recompute": True,
                                       "time_limit_s": 20}).json()
    assert d["source"] == "живой расчёт"
    ref = client.post("/api/plan", json={"region": REGION}).json()
    assert d["assigned"] == ref["assigned"]
    assert d["used_engineers"] <= ref["used_engineers"] + 1


def test_compare_uses_the_reference_too():
    d = client.get("/api/compare", params={"region": REGION}).json()
    assert d["sources"] == {"solver": "эталон", "baseline": "эталон"}
    assert not d["warnings"]
    solver = next(c for c in d["columns"] if c["key"] == "solver")
    ref = client.post("/api/plan", json={"region": REGION}).json()
    assert solver["used_engineers"] == ref["used_engineers"]


def test_missing_reference_degrades_with_a_warning(monkeypatch):
    """Нет артефакта — считаем живьём и ГОВОРИМ об этом. Отсутствие заранее
    посчитанного плана не повод оставить диспетчера без плана вообще."""
    import pathlib

    from app.api import artifacts

    monkeypatch.setattr(artifacts, "ARTIFACTS", pathlib.Path("/нет/такого/пути"))
    d = client.post("/api/plan", json={"region": REGION,
                                       "time_limit_s": 10}).json()
    assert d["source"] == "живой расчёт"
    assert "эталонного плана нет" in d["warning"]
    assert d["assigned"] > 0


def test_summary_screen_reads_artifacts_only():
    d = client.get("/api/summary").json()
    assert set(d["curves"]) == {"Восток", "Юго-восток", "Югоцентр"}
    for points in d["curves"].values():
        assert len(points) >= 3
        assert [p["fleet"] for p in points] == sorted(p["fleet"] for p in points)
        for p in points:
            assert {"fleet", "used", "km", "assigned", "unassigned"} <= set(p)
    assert d["caveat"]


def test_sensitivity_carries_its_caveat():
    """Оговорка едет вместе с данными и описывает, как они посчитаны. Таблица
    от основного плана (from_main_plan): первая строка — сам основной план, и
    ослабление правила не показывает лишней бригады. Прежняя таблица из
    отдельных прогонов — с оговоркой, что с планом дня её сравнивать нельзя."""
    d = client.get("/api/sensitivity").json()
    rows = d["regions"][REGION]
    assert rows[0]["scenario"] == "Как есть"
    if d.get("from_main_plan"):
        assert "основной план" in d["caveat"]
        plan = client.post("/api/plan", json={"region": REGION}).json()
        used = sum(1 for r in plan["routes"] if r["stops"])
        assert rows[0]["engineers"] == used
    else:
        assert "сравнивать нельзя" in d["caveat"]
    assert rows[0]["d_eng"] == 0 and rows[0]["d_km"] == 0.0
    soft = [r for r in rows if r["soft_windows"]]
    assert soft and d["soft_windows_note"]
    for r in rows:
        assert {"scenario", "engineers", "km", "d_eng", "d_km", "note"} <= set(r)


def test_convergence_is_served_with_its_caveat_or_named_as_missing(monkeypatch, tmp_path):
    """Кривая «время счёта -> результат» — заранее посчитанный замер. Нет
    файла — 503 с командой, которая его соберёт; есть — данные и оговорка."""
    import json

    from app.api import artifacts
    monkeypatch.setattr(artifacts, "ARTIFACTS", tmp_path)
    r = client.get("/api/convergence")
    assert r.status_code == 503 and "tools/convergence.py" in r.json()["detail"]
    (tmp_path / "convergence.json").write_text(json.dumps(
        {"machine": "M", "seconds_per_region": 600, "regions": {}, "table": []}),
        encoding="utf-8")
    d = client.get("/api/convergence").json()
    assert d["machine"] == "M" and "лучший план" in d["caveat"]


def test_regions_carry_the_live_day():
    """Числа живого дня приходят из контрольного файла вместе с регионом.
    Вписанные в код экрана, они рано или поздно разойдутся с источником."""
    for r in client.get("/api/regions").json():
        live = r["live_day"]
        assert {"engineers", "assigned", "late", "note"} <= set(live)
        assert live["engineers"] > 0 and live["assigned"] > 0
        assert "пробег невычислим" in live["note"]


def test_geometry_follows_the_roads():
    """Маршрут рисуется по форме дорог из офлайнового кэша. Плечо, которого в
    кэше нет, отдаётся прямой и ПОМЕЧАЕТСЯ: выдать прямую за дорогу нельзя."""
    d = client.post("/api/plan", json={"region": REGION}).json()
    g = client.get(f"/api/geometry/{d['plan_id']}").json()
    assert len(g["routes"]) == len([r for r in d["routes"] if r["stops"]])
    assert g["known_legs"] > 0
    for r in g["routes"]:
        plan_route = next(x for x in d["routes"] if x["engineer_id"] == r["engineer_id"])
        assert [l["job_id"] for l in r["legs"]] == [s["job_id"] for s in plan_route["stops"]]
        for leg in r["legs"]:
            assert len(leg["path"]) >= 2
            if not leg["estimated"]:
                assert len(leg["path"]) > 2, "плечо по дорогам не бывает отрезком"


def test_geometry_marks_unknown_legs_as_estimated():
    """Урезанный парк рождает плечи, которых в кэше нет: они обязаны приехать
    с пометкой, а не молча прямой линией."""
    d = client.post("/api/plan", json={"region": REGION, "keep": 4,
                                       "time_limit_s": 10}).json()
    g = client.get(f"/api/geometry/{d['plan_id']}").json()
    legs = [l for r in g["routes"] for l in r["legs"]]
    guessed = [l for l in legs if l["estimated"]]
    assert guessed, "при урезанном парке обязаны появиться плечи без геометрии"
    assert all(len(l["path"]) == 2 for l in guessed)
    assert g["note"]


def test_demo_emergency_lands_in_the_scarcest_zone():
    """Авария у офиса ничего не доказывает: её возьмёт ближайшая из десяти
    московских бригад. В самой дефицитной зоне одно событие показывает и зону,
    и требование транспорта, и заморозку факта."""
    import sys
    from app.solver.engine import build_problem
    from app.solver.replan import make_demo_emergency
    assert sys  # импорт ради явности зависимости от бэкенда

    p = build_problem("Юго-восток")
    job = make_demo_emergency(p, 15 * 60 + 40)
    fleet = {}
    for e in p.engineers:
        fleet[e.cluster] = fleet.get(e.cluster, 0) + 1
    remote = {k: v for k, v in fleet.items() if k != "Москва"}
    assert job.cluster == min(remote, key=lambda k: (remote[k], k))
    assert job.requires_transport == "car"
    assert job.skill == "emergency"


def test_move_names_the_cost_in_the_mandatory_metric(plan_id):
    """Отдать заявку незанятой бригаде — значит поднять «задействовано
    исполнителей» на единицу. Это дороже любых километров, и вердикт обязан
    это назвать: иначе диспетчер увидит «+12 км» и не поймёт, почему вырос
    главный счётчик."""
    plan = client.get(f"/api/plan/{plan_id}").json()
    stop = plan["routes"][0]["stops"][0]
    rows = client.post("/api/validate_move",
                       json={"plan_id": plan_id, "job_id": stop["job_id"]}
                       ).json()["candidates"]
    working = {r["engineer_id"] for r in plan["routes"] if r["stops"]}
    for r in rows:
        assert r["idle"] == (r["engineer_id"] not in working)
        if r["idle"] and r["allowed"]:
            assert "исполнителей станет на одного больше" in r["verdict"]


def test_urgent_event_accepts_an_address_instead_of_coordinates(plan_id):
    """Диспетчер вводит адрес, а не широту с долготой. Координаты берутся из
    офлайнового геокэша. OSRM не ответил (в тестах сети нет) — плечи до новой
    точки честно помечаются оценкой: дорожная матрица посчитана заранее и
    строки под этот адрес не содержит."""
    from app.geo import geocode
    known = next(iter(geocode.cache()))
    r = client.post("/api/event", json={
        "plan_id": plan_id, "kind": "urgent", "at": "15:40", "time_limit_s": 10,
        "job": {"id": "AVARIA-ADDR", "address": known, "required_skill": "Аварийные работы",
                "duration_min": 60, "window_start": "15:40", "window_end": "17:40",
                "required_transport": "Автомобиль"},
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["geocode"]["source"] == "кэш", d["geocode"]
    placed = [s for route in d["plan"]["routes"] for s in route["stops"]
              if s["job_id"] == "AVARIA-ADDR"]
    assert placed, "срочная заявка не попала в план"
    assert placed[0]["estimated"] is True, \
        "плечо до достроенной точки выдано за измеренное по дорогам"


def test_urgent_event_takes_roads_from_osrm_when_it_answers(plan_id, monkeypatch):
    """Есть сеть — дороги до нового адреса берутся у OSRM, плечо не оценка, и
    пометка к адресу говорит об этом, а не о «прямой»."""
    from app.geo import geocode, roads

    def table(coords, timeout=0):
        n = len(coords)
        lat, lon = coords[-1]
        return {"code": "Ok",
                "distances": [[0 if i == j else 1500 for j in range(n)] for i in range(n)],
                "durations": [[0 if i == j else 180 for j in range(n)] for i in range(n)],
                "sources": [{"location": [0.0, 0.0]}] * (n - 1)
                           + [{"location": [lon, lat]}]}
    monkeypatch.setattr(roads, "ask_table", table)
    known = next(iter(geocode.cache()))
    r = client.post("/api/event", json={
        "plan_id": plan_id, "kind": "urgent", "at": "15:40", "time_limit_s": 10,
        "job": {"id": "AVARIA-ROADS", "address": known, "required_skill": "Аварийные работы",
                "duration_min": 60, "window_start": "15:40", "window_end": "17:40",
                "required_transport": "Автомобиль"},
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["geocode"]["roads"] == "по дорогам", d["geocode"]
    assert "по дорогам" in d["geocode"]["note"]
    placed = [s for route in d["plan"]["routes"] for s in route["stops"]
              if s["job_id"] == "AVARIA-ROADS"]
    assert placed and placed[0]["estimated"] is False


def test_urgent_event_without_coordinates_and_without_network_says_why(plan_id, monkeypatch):
    """Нет сети и нет адреса в кэше — это 400 с человеческой причиной, а не
    500. Для диспетчера разница между «нет сети» и «система сломалась» решающая."""
    from app.geo import geocode
    monkeypatch.setattr(geocode, "ask_geocoder",
                        lambda address, timeout=0, area=None: (_ for _ in ()).throw(
                            geocode.GeocodeUnavailable("сеть недоступна: URLError")))
    r = client.post("/api/event", json={
        "plan_id": plan_id, "kind": "urgent", "at": "15:40",
        "job": {"id": "AVARIA-NOWHERE", "address": "Москва, улица Несуществующая, 1",
                "required_skill": "Аварийные работы", "duration_min": 60,
                "window_start": "15:40", "window_end": "17:40"},
    })
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "геокодер недоступен" in detail and "координаты" in detail, detail


def test_urgent_event_takes_just_an_address(plan_id):
    """Путь интерфейса: диспетчер вводит один адрес. Длительность берётся из
    норматива, окно — из времени события, зона считается по адресу."""
    from app.geo import geocode
    known = next(iter(geocode.cache()))
    r = client.post("/api/event", json={
        "plan_id": plan_id, "kind": "urgent", "at": "15:40",
        "address": known, "time_limit_s": 10,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["geocode"]["source"] == "кэш"
    assert d["geocode"]["address"] == known
    assert d["geocode"]["name"], "диспетчер должен видеть, куда встала точка"
    added = [s for route in d["plan"]["routes"] for s in route["stops"]
             if s["address"] == known]
    unplaced = [u for u in d["plan"]["unassigned"] if u["address"] == known]
    assert added or unplaced, "авария по введённому адресу исчезла из ответа"


# --- события: время, режим, разница, перенос после события ------------------

@pytest.mark.parametrize("at", ["abc", "99:99", "24:00", "7:05", "-05:00", "", "15:4"])
def test_event_time_must_be_hhmm(plan_id, at):
    """Время события — ЧЧ:ММ от 00:00 до 23:59, иначе 422, а не 500. «99:99»
    не становится молча 6039-й минутой, замораживающей весь день."""
    r = client.post("/api/event", json={"plan_id": plan_id, "kind": "cancel",
                                        "at": at, "job_id": "1"})
    assert r.status_code == 422, r.text


def test_new_job_window_must_be_hhmm(plan_id):
    job = {"id": "BAD-WIN", "lat": 55.66, "lon": 37.62, "duration_min": 40,
           "window_start": "25:00", "window_end": "26:00",
           "required_skill": "Локальные работы"}
    r = client.post("/api/event", json={"plan_id": plan_id, "kind": "urgent",
                                        "at": "15:40", "job": job})
    assert r.status_code == 422, r.text


def _ordinary(job_id, window=("16:00", "18:00"), **extra):
    return dict(dict(id=job_id, lat=55.66, lon=37.62, address="Москва, тестовый адрес",
                     duration_min=40, window_start=window[0], window_end=window[1],
                     priority=1, required_skill="Локальные работы"), **extra)


@pytest.fixture(scope="module")
def after_event():
    """Эталон Югоцентра и отмена одной вечерней заявки в 15:40: план после
    события — с моментом события и пометкой замороженного."""
    d = client.post("/api/plan", json={"region": REGION}).json()
    busiest = max(d["routes"], key=lambda r: len(r["stops"]))
    late = [s["job_id"] for s in busiest["stops"] if s["start"] > "17:00"]
    r = client.post("/api/event", json={"plan_id": d["plan_id"], "kind": "cancel",
                                        "at": "15:40", "job_id": late[-1],
                                        "time_limit_s": 1})
    assert r.status_code == 200, r.text
    return d, r.json()


def test_plan_after_event_carries_the_freeze(after_event):
    """Что заморожено, план помнит сам: по этой пометке аудитор проверяет
    прошлое, а перенос отказывается его трогать."""
    _, ev = after_event
    meta = ev["plan"]["meta"]
    assert meta["at_min"] == 15 * 60 + 40 and meta["frozen"]
    for rec in meta["frozen"].values():
        assert {"engineer_id", "start_min", "end_min", "state"} <= set(rec)
        assert rec["state"] in ("started", "en_route")
    assert ev["diff"]["cancelled"] and ev["diff"]["is_quiet"] is False, \
        "отмена — изменение плана, а не «план не изменился»"
    a = client.get(f"/api/audit/{ev['plan']['plan_id']}").json()
    assert a["passed"] == a["total"] == 12, a["violations"]


def test_rejected_new_job_is_named_in_the_diff(plan_id):
    """Авария, которую не удалось принять, — изменение, а не «план не
    изменился»: она едет в diff.rejected с причиной."""
    job = dict(id="AVARIA-KASHIRA", lat=54.84, lon=38.17, address="Кашира, улица Ленина, 1",
               duration_min=90, window_start="15:40", window_end="17:40",
               priority=2, required_skill="Аварийные работы")
    r = client.post("/api/event", json={"plan_id": plan_id, "kind": "urgent",
                                        "at": "15:40", "time_limit_s": 1, "job": job})
    assert r.status_code == 200, r.text
    diff = r.json()["diff"]
    assert [x["job_id"] for x in diff["rejected"]] == ["AVARIA-KASHIRA"]
    assert diff["rejected"][0]["reason"] and diff["is_quiet"] is False


def test_event_cannot_go_back_in_time(after_event):
    _, ev = after_event
    r = client.post("/api/event", json={"plan_id": ev["plan"]["plan_id"],
                                        "kind": "urgent", "at": "15:00",
                                        "job": _ordinary("NEW-EARLIER")})
    assert r.status_code == 400 and "раньше предыдущего" in r.json()["detail"]


def test_started_work_cannot_be_moved_after_the_event(after_event):
    """Начатую работу не переносят: заявку, законченную в 10:30, в 15:40
    нельзя отдать другой бригаде «на 11:13»."""
    _, ev = after_event
    pid = ev["plan"]["plan_id"]
    frozen = ev["plan"]["meta"]["frozen"]
    jid, rec = next((j, x) for j, x in frozen.items() if x["state"] == "started")
    other = next(rt["engineer_id"] for rt in ev["plan"]["routes"]
                 if rt["engineer_id"] != rec["engineer_id"])
    r = client.post("/api/move", json={"plan_id": pid, "job_id": jid,
                                       "engineer_id": other})
    assert r.status_code == 400 and "поздно" in r.json()["detail"], r.text
    v = client.post("/api/validate_move", json={"plan_id": pid, "job_id": jid}).json()
    assert v["locked"] and "поздно" in v["locked"]
    assert not any(c["allowed"] for c in v["candidates"])


def test_validate_and_move_agree_after_the_event(after_event):
    """validate_move обязан давать тот же вердикт, что и move, по каждой
    бригаде; перенос не трогает замороженное и не ставит работу в прошлое."""
    _, ev = after_event
    plan = ev["plan"]
    frozen = plan["meta"]["frozen"]
    stop = next(s for rt in plan["routes"] for s in rt["stops"]
                if s["job_id"] not in frozen)
    rows = client.post("/api/validate_move", json={
        "plan_id": plan["plan_id"], "job_id": stop["job_id"]}).json()["candidates"]
    assert any(r["allowed"] and not r["is_current"] for r in rows)
    for row in rows:
        r = client.post("/api/move", json={"plan_id": plan["plan_id"],
                                           "job_id": stop["job_id"],
                                           "engineer_id": row["engineer_id"]})
        assert (r.status_code == 200) == row["allowed"], (row, r.text)
        if r.status_code != 200:
            continue
        moved = r.json()["plan"]
        assert moved["meta"]["frozen"] == frozen, "перенос потерял пометку заморозки"
        placed = next(s for rt in moved["routes"] for s in rt["stops"]
                      if s["job_id"] == stop["job_id"])
        assert placed["start"] >= "15:40"
        a = client.get(f"/api/audit/{moved['plan_id']}").json()
        assert a["passed"] == a["total"] == 12, a["violations"]


# --- обычная заявка и авария: два режима -------------------------------------

def test_ordinary_new_job_is_inserted_and_says_so():
    """Куратор, 22.09: обычная заявка встаёт в свободный интервал и не
    перестраивает план. Режим назван в ответе и в заголовке разницы."""
    d = client.post("/api/plan", json={"region": REGION}).json()
    r = client.post("/api/event", json={"plan_id": d["plan_id"], "kind": "urgent",
                                        "at": "15:40", "job": _ordinary("NEW-API")})
    assert r.status_code == 200, r.text
    ev = r.json()
    assert ev["mode"] == ev["diff"]["mode"] == "insert"
    assert "вставка" in ev["diff"]["event"] and ev["diff"]["mode_text"]
    assert [a["job_id"] for a in ev["diff"]["added"]] == ["NEW-API"]
    assert not (ev["diff"]["moved"] or ev["diff"]["dropped"] or ev["diff"]["rejected"]
                or ev["diff"]["resequenced"])
    a = client.get(f"/api/audit/{ev['plan']['plan_id']}").json()
    assert a["passed"] == a["total"] == 12, a["violations"]


def test_declared_urgent_priority_replans(plan_id):
    """«Срочная» в поле приоритета делает заявку аварией: она вправе
    перепланировать остаток дня, а не ждать свободного интервала."""
    r = client.post("/api/event", json={
        "plan_id": plan_id, "kind": "urgent", "at": "15:40", "time_limit_s": 1,
        "job": _ordinary("URGENT-API", priority="Срочная")})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "replan"
    stop = next((s for rt in r.json()["plan"]["routes"] for s in rt["stops"]
                 if s["job_id"] == "URGENT-API"), None)
    assert stop is None or stop["priority"] == 1


def test_explain_of_cancelled_job_is_not_500():
    """Объяснение отменённой событием заявки — ответ словами, а не 500."""
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    p = c.post("/api/plan", json={"region": "Восток"}).json()
    jid = p["routes"][0]["stops"][-1]["job_id"]
    ev = c.post("/api/event", json={"plan_id": p["plan_id"], "kind": "cancel",
                                    "job_id": jid, "at": "09:00", "time_limit_s": 5})
    assert ev.status_code == 200, ev.text
    r = c.get(f"/api/explain/{ev.json()['plan']['plan_id']}/{jid}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "отменена"


def test_late_event_is_answered_not_500():
    """Авария поздно вечером: день бригад по нормативу уже закончился.

    Пустой диапазон времени у таких бригад не делает неразрешимой всю задачу:
    авария честно не принимается с причиной, а не роняет API в 500.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    p = c.post("/api/plan", json={"region": "Юго-восток"}).json()
    r = c.post("/api/event", json={"plan_id": p["plan_id"], "kind": "urgent",
                                   "at": "21:40", "time_limit_s": 5})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["diff"]["rejected"] or d["diff"]["added"]
    audit = c.get(f"/api/audit/{d['plan']['plan_id']}").json()
    assert audit["passed"] == audit["total"]


def test_second_demo_emergency_in_a_chain_gets_its_own_id():
    """Две показательные аварии подряд: вторая не должна упираться в
    «заявка с таким номером уже есть»."""
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    p = c.post("/api/plan", json={"region": "Юго-восток"}).json()
    e1 = c.post("/api/event", json={"plan_id": p["plan_id"], "kind": "urgent",
                                    "at": "15:40", "time_limit_s": 5})
    assert e1.status_code == 200, e1.text
    e2 = c.post("/api/event", json={"plan_id": e1.json()["plan"]["plan_id"],
                                    "kind": "urgent", "at": "17:10", "time_limit_s": 5})
    assert e2.status_code == 200, e2.text
    assert "AVARIA-DEMO-2" in e2.json()["diff"]["event"]
