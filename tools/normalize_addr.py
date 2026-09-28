# -*- coding: utf-8 -*-
"""Нормализация адресов из выгрузки BK -> форма, понятная Nominatim."""
import re

TYPES = [
    (('пр-кт', 'пр-т', 'проспект', 'прт'), 'проспект'),
    (('пр-зд', 'проезд', 'прзд'), 'проезд'),
    (('наб', 'набережная'), 'набережная'),
    (('б-р', 'бул', 'бульвар'), 'бульвар'),
    (('пер', 'переулок'), 'переулок'),
    (('ш', 'шоссе'), 'шоссе'),
    (('пл', 'площадь'), 'площадь'),
    (('туп', 'тупик'), 'тупик'),
    (('аллея',), 'аллея'),
    (('линия', 'лн'), 'линия'),
    (('ул', 'улица'), 'улица'),
]
ABBR2CANON = {a: c for keys, c in TYPES for a in keys}
CITIES = ('москва', 'домодедово', 'кашира', 'ступино', 'востряково', 'видное', 'подольск')
DROP = {'г', 'город', 'мо', 'обл', 'московская', 'область', 'пгт', 'д', 'дом', 'кв'}

def _clean(a):
    s = a.strip().replace(' ', ' ')
    s = re.sub(r',?\s*кв[\.\s]\s*[0-9]+\s*$', '', s)       # квартира
    s = s.replace('.', ' ').replace(',', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    return s

_HOUSE_RE = re.compile(
    r'^([0-9]+(?:/[0-9]+)?)'
    r'((?![КкСс][0-9])[А-Яа-я])?'                 # литера, но не к1/с1
    r'(?:\s*[Кк](?:орп(?:ус)?)?\s*([0-9]+[А-Яа-я]?))?'
    r'(?:\s*[Сс](?:тр(?:оение)?)?\s*([0-9]+[А-Яа-я]?))?$'
)

def parse_addr(a):
    """-> (city, street, stype, house, warn)"""
    toks = _clean(a).split()
    warn = []
    city = None
    stype = None
    house_parts = []
    street = []

    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        tl = t.lower()
        if tl in DROP:
            # 'д' = маркер дома: всё после него до конца — номер
            if tl in ('д', 'дом'):
                house_parts = toks[i+1:]
                i = n
                continue
            i += 1
            continue
        if city is None and tl in CITIES:
            city = 'Домодедово' if tl == 'востряково' else t.capitalize()
            i += 1
            continue
        if tl.rstrip('-') in ABBR2CANON and stype is None:
            stype = ABBR2CANON[tl.rstrip('-')]
            i += 1
            continue
        street.append(t)
        i += 1

    # если маркера 'д' не было — дом = хвостовые токены, похожие на номер
    if not house_parts:
        while street and re.match(r'^[0-9]', street[-1]):
            house_parts.insert(0, street.pop())

    house = ' '.join(house_parts)
    m = _HOUSE_RE.match(house.replace(' ', '')) or _HOUSE_RE.match(re.sub(r'\s+', '', house))
    if m:
        h = m.group(1) + (m.group(2) or '')
        if m.group(3): h += 'к' + m.group(3)
        if m.group(4): h += 'с' + m.group(4)
        house = h
    elif house:
        warn.append('house_unparsed:' + house)
        house = re.sub(r'\s+', '', house)

    if city is None:
        city = 'Москва'; warn.append('city_defaulted')
    if not street:
        warn.append('no_street')
    return city, ' '.join(street), stype or 'улица', house, warn

def variants(a):
    """Список запросов к геокодеру от точного к грубому."""
    city, street, stype, house, warn = parse_addr(a)
    v = []
    if street and house:
        v.append(f"{city}, {street} {stype}, {house}")
        v.append(f"{city}, {stype} {street}, {house}")
        v.append(f"{city}, {street}, {house}")
    if street:
        v.append(f"{city}, {street} {stype}")
        v.append(f"{city}, {street}")
    v.append(city)
    out = []
    for x in v:
        if x not in out: out.append(x)
    return out, warn

def norm_addr(a):
    return variants(a)[0][0]

if __name__ == "__main__":
    tests = [
     "Город Москва, пр-кт.Волгоградский, д. 128 к 5",
     "Город Москва, пр-кт.Волгоградский, д. 128к1",
     "г.Город Москва, наб.Семеновская, д. 3/1к2",
     "г. Москва, ул Юных Ленинцев, д 83с 4",
     "г. Москва, ул Бирюлёвская, д 1с1",
     "г.Москва проезд Симферопольский, д.7",
     "МО, г. Кашира Кржижановского ул. д. 5/1",
     "МО, г. Ступино Андропова ул. д. 33",
     "Москва Булатниковский пр-зд. д. 6к1",
     "Москва Бирюлевская ул. д. 44",
     "обл.Московская область, г.Домодедово, пгт.Востряково-1, ул.Жуковского, д. 14/18",
     "Домодедово, проезд.Советский 1-й, д. 1А",
     "Город Москва, б-р.Самаркандский Квартал 137а, д. к5",
     "Москва, проезд Орехово-Зуевский, д. 18/8",
     "Город Москва, ул.3-я Институтская, д. 5 к 2",
     "Город Москва, ш.Каширское, д. 136",
     "Город Москва, ул.Грайвороновская, д. 10 к 2",
    ]
    for t in tests:
        v, w = variants(t)
        print(t, "\n   ->", v[0], ("  WARN:"+",".join(w) if w else ""))
