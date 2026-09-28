# -*- coding: utf-8 -*-
"""Геокодирование в рантайме — штатная деградация, а не аварийный случай.

Рантайм работает офлайн: все адреса выгрузки разобраны заранее и лежат в
геокэше. Диспетчер вправе ввести адрес, которого там нет, и у системы ровно
три честных исхода: взять из кэша, спросить геокодер один раз, отказать
словами, которые называют причину. Четвёртого — «поставить точку куда-нибудь
и посчитать по ней километры» — быть не должно: по этим километрам принимают
решение, и выдавать выдумку за измерение здесь дороже, чем отказать.
"""
import pytest

from app.geo import geocode


def test_known_address_comes_from_cache_without_network(monkeypatch):
    """Сеть не трогается там, где ответ уже есть. Иначе демонстрация без
    интернета начинает зависеть от интернета."""
    def forbidden(address, timeout=0):
        raise AssertionError(f"запрос в сеть за известным адресом: {address}")

    monkeypatch.setattr(geocode, "ask_geocoder", forbidden)
    known = next(iter(geocode.cache()))
    point = geocode.resolve(known)
    assert point.source == "кэш"
    assert 54 < point.lat < 57 and 35 < point.lon < 40


def test_lookup_ignores_case_and_spacing(monkeypatch):
    monkeypatch.setattr(geocode, "ask_geocoder",
                        lambda address, timeout=0: pytest.fail("лишний запрос"))
    known = next(iter(geocode.cache()))
    point = geocode.resolve(f"  {known.upper()}  ")
    assert point.source == "кэш"


def test_unknown_address_asks_geocoder_once(monkeypatch):
    calls = []

    def fake(address, timeout=0, area=None):
        calls.append(address)
        return [geocode.Candidate(55.7512, 37.6184, "Красная площадь, Москва")]

    monkeypatch.setattr(geocode, "ask_geocoder", fake)
    point = geocode.resolve("Москва, Красная площадь, 1")
    assert len(calls) == 1
    assert point.source == "геокодер"
    assert (point.lat, point.lon) == (55.7512, 37.6184)
    assert "прямой" in point.note, point.note
    assert point.name == "Красная площадь, Москва"

    # второй раз — уже из памяти сеанса, повторного запроса нет
    geocode.resolve("Москва, Красная площадь, 1")
    assert len(calls) == 1


def test_without_network_refuses_in_words(monkeypatch):
    """Отказ обязан называть причину и выход. Молчаливое исключение для
    диспетчера выглядит как «система сломалась», а не как «нет сети»."""
    def offline(address, timeout=0, area=None):
        raise geocode.GeocodeUnavailable("сеть недоступна")

    monkeypatch.setattr(geocode, "ask_geocoder", offline)
    with pytest.raises(geocode.GeocodeError) as exc:
        geocode.resolve("Москва, улица которой нет, 1", cache_only=False)
    text = str(exc.value)
    assert "координат" in text and "сеть" in text, text


def test_cache_only_never_goes_to_network(monkeypatch):
    monkeypatch.setattr(geocode, "ask_geocoder",
                        lambda address, timeout=0: pytest.fail("запрос при cache_only"))
    with pytest.raises(geocode.GeocodeError):
        geocode.resolve("Москва, улица которой нет, 2", cache_only=True)


def test_empty_address_is_rejected():
    with pytest.raises(geocode.GeocodeError):
        geocode.resolve("   ")


# --- живой запрос: сборка, ошибки, выбор варианта ----------------------------

VOSTOK = geocode.Area("Восток", (("Офис «Восток»", 55.6997977, 37.7725762),))
# Два ответа Nominatim на один и тот же адрес — реальный случай из нашего
# кэша: «ул. Талалихина, 16» из Таганского района находится и в Щербинке.
TAGANKA = geocode.Candidate(55.7369925, 37.6733454,
                            "16, улица Талалихина, Таганский район, Москва, 109316, Россия")
SHCHERBINKA = geocode.Candidate(55.5066988, 37.5819505,
                                "16, улица Талалихина, Липки, район Щербинка, Москва, "
                                "117623, Россия")


def test_request_headers_survive_http_client_encoding():
    """http.client кодирует заголовки в latin-1. Кириллица в User-Agent роняет
    каждый запрос мимо кэша ещё до выхода в сеть. Проверка без сети:
    putrequest и putheader собирают запрос, соединение не открывается."""
    import http.client

    req = geocode.build_request("Москва, улица Талалихина, 16", VOSTOK)
    conn = http.client.HTTPConnection("nominatim.invalid")
    try:
        conn.putrequest("GET", req.selector)
        for key, value in req.header_items():
            conn.putheader(key, value)             # наши заголовки проходят
        with pytest.raises(UnicodeEncodeError):    # кириллица здесь падает
            conn.putheader("User-Agent", "hive-dispatch/1.0 (ЛЦТ-2026)")
    finally:
        conn.close()
    geocode.USER_AGENT.encode("latin-1")
    req.full_url.encode("ascii")


def test_request_is_bounded_to_the_zone():
    """Пять вариантов и прямоугольник вокруг стартовой точки зоны: улица-тёзка
    в другом конце области в ответ попасть не должна."""
    import urllib.parse

    req = geocode.build_request("Москва, улица Талалихина, 16", VOSTOK)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
    assert q["limit"] == ["5"] and q["bounded"] == ["1"]
    lon1, lat1, lon2, lat2 = map(float, q["viewbox"][0].split(","))
    assert lon1 < 37.7725762 < lon2 and lat2 < 55.6997977 < lat1


