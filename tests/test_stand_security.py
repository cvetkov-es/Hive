# -*- coding: utf-8 -*-
"""Стенд открыт в интернет: что анонимный посетитель может заставить сделать.

Три угрозы, от которых стенд закрыт:
  * путь из адреса выходит за папку сборки интерфейса — через %2e%2e/
    можно прочитать любой файл, доступный процессу (/etc/hostname, домашняя
    папка);
  * тяжёлые расчёты без предела запускают сколько угодно разом — стенд
    встаёт для всех, кто смотрит его одновременно;
  * каждый план пишется в runs/, и открытие страницы в цикле заполняет диск.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import WEB_DIST, app

client = TestClient(app)
UP = "%2e%2e/" * 8


@pytest.mark.parametrize("path", [
    f"/{UP}etc/hostname",
    f"/{UP}etc/passwd",
    "/%2e%2e/%2e%2e/backend/app/config.py",
    "/..%2f..%2fbackend%2fapp%2fconfig.py",
    "/fonts/%2e%2e/%2e%2e/%2e%2e/backend/app/config.py",
])
def test_no_file_outside_the_built_interface(path):
    r = client.get(path)
    assert "Константы модели" not in r.text
    assert "root:" not in r.text
    if r.status_code == 200:
        # Непонятный путь — это страница интерфейса, а не файл с диска.
        assert r.text == (WEB_DIST / "index.html").read_text(encoding="utf-8")


def test_files_of_the_interface_are_still_served():
    font = next((WEB_DIST / "fonts").glob("*.woff2"))
    r = client.get(f"/fonts/{font.name}")
    assert r.status_code == 200 and r.content == font.read_bytes()
    assert client.get("/").status_code == 200
    assert client.get("/plan/какой-то-адрес-интерфейса").status_code == 200


def test_heavy_computations_are_limited(monkeypatch):
    """Свободных мест под расчёт нет — отказ сразу и словами, а не очередь,
    в которой стенд стоит для всех."""
    from app.api import routes
    taken = [routes.HEAVY.acquire(blocking=False) for _ in range(routes.HEAVY_MAX)]
    assert all(taken)
    try:
        r = client.post("/api/plan", json={"region": "Югоцентр", "recompute": True,
                                           "time_limit_s": 1})
        assert r.status_code == 503, r.text
        assert "расчёт" in r.json()["detail"]
        # Заранее посчитанный план расчёта не требует и отдаётся всегда.
        r = client.post("/api/plan", json={"region": "Югоцентр"})
        assert r.status_code == 200, r.text
    finally:
        for _ in taken:
            routes.HEAVY.release()


def test_runs_folder_keeps_only_the_latest_plans(monkeypatch, tmp_path):
    from app.api import state
    monkeypatch.setattr(state, "RUNS", tmp_path)
    monkeypatch.setattr(state, "RUNS_MAX", 3)
    for _ in range(6):
        client.post("/api/plan", json={"region": "Югоцентр"})
    assert len(list(tmp_path.glob("*.json"))) == 3


# --- ввод посетителя и внешние сервисы --------------------------------------

@pytest.fixture(scope="module")
def plan_id():
    return client.post("/api/plan", json={"region": "Югоцентр"}).json()["plan_id"]


@pytest.mark.parametrize("job", [
    {"id": "<img src=x onerror=alert(1)>", "lat": 55.66, "lon": 37.62},
    {"id": "X" * 41, "lat": 55.66, "lon": 37.62},
    {"id": "EVIL-1", "address": "Москва, <script>alert(1)</script>"},
    {"id": "EVIL-2", "address": "Москва, " + "а" * 300},
])
def test_new_job_text_cannot_carry_markup(plan_id, job):
    """Номер и адрес новой заявки попадают в подсказки карты как HTML. Разметку
    и заведомо не адресную длину сервер не принимает — это страховка на
    случай, если интерфейс где-то не экранирует."""
    body = dict(duration_min=40, window_start="16:00", window_end="18:00",
                required_skill="Локальные работы", **job)
    r = client.post("/api/event", json={"plan_id": plan_id, "kind": "urgent",
                                        "at": "15:40", "job": body})
    assert r.status_code in (400, 422), r.text


def test_address_of_the_demo_emergency_cannot_carry_markup(plan_id):
    r = client.post("/api/event", json={"plan_id": plan_id, "kind": "urgent",
                                        "at": "15:40", "address": "<b>Москва</b>"})
    assert r.status_code in (400, 422), r.text


def test_time_limit_is_capped():
    r = client.post("/api/plan", json={"region": "Югоцентр", "recompute": True,
                                       "time_limit_s": 31})
    assert r.status_code == 422


def test_outbound_requests_are_spaced():
    """Nominatim: не больше запроса в секунду, иначе блокируют IP всего
    сервера. Обычная новая заявка решатель не запускает, так что предел
    расчётов её частоту не держит — держит пауза между запросами."""
    import time
    from app.geo.polite import Throttle
    th = Throttle(0.2)
    t0 = time.monotonic()
    for _ in range(3):
        th.wait()
    assert time.monotonic() - t0 >= 0.4


def test_geocoder_session_memory_is_bounded(monkeypatch):
    from app.geo import geocode
    monkeypatch.setattr(geocode, "SESSION_MAX", 3)
    monkeypatch.setattr(geocode, "_session", {})
    monkeypatch.setattr(geocode, "ask_geocoder", lambda address, timeout=0, area=None:
                        [geocode.Candidate(55.75, 37.61, address)])
    for i in range(6):
        geocode.resolve(f"Москва, улица Пробная, {i}")
    assert len(geocode._session) == 3
