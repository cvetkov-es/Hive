# -*- coding: utf-8 -*-
"""Правила «тип заявки -> навык, длительность, оборудование, приоритет».

Источники:
  * Нормативы.xlsx (официальная таблица от постановщика);
  * разъяснения от 19.09: BK = Beekeeper, HD = HelpDesk — ДВЕ РАЗНЫЕ СИСТЕМЫ,
    классификаторы одной заявки, соответствие между ними команда определяет сама;
  * приоритет распределения: Авария -> Подключение -> Ремонт/Дозаказ;
  * авария считается по нормативу 100 мин, а НЕ «весь день».

ГЛАВНОЕ ДОПУЩЕНИЕ. В базовый норматив уже включены 20 минут дороги
(колонка «Дорога до клиента/ТКД»). Мы их вычитаем: service = норматив - 20,
дорога берётся отдельно из дорожной матрицы. Иначе дорога считается дважды.
"""
from ..config import (NORM_ROAD_MIN, APPROACH_MIN,
                      PRIO_EMERGENCY, PRIO_CONNECT, PRIO_ROUTINE)

# Базовые нормативы из Нормативы.xlsx, минуты
NORMATIVE = {
    "Подключение клиентов Базовая":        90,
    "Аварий на ТКД":                      100,
    "Дозаказ оборудования":                40,
    "Локальная заявка/ремонт у клиента":   50,
}


def _service(norm_total: int) -> int:
    """Норматив минус заложенная в него дорога, плюс подход и парковка."""
    return norm_total - NORM_ROAD_MIN + APPROACH_MIN


# Итоговые длительности визита (минуты на объекте, без дороги)
SRV_CONNECT   = _service(NORMATIVE["Подключение клиентов Базовая"])       # 80
SRV_EMERGENCY = _service(NORMATIVE["Аварий на ТКД"])                      # 90
SRV_EXTRA     = _service(NORMATIVE["Дозаказ оборудования"])               # 30
SRV_LOCAL     = _service(NORMATIVE["Локальная заявка/ремонт у клиента"])  # 40

# Подстроки HD, по которым определяется оборудование
_HD_ROUTER = ("роутер",)
_HD_TVBOX = ("приставк",)
_EXTRA_ITEMS = ("router", "tvbox", "speaker")

# Работы, для которых нужен автомобиль. Требование выведено из ХАРАКТЕРА РАБОТ,
# а не из географии: линейные работы требуют лестницы, барабана с кабелем и
# инструмента, которые не унести на себе и не увезти в метро. Аварию, кроме того,
# нужно закрывать как можно быстрее. ТЗ: «если в заявке указан требуемый тип
# транспорта, назначенный инженер должен иметь этот тип транспорта».
_HD_NEEDS_CAR = ("работа с кабелем", "разрывы", "рост ошибок на порту")


