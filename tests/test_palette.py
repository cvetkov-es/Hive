# -*- coding: utf-8 -*-
"""Цвета бригад: измерение, а не вкус.

Решение «восемь цветов повторяются с четырьмя формами метки» обосновано
расчётом, и расчёт обязан быть воспроизводим — иначе на вопрос «почему не
пятнадцать цветов» останется ответ «нам так показалось».

Числа проверены против независимой реализации того же метода (валидатор
палитр из набора dataviz, Node): для трёх слотов по всем парам ΔE 9.2 при
дальтонизме и 24.0 при обычном зрении; для четырёх — 13.7 при обычном зрении,
ниже жёсткого пола; для восьми по СОСЕДНИМ парам — 9.1 и 19.6. Совпадение
трёх независимо задокументированных чисел и означает, что реализация верна.
"""
import pathlib
import re
import sys

TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import pytest  # noqa: E402

from check_palette import (SERIES, check, delta_e,  # noqa: E402
                           lightness_chroma, simulate)

WEB_COLORS = (pathlib.Path(__file__).resolve().parents[1]
              / "web" / "src" / "colors.ts")


def test_three_slots_survive_a_map():
    """На карте видны ВСЕ пары сразу, и по всем парам проходят три цвета."""
    res = check(SERIES[:3])
    assert res["ok"], res["lines"]
    assert res["worst_cvd"] == 9.2
    assert res["worst_normal"] == 24.0


def test_the_fourth_slot_breaks_normal_vision():
    """Четвёртый цвет ставит жёлтый рядом с оранжевым, и пара проваливает
    жёсткий пол обычного зрения. Это и есть причина формы метки: дальше
    добавлять оттенки бессмысленно, сколько их ни подбирай."""
    res = check(SERIES[:4])
    assert not res["ok"]
    assert res["worst_normal"] == 13.7
    assert res["worst_normal"] < 15.0


def test_all_eight_are_hopeless_on_a_map():
    res = check(SERIES)
    assert not res["ok"]
    assert res["worst_cvd"] < 6.0, "восемь цветов на карте неразличимы"


def test_eight_slots_are_fine_on_a_bar_chart():
    """Тот же набор по СОСЕДНИМ парам проходит: на графике со столбиками рядом
    оказываются только соседние ряды. Разница в постановке, а не в палитре."""
    res = check(SERIES, pairs="adjacent")
    assert res["ok"], res["lines"]
    assert res["worst_cvd"] == 9.1
    assert res["worst_normal"] == 19.6


def test_palette_matches_the_interface():
    """Список цветов продублирован в web/src/colors.ts, чтобы интерфейс не
    зависел от Python, а проверка — от Node. Расхождение обесценивает обе
    стороны, поэтому оно проверяется."""
    text = WEB_COLORS.read_text(encoding="utf-8")
    block = text.split("export const SERIES")[1].split("] as const")[0]
    found = re.findall(r"#[0-9a-fA-F]{6}", block)
    assert found == SERIES, f"в интерфейсе {found}, в проверке {SERIES}"


def test_shapes_cover_the_whole_fleet():
    """Меток должно хватать на весь парк самого большого региона (15 бригад).

    Проверяется ТОЛЬКО это, а не число форм и их имена: добавить форму и есть
    то средство, которое эта проверка советует вместо девятого цвета. Тест не
    должен запрещать собственную рекомендацию.
    """
    text = WEB_COLORS.read_text(encoding="utf-8")
    block = text.split("const SHAPES")[1].split("];")[0]
    shapes = re.findall(r"'([a-z]+)'", block)
    assert len(shapes) >= 2, "одной формы мало: цвета повторяются с девятой бригады"
    assert len(shapes) == len(set(shapes)), f"формы повторяются: {shapes}"
    assert len(SERIES) * len(shapes) >= 15, (
        f"{len(SERIES)} цветов x {len(shapes)} форм = "
        f"{len(SERIES) * len(shapes)} меток, а бригад до 15")


def test_colour_blind_simulation_matches_known_values():
    """Симуляция сверяется с ТОЧНЫМИ значениями, а не с неравенствами.

    Неравенства «симулированные цвета различаются» и «расстояние при
    дальтонизме меньше обычного» выполняются и у неправильной модели: если
    применить матрицы к гамма-кодированному sRGB вместо линейного, цвет
    получится другой, но всё ещё не исходный и всё ещё ближе к соседу — и тест
    проходил бы, ничего не проверив. Подмена simulate это подтверждает:
    неравенства остаются зелёными.

    Эталонные тройки — линейное RGB после матриц Мачадо при полной тяжести.
    Ошибка в матрицах, в порядке строк или в пространстве, к которому они
    применены, сдвигает их сразу.
    """
    got = simulate("#e34948", "deutan")
    want = (0.324727, 0.263026, 0.056568)
    assert all(abs(a - b) < 1e-6 for a, b in zip(got, want)), got

    got = simulate("#008300", "protan")
    want = (0.238900, 0.178459, 0.000000)
    assert all(abs(a - b) < 1e-6 for a, b in zip(got, want)), got

    for kind in ("protan", "deutan", "tritan"):
        for colour in SERIES:
            out = simulate(colour, kind)
            assert 0.0 <= min(out) <= max(out) <= 1.0, (colour, kind, out)


def test_colour_blindness_actually_costs_distance():
    """Смысловая проверка поверх эталонной: красное и зелёное при дейтеранопии
    обязаны сблизиться, иначе модель ничего не моделирует."""
    red, green = "#e34948", "#008300"
    assert delta_e(red, green) > delta_e(red, green, "deutan") + 5


def test_every_colour_sits_in_the_light_band():
    for c in SERIES:
        L, chroma = lightness_chroma(c)
        assert 0.43 <= L <= 0.77, f"{c}: светлота {L:.3f}"
        assert chroma >= 0.10, f"{c}: насыщенность {chroma:.3f}"


def test_pair_mode_typo_is_refused():
    """Опечатка в режиме пар — ошибка, а не молчаливый уход в мягкую ветку
    соседних пар, где восемь цветов получают вердикт «проходит» — ровно тот
    ответ, который эта проверка существует чтобы опровергнуть."""
    with pytest.raises(ValueError, match="режим пар"):
        check(SERIES, pairs="All")


def test_degenerate_palette_does_not_pass_vacuously():
    """Меньше двух цветов — не «проходит», а нечего сравнивать. Сторожевые
    значения расстояний некому сбить, и отчёт рапортовал бы об успехе,
    ничего не измерив."""
    for palette in ([], SERIES[:1]):
        res = check(palette)
        assert not res["ok"]
        assert res["worst_cvd"] is None
