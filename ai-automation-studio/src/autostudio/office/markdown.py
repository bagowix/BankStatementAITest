"""Небольшой Markdown → HTML для договоров: ровно то подмножество, что есть в шаблонах.

Заголовки `#`, абзацы, `---`, цитаты `>`, списки `-` и `1.` с вложенностью по отступу,
таблицы `|`, `**жирный**`, `` `код` `` и ссылки `[текст](url)` (печатаются текстом с
адресом в скобках — в PDF ссылка всё равно не кликается). Сырого HTML нет: весь текст
экранируется, поэтому значения, вписанные в договор, не могут стать разметкой.
Отдельная библиотека ради этого не нужна, а новая зависимость стоила бы дороже.
"""

import html
import re
from collections.abc import Sequence
from typing import Final

import msgspec

_HEADING: Final = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
_HR: Final = re.compile(r'^(-{3,}|\*{3,}|_{3,})$')
_LIST_ITEM: Final = re.compile(r'^(\s*)([-*+]|\d+[.)])\s+(.*)$')
_TABLE_SEPARATOR: Final = re.compile(r'^:?-{2,}:?$')
_BOLD: Final = re.compile(r'\*\*(.+?)\*\*')
_LINK: Final = re.compile(r'\[([^\]]+)\]\(([^)\s]+)\)')
_CODE_SPLIT: Final = re.compile(r'(`[^`]*`)')
_PIPE: Final = re.compile(r'(?<!\\)\|')
_BLOCKQUOTE: Final = re.compile(r'^\s*>\s?')
_CONTINUATION_INDENT: Final = 2


def inline(text: str) -> str:
    """Строчная разметка: код, жирный, ссылки; всё остальное — экранированный текст."""
    out: list[str] = []
    for part in _CODE_SPLIT.split(text):
        if len(part) >= 2 and part.startswith('`') and part.endswith('`'):
            out.append(f'<code>{html.escape(part[1:-1], quote=True)}</code>')
            continue
        escaped = html.escape(part, quote=True)
        escaped = _BOLD.sub(r'<strong>\1</strong>', escaped)
        escaped = _LINK.sub(r'\1 (\2)', escaped)
        out.append(escaped)
    return ''.join(out)


class _Entry(msgspec.Struct, kw_only=True):
    indent: int
    ordered: bool
    number: int
    text: str


def _starts_block(line: str) -> bool:
    stripped = line.strip()
    return bool(
        _HEADING.match(stripped) or _HR.match(stripped) or stripped.startswith(('>', '|')) or _LIST_ITEM.match(line)
    )


def _split_row(row: str) -> list[str]:
    body = row.strip()
    body = body.removeprefix('|')
    if body.endswith('|') and not body.endswith('\\|'):
        body = body[:-1]
    return [cell.strip().replace('\\|', '|') for cell in _PIPE.split(body)]


def _table(rows: Sequence[str]) -> str:
    cells = [_split_row(row) for row in rows]
    header: list[str] | None = None
    aligns: list[str] = []
    body = cells
    if len(cells) >= 2 and all(_TABLE_SEPARATOR.match(cell.replace(' ', '')) for cell in cells[1]):
        header, body = cells[0], cells[2:]
        for cell in cells[1]:
            compact = cell.replace(' ', '')
            aligns.append(' class="r"' if compact.endswith(':') and not compact.startswith(':') else '')

    def align(index: int) -> str:
        return aligns[index] if index < len(aligns) else ''

    head = (
        '<thead><tr>' + ''.join(f'<th{align(i)}>{inline(c)}</th>' for i, c in enumerate(header)) + '</tr></thead>'
        if header is not None
        else ''
    )
    rows_html = ''.join(
        '<tr>' + ''.join(f'<td{align(i)}>{inline(c)}</td>' for i, c in enumerate(row)) + '</tr>' for row in body
    )
    return f'<table>{head}<tbody>{rows_html}</tbody></table>'


def _render_level(entries: Sequence[_Entry], pos: int) -> tuple[str, int]:
    base = entries[pos].indent
    ordered = entries[pos].ordered
    start = entries[pos].number
    items: list[str] = []
    while pos < len(entries) and entries[pos].indent >= base:
        entry = entries[pos]
        if entry.indent > base:
            # Первый элемент уровня всегда с отступом `base`, так что родитель у вложенного списка есть.
            child, pos = _render_level(entries, pos)
            items[-1] += child
            continue
        if entry.ordered != ordered:
            break
        items.append(inline(entry.text))
        pos += 1
    tag = 'ol' if ordered else 'ul'
    attr = f' start="{start}"' if ordered and start != 1 else ''
    return f'<{tag}{attr}>' + ''.join(f'<li>{item}</li>' for item in items) + f'</{tag}>', pos


def _render_list(entries: Sequence[_Entry]) -> str:
    out: list[str] = []
    pos = 0
    while pos < len(entries):
        part, pos = _render_level(entries, pos)
        out.append(part)
    return ''.join(out)


def _collect_list(lines: Sequence[str], i: int) -> tuple[list[_Entry], int]:
    entries: list[_Entry] = []
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            following = next((later for later in lines[i + 1 :] if later.strip()), '')
            indented = len(following) - len(following.lstrip()) >= _CONTINUATION_INDENT
            if following and (_LIST_ITEM.match(following) or indented):
                i += 1
                continue
            break
        match = _LIST_ITEM.match(line)
        if match is not None:
            marker = match.group(2)
            ordered = marker[0].isdigit()
            entries.append(
                _Entry(
                    indent=len(match.group(1).expandtabs(4)),
                    ordered=ordered,
                    number=int(marker[:-1]) if ordered else 1,
                    text=match.group(3).strip(),
                )
            )
        elif entries and not _starts_block(line):
            entries[-1].text += ' ' + line.strip()
        else:
            break
        i += 1
    return entries, i


def to_html(text: str) -> str:
    """Markdown → HTML-фрагмент (без `<html>`): блоки по строкам, строчная разметка внутри.

    Args:
        text: Markdown-текст договора.

    Returns:
        HTML, где весь текст экранирован.
    """
    lines = text.split('\n')
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        heading = _HEADING.match(stripped)
        if heading is not None:
            level = len(heading.group(1))
            out.append(f'<h{level}>{inline(heading.group(2))}</h{level}>')
            i += 1
        elif _HR.match(stripped):
            out.append('<hr>')
            i += 1
        elif stripped.startswith('>'):
            quoted: list[str] = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                quoted.append(_BLOCKQUOTE.sub('', lines[i]))
                i += 1
            out.append(f'<blockquote>{to_html(chr(10).join(quoted))}</blockquote>')
        elif stripped.startswith('|'):
            rows: list[str] = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append(lines[i])
                i += 1
            out.append(_table(rows))
        elif _LIST_ITEM.match(line):
            entries, i = _collect_list(lines, i)
            out.append(_render_list(entries))
        else:
            paragraph = [stripped]
            i += 1
            while i < len(lines) and lines[i].strip() and not _starts_block(lines[i]):
                paragraph.append(lines[i].strip())
                i += 1
            out.append(f'<p>{inline(" ".join(paragraph))}</p>')
    return '\n'.join(out)
