# -*- coding: utf-8 -*-
"""Адрес -> координаты в рантайме.

Рантайм работает офлайн: адреса выгрузки разобраны заранее (tools/normalize_addr.py),
их координаты лежат в data/geo/geocode.json и коммитятся. Этот модуль нужен для
одного случая — диспетчер вводит адрес, которого в выгрузке не было: авария на
объекте, которого сегодня утром ещё не существовало в списке.

Порядок жёсткий: кэш, память сеанса, один запрос к геокодеру. Если сети нет —
отказ словами, которые называют причину и выход. Четвёртого пути, «поставить
точку примерно туда», здесь нет: дальше по этой точке считается пробег, по
пробегу принимается решение, и выдать выдумку за измерение дороже, чем отказать.

Первому ответу геокодера тоже верить нельзя. В нашем же кэше «ул. Талалихина,
16» из Таганского района встала в Щербинку: у Nominatim было шесть вариантов,
взят первый. Поэтому живой запрос строже офлайновой подготовки:

  * поиск ограничен прямоугольником вокруг стартовой точки зоны, к которой
    адрес относится (viewbox, bounded=1): улица-тёзка в другом конце области
    в ответ не попадает;
  * вариантов пять, и выбирается тот, в чьём названии больше слов из
    введённого адреса — города, района, улицы; при равенстве — ближайший;
  * точка дальше 25 км от стартовой точки зоны (офиса региона или «дома»
    удалённого города) — отказ словами, а не точка;
  * название найденного места едет в ответ: диспетчер видит, куда встала точка.

Дороги до такой точки спрашиваются отдельно, у OSRM (geo/roads.py). Не ответил —
плечи достраиваются по прямой с коэффициентом извилистости (geo/travel.py).
Поэтому пометка о расстояниях в ответе API ставится после сборки задачи
(api/routes.py, _roads_note), а ESTIMATE_NOTE ниже — только исход по умолчанию.
"""
from __future__ import annotations
import functools
import http.client
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request

from dataclasses import dataclass

from ..config import DATA
from .cluster import classify_cluster
from .polite import Throttle
from .travel import haversine_km

NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Только латиница. http.client кодирует заголовки в latin-1, и кириллица здесь
# уронила бы КАЖДЫЙ запрос мимо кэша ещё до выхода в сеть. Проверяется тестом
# без сети.
USER_AGENT = "hive-dispatch/1.0 (+https://github.com/cvetkov-es/Hive; LCT-2026 task 3)"
TIMEOUT_S = 5.0
LIMIT = 5                 # вариантов от геокодера
MAX_KM = 25.0             # дальше этого от стартовой точки зоны — чужое место
SESSION_MAX = 1000        # адресов в памяти сеанса; старые забываются
POLITE = Throttle(1.0)    # правило Nominatim: не чаще запроса в секунду

ESTIMATE_NOTE = ("адреса не было в офлайновом кэше: расстояния до этой точки "
                 "считаются по прямой с замеренным коэффициентом извилистости 1.45")


class GeocodeError(RuntimeError):
    """Координат нет и взять их неоткуда. Текст исключения идёт диспетчеру
    на экран, поэтому пишется для человека, а не для журнала."""


class GeocodeUnavailable(GeocodeError):
    """Сеть или сам геокодер не ответили. Выход — координаты руками."""


class GeocodeRequestError(GeocodeError):
    """Запрос не удалось даже собрать. Это ошибка системы, а не сети, и
    называть её «сеть недоступна» — отправить человека чинить не то."""


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float
    source: str                 # «кэш» или «геокодер»
    note: str = ""
    name: str = ""              # что это за место по мнению источника
    km: float | None = None     # до ближайшей стартовой точки зоны
    anchor: str = ""            # какой именно


@dataclass(frozen=True)
class Candidate:
    """Один вариант ответа геокодера."""
    lat: float
    lon: float
    name: str = ""


@dataclass(frozen=True)
class Area:
    """Где точка вправе оказаться: стартовые точки зоны, к которой адрес
    относится по тексту, — офис региона или «дом» удалённого города."""
    region: str
    anchors: tuple              # ((название, широта, долгота), ...)

    def nearest(self, lat: float, lon: float) -> tuple:
        """-> (км, название) до ближайшей стартовой точки."""
        return min(((haversine_km(lat, lon, a_lat, a_lon), name)
                    for name, a_lat, a_lon in self.anchors), key=lambda x: x[0])

    def viewbox(self, km: float = MAX_KM) -> str:
        """Прямоугольник Nominatim «долгота,широта,долгота,широта»."""
        lats = [a[1] for a in self.anchors]
        lons = [a[2] for a in self.anchors]
        dlat = km / 111.0
        dlon = km / (111.32 * math.cos(math.radians(sum(lats) / len(lats))))
        return (f"{min(lons) - dlon:.5f},{max(lats) + dlat:.5f},"
                f"{max(lons) + dlon:.5f},{min(lats) - dlat:.5f}")