def test_encoding_failure_is_not_called_a_network_failure(monkeypatch):
    """Ошибка сборки запроса — ошибка системы, а не «сеть недоступна»: при
    живой сети такая надпись отправляет человека чинить не то."""
    def broken(req, timeout=0):
        "ЛЦТ".encode("latin-1")

    monkeypatch.setattr(geocode.urllib.request, "urlopen", broken)
    with pytest.raises(geocode.GeocodeError) as exc:
        geocode.resolve("Москва, улица которой нет, 3")
    text = str(exc.value)
    assert "сеть недоступна" not in text and "собрать" in text, text


def test_network_failure_is_named_as_such(monkeypatch):
    import urllib.error

    def offline(req, timeout=0):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(geocode.urllib.request, "urlopen", offline)
    with pytest.raises(geocode.GeocodeError) as exc:
        geocode.resolve("Москва, улица которой нет, 4")
    text = str(exc.value)
    assert "геокодер недоступен" in text and "сеть недоступна" in text, text


def test_district_from_the_address_wins():
    """Район, названный диспетчером, решает: Щербинка ближе к офису не станет."""
    both = [SHCHERBINKA, TAGANKA]
    assert geocode.choose("Москва, Таганский район, ул. Талалихина, 16",
                          both, VOSTOK) == TAGANKA
    assert geocode.choose("Москва, район Щербинка, ул. Талалихина, 16",
                          [TAGANKA, SHCHERBINKA], VOSTOK) == SHCHERBINKA


def test_without_a_district_the_nearest_wins():
    """Район не назван — берётся вариант ближе к стартовой точке зоны: заявка
    региона скорее лежит в регионе. Так заявка 48227 встаёт в Таганский район."""
    assert geocode.choose("Москва, ул. Талалихина, 16",
                          [SHCHERBINKA, TAGANKA], VOSTOK) == TAGANKA


def test_far_result_is_refused_in_words(monkeypatch):
    """Точка за 25 км от стартовой точки зоны — не точка, а отказ, который
    называет, что нашлось, как далеко и что делать."""
    kashira = geocode.Candidate(54.8375, 38.1678, "улица Ленина, Кашира, Московская область")
    monkeypatch.setattr(geocode, "ask_geocoder",
                        lambda address, timeout=0, area=None: [kashira])
    with pytest.raises(geocode.GeocodeError) as exc:
        geocode.resolve("улица Ленина, 1", area=VOSTOK)
    text = str(exc.value)
    assert "Кашира" in text and "км" in text and "координаты" in text, text


def test_zone_of_the_address_picks_the_start_point():
    """Адрес Каширы в Юго-востоке ищется вокруг «дома» бригад Каширы, а не
    вокруг офиса в 91 км от неё; в регионе без Каширы — вокруг офиса, и тогда
    честно отказывает."""
    from helpers import problem

    se = problem("Юго-восток").rd.depots.values()
    kashira = geocode.area_for("Юго-восток", se, "Кашира, улица Советская, 5")
    assert len(kashira.anchors) == 1 and "Кашира" in kashira.anchors[0][0]
    moscow = geocode.area_for("Юго-восток", se, "Москва, Бирюлёвская, 1")
    assert "Офис" in moscow.anchors[0][0]
    east = geocode.area_for("Восток", problem("Восток").rd.depots.values(),
                            "Кашира, улица Советская, 5")
    assert "Офис" in east.anchors[0][0]


def test_point_names_the_place(monkeypatch):
    """Диспетчер видит, куда встала точка: название едет вместе с координатами,
    и из кэша тоже."""
    monkeypatch.setattr(geocode, "ask_geocoder",
                        lambda address, timeout=0, area=None: pytest.fail("сеть"))
    point = geocode.resolve(next(iter(geocode.cache())), area=VOSTOK)
    assert point.name and point.km is not None and point.anchor


def test_manual_override_beats_the_cache(tmp_path, monkeypatch):
    """Ручная правка координат важнее кэша — как при чтении выгрузки. Иначе
    исправленный адрес, набранный диспетчером, вставал бы в старую ошибку."""
    import json

    geo = tmp_path / "geo"
    geo.mkdir()
    addr = "Город Москва, ул.Талалихина, д. 16"
    (geo / "geocode.json").write_text(json.dumps({addr: dict(
        lat=SHCHERBINKA.lat, lon=SHCHERBINKA.lon, name=SHCHERBINKA.name,
        query="Москва, Талалихина улица, 16")}, ensure_ascii=False), encoding="utf-8")
    (geo / "overrides.json").write_text(json.dumps({
        "_comment": "ручные координаты",
        addr: dict(lat=TAGANKA.lat, lon=TAGANKA.lon, source="по колонке Район")},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(geocode, "DATA", tmp_path)
    geocode.cache.cache_clear()
    try:
        for typed in (addr, "Москва, Талалихина улица, 16"):
            point = geocode.resolve(typed)
            assert (point.lat, point.lon) == (TAGANKA.lat, TAGANKA.lon), typed
            assert "Щербинка" not in point.name
    finally:
        geocode.cache.cache_clear()
