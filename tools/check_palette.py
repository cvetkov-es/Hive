#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка цветов бригад на различимость. Офлайн, в рантайме НЕ вызывается.

    python tools/check_palette.py            # наша палитра, отчёт и вердикт
    python tools/check_palette.py --slots 4  # что будет, если взять больше цветов

ЗАЧЕМ. На карте у нас до пятнадцати бригад и восемь цветов, повторяющихся с
разной формой метки. Вопрос «почему метки разной формы, а не просто пятнадцать
цветов» задают первым, и отвечать на него «так красивее» нельзя. Ответ здесь
считается, а не утверждается.

МЕТОД. Различимость меряется расстоянием в пространстве OKLab, умноженным на
сто: OKLab устроен так, что одинаковая разница координат означает примерно
одинаковую разницу на глаз, чего не даёт ни RGB, ни HSL. Дальтонизм
моделируется матрицами Мачадо, Оливейры и Фернандеса (2009) при полной тяжести,
применёнными в ЛИНЕЙНОМ RGB — то есть до гамма-коррекции, иначе смешение
считается неверно.

Пороги:
  * ΔE >= 8 при дальтонизме (протанопия и дейтеранопия) — цель;
  * 6..8 — пол, допустимый ТОЛЬКО при втором признаке помимо цвета;
  * ΔE >= 15 при обычном зрении — жёсткий пол: если пара не различается
    полноцветным зрением, никакой второй признак этого не искупает.

ГЛАВНОЕ РАЗЛИЧИЕ — какие пары считать. У графика со столбиками рядом стоят
только соседние ряды, и проверять достаточно соседние пары. На карте видны ВСЕ
пары одновременно, и проверять надо все. Это меняет ответ: по соседним парам
проходят восемь цветов, по всем — три.