def area_for(region: str, depots, address: str) -> Area | None:
    """Стартовые точки той зоны, куда адрес попадает по тексту. Если такой
    зоны в регионе нет — офис: адрес Каширы в регионе без Каширы должен
    получить отказ «далеко от офиса», а не точку за сто километров."""
    depots = list(depots)
    if not depots:
        return None
    zone = classify_cluster(address)
    pick = ([d for d in depots if d.cluster == zone]
            or [d for d in depots if d.is_office] or depots)
    return Area(region=region, anchors=tuple((d.name, d.lat, d.lon) for d in pick))


def _key(address: str) -> str:
    """Ключ поиска. Регистр, лишние пробелы и точки с запятыми различать
    незачем: один и тот же адрес приходит из выгрузки и из формы по-разному."""
    s = address.replace("ё", "е").casefold()
    s = re.sub(r"[.,]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _read(name: str) -> dict:
    path = DATA / "geo" / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


@functools.lru_cache(maxsize=1)
def cache() -> dict:
    """Офлайновый геокэш: нормализованный адрес -> (широта, долгота, место).

    В кэш кладутся оба написания — исходное из выгрузки и нормализованный
    запрос, который по нему уходил в геокодер. Диспетчер может ввести любое.

    Ручные правки (data/geo/overrides.json) важнее кэша — ровно как при чтении
    выгрузки (io/csv_loader). Иначе исправленный вручную адрес, набранный
    диспетчером, снова встал бы в старую ошибочную точку."""
    raw, overrides = _read("geocode.json"), _read("overrides.json")
    out: dict = {}
    for addr, d in raw.items():
        if d.get("lat") is None or d.get("lon") is None:
            continue
        point = (float(d["lat"]), float(d["lon"]), str(d.get("name") or ""))
        out.setdefault(_key(addr), point)
        if d.get("query"):
            out.setdefault(_key(str(d["query"])), point)
    for addr, v in overrides.items():
        if addr.startswith("_") or v.get("lat") is None or v.get("lon") is None:
            continue
        # Название из кэша к исправленной точке не относится: оно описывает
        # как раз ту ошибку, которую правка исправила.
        name = v.get("name") or "координаты заданы вручную"
        point = (float(v["lat"]), float(v["lon"]), str(name))
        out[_key(addr)] = point
        query = (raw.get(addr) or {}).get("query")
        if query:
            out[_key(str(query))] = point
    return out


_session: dict = {}      # (адрес, регион) -> Candidate, спрошенные за этот сеанс


def resolve(address: str, cache_only: bool = False, area: Area | None = None) -> Point:
    key = _key(address or "")
    if not key:
        raise GeocodeError("адрес пустой: точку на карте ставить не из чего")

    hit = cache().get(key)
    if hit:
        return _point(hit[0], hit[1], "кэш", "", hit[2], area)

    skey = (key, area.region if area else "")
    hit = _session.get(skey)
    if hit:                       # уже спрашивали в этом сеансе — не повторяем
        return _point(hit.lat, hit.lon, "геокодер", ESTIMATE_NOTE, hit.name, area)

    if cache_only:
        raise GeocodeError(
            f"адреса «{address}» нет в офлайновом геокэше, а обращение к "
            f"геокодеру выключено: укажите координаты явно")

    try:
        found = ask_geocoder(address, area=area)
    except GeocodeRequestError as exc:
        raise GeocodeError(
            f"адреса «{address}» нет в кэше, а запрос к геокодеру не удалось "
            f"собрать ({exc}) — это ошибка системы, а не сети: укажите "
            f"координаты явно") from exc
    except GeocodeError as exc:
        raise GeocodeError(
            f"адреса «{address}» нет в кэше, а геокодер недоступен ({exc}): "
            f"укажите координаты явно — сеть для работы системы не обязательна"
        ) from exc

    if not found:
        where = (f" в пределах {MAX_KM:.0f} км от «{area.anchors[0][0]}»"
                 if area else "")
        raise GeocodeError(
            f"геокодер не знает адрес «{address}»{where}: проверьте написание "
            f"или укажите координаты")

    best = choose(address, found, area)
    _session[skey] = best
    while len(_session) > SESSION_MAX:
        _session.pop(next(iter(_session)))
    return _point(best.lat, best.lon, "геокодер", ESTIMATE_NOTE, best.name, area)


def _point(lat, lon, source, note, name, area) -> Point:
    km, anchor = area.nearest(lat, lon) if area else (None, "")
    return Point(lat, lon, source, note, name,
                 None if km is None else round(km, 1), anchor)


# --- выбор из вариантов ------------------------------------------------------

# Слова адреса, которые ничего не говорят о месте: тип улицы, «дом», «корпус».
_NOISE = {"г", "гор", "город", "ул", "улица", "д", "дом", "к", "корп", "корпус",
          "с", "стр", "строение", "кв", "квартира", "пр", "просп", "проспект",
          "пер", "переулок", "бул", "бульвар", "ш", "шоссе", "пл", "площадь",
          "наб", "набережная", "проезд", "туп", "тупик", "обл", "область",
          "р", "н", "район", "мкр", "микрорайон", "пос", "поселок", "пгт",
          "россия", "рф"}


def _words(text: str) -> list:
    return re.findall(r"[a-zа-я]+", text.replace("ё", "е").casefold())


def _place_words(address: str) -> set:
    return {w for w in _words(address) if len(w) >= 3 and w not in _NOISE}


def _overlap(words: set, name: str) -> int:
    """Сколько слов адреса нашлось в названии варианта. Сравнение по основе:
    «Каширы» и «Кашира», «Таганского» и «Таганский» — одно и то же место."""
    have = _words(name)
    return sum(1 for w in words
               if any(h.startswith(w[:max(4, len(w) - 2)]) for h in have))


def choose(address: str, candidates, area: Area | None) -> Candidate:
    """Лучший вариант геокодера или отказ словами.

    Сначала отсекается всё дальше MAX_KM от стартовой точки зоны. Из
    оставшихся берётся вариант, в названии которого больше слов из адреса:
    «Таганский район» в адресе побеждает Щербинку. При равенстве — ближайший
    к стартовой точке с точностью до километра: заявка региона скорее лежит в
    регионе. Дальше — порядок самого геокодера: дом и магазин в этом доме
    различаются метрами, и выбирать между ними по метрам значит показать
    диспетчеру вывеску вместо адреса.
    """
    words = _place_words(address)
    scored = []
    for i, c in enumerate(candidates):
        km, anchor = area.nearest(c.lat, c.lon) if area else (0.0, "")
        scored.append((c, km, anchor, _overlap(words, c.name), i))
    near = [x for x in scored if area is None or x[1] <= MAX_KM]
    if not near:
        c, km, anchor, _, _ = min(scored, key=lambda x: x[1])
        raise GeocodeError(
            f"геокодер нашёл «{c.name or 'точку без названия'}» в {km:.0f} км от "
            f"«{anchor}» — это дальше {MAX_KM:.0f} км, за пределами зоны "
            f"обслуживания: проверьте адрес, допишите город и район или укажите "
            f"координаты явно")
    return max(near, key=lambda x: (x[3], -round(x[1]), -x[4]))[0]


# --- сеть ------------------------------------------------------------------

def build_request(address: str, area: Area | None = None) -> urllib.request.Request:
    """Запрос к Nominatim. Собирается отдельно от отправки, чтобы его можно
    было проверить без сети."""
    params = dict(q=address, format="json", limit=LIMIT, countrycodes="ru")
    if area is not None:
        params.update(viewbox=area.viewbox(), bounded=1)
    return urllib.request.Request(
        NOMINATIM + "?" + urllib.parse.urlencode(params),
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ru"})


def ask_geocoder(address: str, timeout: float = TIMEOUT_S,
                 area: Area | None = None) -> list:
    """Один запрос к Nominatim -> до пяти вариантов (Candidate); пустой
    список — адрес не найден.

    Вынесен отдельной функцией намеренно: это единственное место рантайма,
    которое ходит в сеть, и в тестах оно подменяется целиком.
    """
    req = build_request(address, area)
    POLITE.wait()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except ValueError as exc:
        # Сюда попадает UnicodeEncodeError от заголовков: это ошибка сборки
        # запроса, и называть её «сеть недоступна» при живой сети нельзя.
        # Ветка стоит первой: UnicodeError — подкласс ValueError.
        raise GeocodeRequestError(f"{type(exc).__name__}: запрос не кодируется "
                                  f"для HTTP") from exc
    except urllib.error.HTTPError as exc:
        raise GeocodeUnavailable(f"геокодер ответил HTTP {exc.code}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise GeocodeUnavailable(f"сеть недоступна: {type(exc).__name__}") from exc
    try:
        return [Candidate(float(d["lat"]), float(d["lon"]),
                          str(d.get("display_name") or ""))
                for d in json.loads(body.decode("utf-8"))]
    except (ValueError, KeyError, TypeError) as exc:
        raise GeocodeUnavailable(f"геокодер прислал непонятный ответ: "
                                 f"{type(exc).__name__}") from exc
