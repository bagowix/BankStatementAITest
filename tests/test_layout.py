from bankstatement.layout import group_lines, merge_split_numbers
from tests.conftest import text_line, word


def test__group_lines__tiny_glyph_on_the_row__row_stays_whole() -> None:
    """Ловушка Renasant: выносная точка высотой в пиксель рвала строку пополам."""
    words = [
        word('1693', 50, 367),
        word('01-02', 150, 367),
        word('.', 190, 367, height=1.3),
        word('363.80', 260, 367),
    ]
    lines = group_lines(words)

    assert len(lines) == 1
    assert [w.text for w in lines[0].money] == ['363.80']


def test__group_lines__separate_rows__not_merged() -> None:
    words = [*text_line('04/01 CHECK 100.00', 100.0), *text_line('04/02 CHECK 200.00', 130.0)]

    assert len(group_lines(words)) == 2


def test__group_lines__no_words__empty() -> None:
    assert group_lines([]) == []


def test__merge_split_numbers__lost_thousands_comma__glued() -> None:
    """Binder: '$848 578.92' — потерянная разрядная запятая, а не два числа."""
    merged = merge_split_numbers([word('$848', 100, 20), word('578.92', 124, 20)])

    assert [w.text for w in merged] == ['$848,578.92']


def test__merge_split_numbers__distant_columns__left_apart() -> None:
    """Дневная сводка: дата и остаток стоят рядом по виду, но далеко по месту."""
    merged = merge_split_numbers([word('01', 100, 20), word('607,330.75', 300, 20)])

    assert len(merged) == 2


def test__merge_split_numbers__head_with_comma__glued_without_doubling() -> None:
    """Число, заканчивающееся на запятую, склеивается с хвостом через маленький зазор.

    На реальной выписке потерянная разрядная запятая оставляет зазор ~0-1 пиксель.
    На синтетике эмулируем очень маленький gap (gap < половины медианного зазора).
    """
    from bankstatement.recognize.base import Word

    # Используем gap=0, чтобы эмулировать потерянную запятую
    head = Word(
        text='$1,085,',
        x0=100,
        y0=20,
        x1=142,  # ширина = 42 пиксель
        y1=30,
        page=0,
        confidence=95.0,
        engine='test',
    )
    tail_zero_gap = Word(
        text='703.81',
        x0=142,
        y0=20,
        x1=178,  # x0 = x1 предыдущего (gap=0)
        y1=30,
        page=0,
        confidence=95.0,
        engine='test',
    )
    merged = merge_split_numbers([head, tail_zero_gap])
    assert [w.text for w in merged] == ['$1,085,703.81']


def test__merge_split_numbers__synthetic_line__no_merge_with_normal_gap() -> None:
    """На синтетической строке обычный пробел (gap=CHAR_WIDTH) не должен вызвать склейку.

    CHECK 101 250.00 — это три отдельных токена, не число, разорванное на запятой.
    Зазор между 101 и 250.00 равен обычному межтокенному (6.0 пиксель), а не потерянной запятой.
    """
    words = text_line('CHECK 101 250.00', 100.0)
    merged = merge_split_numbers(words)

    # CHECK, 101, 250.00 — три отдельных токена
    assert len(merged) == 3
    assert [w.text for w in merged] == ['CHECK', '101', '250.00']