def _stable_pick(key: str, n: int) -> int:
    """Детерминированный выбор по номеру заявки: одинаков при каждом запуске."""
    return sum(ord(c) for c in str(key)) % n


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def classify(bk: str, hd: str, gigabit: bool = False, job_id: str = "") -> dict:
    """-> {skill, service_min, equipment, priority, work_kind, rule}

    Порядок правил важен: авария определяется полем HD, а не BK.
    «Глобальная проблема» != авария: в Востоке из 8 глобальных проблем
    только 3 имеют HD='Авария', остальные — 'Информация'/'Мониторинг'.
    """
    b, h = _norm(bk), _norm(hd)
    eq: dict = {}

    # 1. Аварийные работы — по HD, независимо от BK
    if "авари" in h:
        return dict(skill="emergency", service_min=SRV_EMERGENCY, equipment=eq,
                    priority=PRIO_EMERGENCY, work_kind="Авария", requires_transport="car",
                    rule="HD содержит «Авария» -> аварийные работы, норматив 100 мин, "
                         "нужен автомобиль (инструмент и скорость прибытия)")

    # 2. Глобальная проблема без аварии — выезд на осмотр/мониторинг сети
    if "глобальн" in b:
        return dict(skill="local", service_min=SRV_LOCAL, equipment=eq,
                    priority=PRIO_ROUTINE, work_kind="Обследование сети",
                    rule="BK «Глобальная проблема», HD не авария -> локальные работы")

    # 3. Подключения и дозаказы
    if "подключение" in b:
        if "дозаказ" in h:
            eq = {"router": 1}
            return dict(skill="connect", service_min=SRV_EXTRA, equipment=eq,
                        priority=PRIO_CONNECT, work_kind="Заказ подключения/дозаказ",
                        rule="BK «Подключение» + HD «Дозаказ» -> норматив дозаказа 40 мин")
        # Оборудование — НЕ обязательный атрибут подключения. Постановщик: «Оборудование
        # нужно, оборудование не нужно». Архитектор: клиент выбирает установку роутера
        # «дополнительной опцией». Поэтому железо требуется не каждому подключению, а
        # доле заявок; доля наша, выбор детерминирован по номеру заявки.
        eq = {"router": 1} if _stable_pick(job_id, 5) < 2 else {}
        return dict(skill="connect", service_min=SRV_CONNECT, equipment=eq,
                    priority=PRIO_CONNECT,
                    work_kind="Подключение" + (" (гигабит)" if gigabit else ""),
                    rule="BK «Подключение» -> норматив подключения 90 мин"
                         + (", клиент заказал роутер" if eq else ", своё оборудование"))

    if "дозаказ" in b:
        if any(k in h for k in _HD_TVBOX):
            eq = {"tvbox": 1}
        elif any(k in h for k in _HD_ROUTER):
            eq = {"router": 1}
        else:
            # Состав дозаказа в выгрузке не детализирован. Архитектор постановщика
            # описал его как «один роутер, две ТВ-приставки, три Алисы» — то есть
            # произвольный набор абонентского оборудования. Раскладываем
            # детерминированно по номеру заявки, чтобы все три позиции
            # встречались в данных и ограничение по оборудованию было живым.
            eq = {_EXTRA_ITEMS[_stable_pick(job_id, len(_EXTRA_ITEMS))]: 1}
        return dict(skill="connect", service_min=SRV_EXTRA, equipment=eq,
                    priority=PRIO_ROUTINE, work_kind="Дозаказ оборудования",
                    rule="BK «Дозаказ» -> норматив 40 мин, нужна позиция оборудования")

    # 4. Локальные заявки и ремонты
    if any(k in h for k in _HD_TVBOX):
        eq = {"tvbox": 1}
    elif any(k in h for k in _HD_ROUTER):
        eq = {"router": 1}
    if any(k in h for k in _HD_NEEDS_CAR):
        return dict(skill="local", service_min=SRV_LOCAL, equipment=eq,
                    priority=PRIO_ROUTINE, work_kind="Линейные работы", requires_transport="car",
                    rule="HD «%s» -> линейные работы, нужен автомобиль "
                         "(лестница, барабан с кабелем, инструмент)" % hd)
    return dict(skill="local", service_min=SRV_LOCAL, equipment=eq,
                priority=PRIO_ROUTINE, work_kind="Локальная заявка / ремонт",
                rule="BK «Локальная заявка» -> норматив ремонта 50 мин")


def table_for_readme() -> str:
    rows = [
        ("любой", "содержит «Авария»", "emergency", SRV_EMERGENCY, "—", "1"),
        ("Глобальная проблема", "Информация / Мониторинг", "local", SRV_LOCAL, "—", "3"),
        ("Подключение", "содержит «Дозаказ»", "connect", SRV_EXTRA, "роутер", "2"),
        ("Подключение", "прочее", "connect", SRV_CONNECT, "роутер", "2"),
        ("Дозаказ", "любой", "connect", SRV_EXTRA, "роутер / приставка", "3"),
        ("Локальная заявка", "замена роутера", "local", SRV_LOCAL, "роутер", "3"),
        ("Локальная заявка", "замена приставки", "local", SRV_LOCAL, "приставка", "3"),
        ("Локальная заявка", "прочее", "local", SRV_LOCAL, "—", "3"),
    ]
    out = ["| Тип BK | Тип HD | Навык | Мин на объекте | Оборудование | Приоритет |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)