Результат этой проверки закреплён тестом tests/test_palette.py: если кто-то
добавит девятый цвет вместо девятой формы, тест упадёт и объяснит почему.
"""
from __future__ import annotations
import argparse
import itertools
import math
import sys

# Палитра бригад — та же, что в web/src/colors.ts. Продублирована сюда
# намеренно: интерфейс не должен зависеть от Python, а проверка — от Node.
# Расхождение между двумя списками ловит тест.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
          "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SURFACE_LIGHT = "#ffffff"      # маркеры лежат на белом кольце, а не на тайлах

CVD_TARGET, CVD_FLOOR = 8.0, 6.0
NORMAL_FLOOR = 15.0
CHROMA_FLOOR = 0.10
BAND_LIGHT = (0.43, 0.77)
CONTRAST_MIN = 3.0

# Мачадо, Оливейра, Фернандес (2009), тяжесть 1.0, линейное RGB
MACHADO = {
    "protan": ((0.152286, 1.052583, -0.204868),
               (0.114503, 0.786281, 0.099216),
               (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968),
               (0.280085, 0.672501, 0.047413),
               (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779),
               (-0.078411, 0.930809, 0.147602),
               (0.004733, 0.691367, 0.303900)),
}


def _srgb(h: str) -> tuple:
    h = h.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear(h: str) -> tuple:
    return tuple(_to_linear(c) for c in _srgb(h))


def oklab(rgb: tuple) -> tuple:
    r, g, b = rgb
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def lightness_chroma(h: str) -> tuple:
    L, a, b = oklab(linear(h))
    return L, math.hypot(a, b)


def simulate(h: str, kind: str) -> tuple:
    r, g, b = linear(h)
    M = MACHADO[kind]
    return tuple(min(1.0, max(0.0, row[0] * r + row[1] * g + row[2] * b))
                 for row in M)


def delta_e(h1: str, h2: str, kind: str | None = None) -> float:
    a = oklab(simulate(h1, kind) if kind else linear(h1))
    b = oklab(simulate(h2, kind) if kind else linear(h2))
    return 100 * math.dist(a, b)


def contrast(h: str, surface: str) -> float:
    def lum(x):
        r, g, b = linear(x)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    hi, lo = sorted((lum(h), lum(surface)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


PAIR_MODES = ("all", "adjacent")


def check(palette: list, surface: str = SURFACE_LIGHT, pairs: str = "all") -> dict:
    """-> отчёт. `pairs`: 'all' — все пары (карта), 'adjacent' — соседние (график).

    Режим пар проверяется, а не угадывается. Опечатка вроде 'All' иначе
    молча увела бы в мягкую ветку соседних пар, и восемь цветов получили бы
    вердикт «проходит» — ровно тот ответ, который эта проверка существует
    чтобы опровергнуть. Тихий мягкий ответ хуже громкой ошибки.
    """
    if pairs not in PAIR_MODES:
        raise ValueError(f"неизвестный режим пар «{pairs}», есть: "
                         f"{', '.join(PAIR_MODES)}")
    idx = range(len(palette))
    pairlist = (list(itertools.combinations(idx, 2)) if pairs == "all"
                else [(i, i + 1) for i in range(len(palette) - 1)])
    lines, ok = [], True

    # Палитра меньше двух цветов не «проходит», а не подлежит проверке: пар нет,
    # сторожевые 99.0 никем не сбиваются, и отчёт бодро рапортовал бы
    # «проходит», ничего не измерив.
    if len(palette) < 2:
        return dict(ok=False, lines=[("Размер палитры", False,
                                      f"цветов {len(palette)}: сравнивать нечего")],
                    warnings=[], worst_cvd=None, worst_normal=None,
                    pairs=pairs, slots=len(palette))

    lo, hi = BAND_LIGHT
    off = [(c, round(lightness_chroma(c)[0], 3)) for c in palette
           if not lo <= lightness_chroma(c)[0] <= hi]
    ok &= not off
    lines.append(("Полоса светлоты", not off,
                  f"вне полосы {lo}–{hi}: {off}" if off
                  else f"все {len(palette)} внутри {lo}–{hi}"))

    low = [(c, round(lightness_chroma(c)[1], 3)) for c in palette
           if lightness_chroma(c)[1] < CHROMA_FLOOR]
    ok &= not low
    lines.append(("Насыщенность", not low,
                  f"бледнее порога: {low}" if low
                  else f"все >= {CHROMA_FLOOR}"))

    worst_cvd, worst_pair, worst_kind = 99.0, None, ""
    for i, j in pairlist:
        # Берётся ХУДШИЙ из двух видов дальтонизма: пара обязана различаться и
        # при протанопии, и при дейтеранопии, а не в среднем по ним.
        d = min(delta_e(palette[i], palette[j], k) for k in ("protan", "deutan"))
        if d < worst_cvd:
            worst_cvd, worst_pair, worst_kind = d, (palette[i], palette[j]), "protan/deutan"
    cvd_ok = worst_cvd >= CVD_FLOOR
    ok &= cvd_ok
    if worst_cvd >= CVD_TARGET:
        note = ""
    elif worst_cvd >= CVD_FLOOR:
        note = f" — ниже цели {CVD_TARGET}, допустимо только при втором признаке"
    else:
        # Ниже жёсткого пола второй признак НЕ спасает: пара неразличима, и
        # форма метки лишь маскирует это.
        note = (f" — ниже жёсткого пола {CVD_FLOOR}: второй признак этого "
                f"не искупает")
    lines.append((f"Различимость при дальтонизме ({pairs})", cvd_ok,
                  f"худшая пара {worst_pair[0]}↔{worst_pair[1]} ΔE {worst_cvd:.1f}"
                  + note if worst_pair else "пар нет"))

    worst_n, worst_np = 99.0, None
    for i, j in pairlist:
        d = delta_e(palette[i], palette[j])
        if d < worst_n:
            worst_n, worst_np = d, (palette[i], palette[j])
    normal_ok = worst_n >= NORMAL_FLOOR
    ok &= normal_ok
    lines.append((f"Различимость обычным зрением ({pairs})", normal_ok,
                  f"худшая пара {worst_np[0]}↔{worst_np[1]} ΔE {worst_n:.1f}"
                  + ("" if normal_ok else f" — ниже жёсткого пола {NORMAL_FLOOR}")
                  if worst_np else "пар нет"))

    # Контраст с фоном НЕ валит проверку: он обязывает к подписи или таблице,
    # а не запрещает цвет. У нас обязательство выполнено — у каждой метки есть
    # белое кольцо и подпись во всплывающей подсказке, а список бригад работает
    # легендой. Поэтому это предупреждение, а не отказ.
    dim = [(c, round(contrast(c, surface), 2)) for c in palette
           if contrast(c, surface) < CONTRAST_MIN]
    warn = [(f"Контраст с фоном {surface}", not dim,
             f"ниже {CONTRAST_MIN}:1: {dim}; обязывает к подписи или легенде"
             if dim else f"все >= {CONTRAST_MIN}:1")]

    return dict(ok=ok, lines=lines, warnings=warn, worst_cvd=round(worst_cvd, 1),
                worst_normal=round(worst_n, 1), pairs=pairs, slots=len(palette))


def plural(n: int, one: str, few: str, many: str) -> str:
    """1 цвет, 2 цвета, 5 цветов."""
    if 11 <= n % 100 <= 14:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def report(res: dict) -> str:
    word = plural(res["slots"], "цвет", "цвета", "цветов")
    out = [f"\nПалитра: {res['slots']} {word}, пары «{res['pairs']}»"]
    for title, good, detail in res["lines"]:
        out.append(f"  [{'ОК  ' if good else 'СБОЙ'}] {title:<44} {detail}")
    for title, good, detail in res["warnings"]:
        out.append(f"  [{'ОК  ' if good else 'ВНИМ'}] {title:<44} {detail}")
    out.append(f"  ИТОГ: {'проходит' if res['ok'] else 'НЕ ПРОХОДИТ'}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slots", type=int, default=0,
                    help="сколько цветов проверить; по умолчанию разбор по нарастанию")
    args = ap.parse_args(argv)

    if args.slots:
        if not 2 <= args.slots <= len(SERIES):
            ap.error(f"--slots должен быть от 2 до {len(SERIES)}, "
                     f"получено {args.slots}")
        res = check(SERIES[:args.slots])
        print(report(res))
        # Код возврата — не украшение: без него проверку нельзя поставить в
        # сборку, она всегда будет зелёной, что бы ни напечатала.
        return 0 if res["ok"] else 1

    print("Сколько цветов выдерживает карта, где видны ВСЕ пары сразу")
    last_ok = 0
    for n in range(2, len(SERIES) + 1):
        res = check(SERIES[:n])
        mark = "проходит" if res["ok"] else "НЕ ПРОХОДИТ"
        word = plural(n, "цвет", "цвета", "цветов")
        print(f"  {n} {word+':':<8} дальтонизм ΔE {res['worst_cvd']:>4}, "
              f"обычное зрение ΔE {res['worst_normal']:>4}  — {mark}")
        if res["ok"]:
            last_ok = n

    print(f"\nПо всем парам выдерживают {last_ok} "
          f"{plural(last_ok, 'цвет', 'цвета', 'цветов')}. Отсюда форма метки: "
          f"восемь цветов\nповторяются с четырьмя формами, а опознание бригады "
          f"держится на выборе\n(выбранная горит, остальные гаснут) и на списке "
          f"бригад как легенде.")

    if last_ok >= 2:
        print(report(check(SERIES[:last_ok])))
    adjacent = check(SERIES, pairs="adjacent")
    print(report(adjacent))
    print("\nПоследний отчёт — для сравнения: на графике со столбиками соседними "
          "бывают\nтолько соседние ряды, и там те же восемь цветов проходят. "
          "Карта — другой случай.")
    # Разбор по нарастанию не «падает»: он показывает, где проходит граница.
    # Проверка считается сломанной, только если не выдерживает даже наш
    # рабочий режим — восемь цветов по соседним парам.
    return 0 if (last_ok >= 3 and adjacent["ok"]) else 1


if __name__ == "__main__":
    sys.exit(main())
