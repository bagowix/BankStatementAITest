"""Разбор письма и сборка черновика ответа — чистые функции, без сети и ввода-вывода.

Письмо приходит от постороннего человека и часто сломано: заголовки в RFC 2047, тело
в windows-1251 без указания кодировки, HTML без text/plain, нет Message-ID, дата
«yesterday». Разбор не падает на таком письме, а честно восстанавливает, что может.
Из письма ничего не исполняется и не загружается: ни ссылки, ни картинки, ни вложения.
"""

import codecs
import datetime as dt
import email
import email.policy
import hashlib
import re
from collections.abc import Iterable, Sequence
from email.errors import HeaderParseError
from email.header import Header, decode_header
from email.headerregistry import Address
from email.message import EmailMessage, Message
from email.utils import format_datetime, getaddresses, parsedate_to_datetime
from typing import Final
from zoneinfo import ZoneInfo

import msgspec
from selectolax.lexbor import LexborHTMLParser, LexborNode

from autostudio.inbox.errors import MailParseError

GENERATED_ID_DOMAIN: Final = 'autostudio.generated'
_SIGNATURE_LIMIT: Final = 600
_ATTACHMENT_LIMIT: Final = 20
_QUOTE_LIMIT: Final = 3000
_REFERENCES_KEPT: Final = 10

# Квантификаторы ограничены: текст письма чужой, и регулярка не должна уходить в
# квадратичный перебор на длинной строке без «@».
_ADDRESS: Final = re.compile(r'[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,8}', re.UNICODE)
_MSGID: Final = re.compile(r'<[^<>\s]+>')
_WS: Final = re.compile(r'\s+')
_HIGH_RUN: Final = re.compile(rb'[\x80-\xff]{3,}')
# Самые частые строчные русские буквы: в настоящем тексте их много, а при чтении
# не той однобайтовой кодировкой вместо них получаются заглавные и псевдографика.
_RU_COMMON: Final = frozenset('оеаинтсрвлкмдпуяызьбгчйхжшюцщэф')

_AUTO_SUBJECT: Final = re.compile(
    r'^\s*(out of office|automatic reply|auto[- ]?reply|autoreply|auto:|'
    r'автоответ|автоматический ответ|нет на месте|не в офисе)',
    re.IGNORECASE,
)
_BOUNCE_SENDERS: Final = frozenset({'mailer-daemon', 'postmaster'})
_BULK_PRECEDENCE: Final = frozenset({'bulk', 'list', 'junk'})

# Строка, с которой начинается цитата предыдущего письма. Пересылку («Forwarded
# message») не режем: пересланное письмо и есть содержание.
_ON_WROTE: Final = re.compile(
    r'^\s*(on\s.{3,250}\swrote:|.{3,250}\s(пишет|написал|написала|написал\(а\)|писал|писала|писал\(а\)):)\s*$',
    re.I,
)
# Yandex и Mail.ru пишут над цитатой дату и адрес без глагола: «12.10.2026, 10:00, Имя <a@b>:».
_DATE_ADDR_COLON: Final = re.compile(r'^.{0,200}\d.{0,200}<[^<>\s]+@[^<>\s]+>\s*:\s*$')
_ORIGINAL_MARK: Final = re.compile(r'^\s*-{2,}\s*(original message|исходное сообщение)\s*-{2,}\s*$', re.I)
_FORWARD_MARK: Final = re.compile(
    r'^\s*(-{2,}\s*(forwarded message|пересылаемое сообщение|пересланное сообщение)\s*-{2,}|begin forwarded message:)',
    re.I,
)
_OUTLOOK_FROM: Final = re.compile(r'^\s*\*?(from|от|von):\*?\s', re.I)
# Заголовок цитаты Outlook — «From:» и следом «Sent:». Одно «From:/Subject:» бывает и в
# письмах форм сайта («From: John / Subject: Booking»), их резать нельзя; «Date:»
# засчитываем, только если в ней год и время.
_OUTLOOK_NEXT: Final = re.compile(
    r'^\s*\*?((sent|отправлено|gesendet|envoyé)\s*:|(date|дата)\s*:.*\d{4}.*\d{1,2}:\d{2})', re.I
)
_MOBILE_FOOTER: Final = re.compile(
    r'^\s*(sent from my\b|sent from mail for\b|get outlook for\b|sent with\b|'
    r'отправлено (с|из)\b|отправлено из мобильной)',
    re.I,
)
_SIGNATURE_MARK: Final = re.compile(r'^--\s?$')
_HEADER_LINE_LIMIT: Final = 300
_SUBJECT_LIMIT: Final = 500
_NAME_LIMIT: Final = 200

_DROP_TAGS: Final = 'script, style, head, title, noscript, template, svg, iframe, object, blockquote, [hidden]'
_BREAK: Final = '\u2029'
_INLINE_WS: Final = re.compile(r'[^\S\u2029]+')
_HIDDEN_STYLE: Final = re.compile(r'display\s*:\s*none|visibility\s*:\s*hidden', re.I)
_BLOCK_TAGS: Final = frozenset(
    {
        'p', 'div', 'li', 'ul', 'ol', 'tr', 'table', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article',
        'header', 'footer', 'pre', 'hr', 'address', 'dd', 'dt', 'dl', 'form', 'fieldset', 'center', 'main',
        'nav', 'aside', 'figure', 'figcaption', 'br',
    }
)  # fmt: skip


class ParsedEmail(msgspec.Struct, frozen=True, kw_only=True):
    """Письмо в том виде, в каком его видят классификатор и таблица.

    Attributes:
        message_id: Message-ID в угловых скобках; если его нет — стабильный хеш письма.
        message_id_generated: Message-ID сгенерирован (в In-Reply-To его ставить нельзя).
        from_name: Имя отправителя (декодированное).
        from_addr: Адрес отправителя в нижнем регистре.
        reply_to: Reply-To, если он отличается от From (формы сайтов шлют от noreply).
        to: Адреса получателей.
        subject: Тема, RFC 2047 раскодирован, без переводов строк.
        date: Дата письма с часовым поясом (если заголовок битый — момент разбора).
        date_fallback: Дата взята из момента разбора, а не из заголовка.
        text: Видимый текст без цитат и подписи, обрезанный до лимита.
        signature: Подпись отправителя (там часто телефон) — отдельно от текста.
        truncated: Текст обрезан по лимиту.
        attachments: Имена вложений — только имена, содержимое не читается.
        is_auto_reply: Автоответ, «нет на месте» или уведомление о недоставке.
        is_bulk: Рассылка (List-Unsubscribe, List-Id, Precedence: bulk/list/junk).
        in_reply_to: Message-ID письма, на которое это отвечает.
        references: Цепочка Message-ID переписки.
    """

    message_id: str
    message_id_generated: bool = False
    from_name: str = ''
    from_addr: str = ''
    reply_to: str | None = None
    to: list[str] = msgspec.field(default_factory=list)
    subject: str = ''
    date: dt.datetime
    date_fallback: bool = False
    text: str = ''
    signature: str = ''
    truncated: bool = False
    attachments: list[str] = msgspec.field(default_factory=list)
    is_auto_reply: bool = False
    is_bulk: bool = False
    in_reply_to: str | None = None
    references: list[str] = msgspec.field(default_factory=list)

    @property
    def reply_address(self) -> str:
        """Куда отвечать: Reply-To, иначе From."""
        return self.reply_to or self.from_addr

    @property
    def sender_domain(self) -> str:
        return self.from_addr.rpartition('@')[2]


def parse_email(raw: bytes, *, now: dt.datetime, max_body_chars: int = 8000) -> ParsedEmail:
    """Разбирает письмо из байтов IMAP.

    Args:
        raw: Письмо целиком (RFC 5322), как его отдал сервер.
        now: Момент разбора (с часовым поясом) — запасная дата для битого заголовка.
        max_body_chars: Сколько символов текста оставить.

    Returns:
        Разобранное письмо.

    Raises:
        MailParseError: Байты не содержат ни одного заголовка — это не письмо.
    """
    if now.tzinfo is None:
        raise ValueError('now должен быть с часовым поясом')
    msg = email.message_from_bytes(raw)
    if not raw.strip() or not msg.keys():
        raise MailParseError('в сообщении нет заголовков — это не письмо')

    from_name, from_addr = _first_address(msg.get_all('From') or [])
    _, reply_addr = _first_address(msg.get_all('Reply-To') or [])
    subject = _one_line(decode_header_value(msg.get('Subject')))[:_SUBJECT_LIMIT]
    date, date_fallback = _parse_date(msg.get('Date'), now)
    message_id, generated = _message_id(msg, raw)

    body_text = _body_text(msg)
    text, signature = split_reply(body_text)
    truncated = len(text) > max_body_chars
    if truncated:
        hidden = len(text) - max_body_chars
        text = text[:max_body_chars].rstrip() + f'\n[… truncated: {hidden} more characters]'

    return ParsedEmail(
        message_id=message_id,
        message_id_generated=generated,
        from_name=from_name[:_NAME_LIMIT],
        from_addr=from_addr,
        reply_to=reply_addr if reply_addr and reply_addr != from_addr else None,
        to=[addr for _, addr in _addresses(msg.get_all('To') or []) if addr],
        subject=subject,
        date=date,
        date_fallback=date_fallback,
        text=text,
        signature=signature[:_SIGNATURE_LIMIT].rstrip(),
        truncated=truncated,
        attachments=_attachment_names(msg),
        is_auto_reply=_is_auto_reply(msg, from_addr, subject),
        is_bulk=_is_bulk(msg),
        in_reply_to=next(iter(_MSGID.findall(_header(msg, 'In-Reply-To'))), None),
        references=_MSGID.findall(_header(msg, 'References'))[-_REFERENCES_KEPT:],
    )


# --- кодировки -----------------------------------------------------------------


def decode_bytes(data: bytes, declared: str | None) -> str:
    """Декодирует байты текста с учётом объявленной кодировки и её частых ошибок.

    Порядок: объявленная кодировка, затем UTF-8, затем догадка между windows-1251,
    koi8-r и cp1252 по самому тексту. Последний шаг — с заменой нечитаемых байтов:
    лучше «�» в письме, чем потерянное письмо.

    Args:
        data: Байты.
        declared: Кодировка из заголовка или None.

    Returns:
        Текст.
    """
    candidates = [name for name in (_codec(declared), 'utf-8') if name is not None]
    for name in dict.fromkeys(candidates):
        try:
            return data.decode(name)
        except UnicodeDecodeError:
            continue
    return data.decode(_guess_8bit(data), errors='replace')


def _codec(name: str | None) -> str | None:
    if not name:
        return None
    try:
        return codecs.lookup(name.strip().strip('"').lower()).name
    except LookupError:
        return None


def _guess_8bit(data: bytes) -> str:
    runs = b' '.join(_HIGH_RUN.findall(data))
    if not runs:
        # Одиночные 8-битные байты среди латиницы — это западноевропейский текст.
        return 'cp1252'
    scores = {
        name: sum(ch in _RU_COMMON for ch in runs.decode(name, errors='replace')) for name in ('cp1251', 'koi8_r')
    }
    return max(scores, key=lambda name: scores[name])


def decode_header_value(value: str | Header | None) -> str:
    """Раскодирует заголовок: RFC 2047, сырые 8-битные байты, битые кодировки.

    Args:
        value: Значение заголовка из `email.message.Message`.

    Returns:
        Текст заголовка.
    """
    if value is None:
        return ''
    try:
        fragments = decode_header(value)
    except (HeaderParseError, ValueError, LookupError):
        return _unescape(str(value))
    parts: list[str] = []
    for fragment, charset in fragments:
        parts.append(_unescape(fragment) if isinstance(fragment, str) else decode_bytes(fragment, charset))
    return ''.join(parts)


def _unescape(text: str) -> str:
    # Сырые 8-битные байты в заголовке compat32 прячет в суррогаты — достаём и декодируем.
    if any('\udc80' <= ch <= '\udcff' for ch in text):
        return decode_bytes(text.encode('utf-8', errors='surrogateescape'), None)
    return text


def _one_line(text: str) -> str:
    # Перевод строки в теме — это внедрение заголовка в черновик ответа.
    return _WS.sub(' ', text).strip()


def _header(msg: Message, name: str) -> str:
    return _one_line(decode_header_value(msg.get(name)))


# --- адреса, дата, Message-ID -------------------------------------------------------


def _addresses(values: Sequence[str | Header]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for value in values:
        raw = _one_line(str(value) if isinstance(value, str) else decode_header_value(value))
        pairs = [(name, addr) for name, addr in getaddresses([raw]) if '@' in addr]
        if not pairs:
            # Строгий разбор Python отказывается от странных заголовков; адрес всё равно берём.
            pairs = [('', match) for match in _ADDRESS.findall(raw)]
        found += [(_one_line(decode_header_value(name)), addr.strip().lower()) for name, addr in pairs]
    return found


def _first_address(values: Sequence[str | Header]) -> tuple[str, str]:
    pairs = _addresses(values)
    return pairs[0] if pairs else ('', '')


def _parse_date(value: str | None, now: dt.datetime) -> tuple[dt.datetime, bool]:
    if not value:
        return now.astimezone(dt.UTC), True
    try:
        parsed = parsedate_to_datetime(_one_line(str(value)))
    except (ValueError, TypeError, IndexError, OverflowError):
        return now.astimezone(dt.UTC), True
    if parsed.tzinfo is None:
        # «-0000» по RFC 5322 значит «пояс неизвестен»: честнее всего считать UTC.
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC), False


def _message_id(msg: Message, raw: bytes) -> tuple[str, bool]:
    header = _header(msg, 'Message-ID')
    found = _MSGID.findall(header)
    if found:
        return found[0], False
    if header and ' ' not in header:
        return f'<{header.strip("<>")}>', False
    # Без Message-ID дедупликация держится на хеше заголовков и тела: тот же
    # набор байтов при повторной выборке даёт тот же идентификатор.
    head = '\n'.join(str(msg.get(name, '')) for name in ('From', 'To', 'Subject', 'Date'))
    body = raw.split(b'\r\n\r\n', 1)[-1] if b'\r\n\r\n' in raw else raw.split(b'\n\n', 1)[-1]
    digest = hashlib.sha256(head.encode('utf-8', 'surrogateescape') + b'\0' + body).hexdigest()[:40]
    return f'<{digest}@{GENERATED_ID_DOMAIN}>', True


# --- тело ---------------------------------------------------------------------------


def _is_attachment(part: Message) -> bool:
    disposition = (part.get_content_disposition() or '').lower()
    return disposition == 'attachment' or part.get_filename() is not None


def _part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ''
    return decode_bytes(payload, part.get_content_charset())


def _body_text(msg: Message) -> str:
    plain = ''
    html = ''
    for part in msg.walk():
        if part.is_multipart() or _is_attachment(part):
            continue
        kind = part.get_content_type()
        if kind == 'text/plain' and not plain.strip():
            plain = _part_text(part)
        elif kind == 'text/html' and not html.strip():
            html = _part_text(part)
    # Бывает пустой text/plain рядом с настоящим HTML — тогда берём HTML.
    if plain.strip():
        return _normalize_text(plain)
    return html_to_text(html) if html.strip() else ''


def _normalize_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    out: list[str] = []
    for line in lines:
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    return '\n'.join(out).strip()


def _outermost(nodes: list[LexborNode]) -> list[LexborNode]:
    # Удаление узла освобождает и его потомков: вложенное совпадение (script внутри
    # скрытого div) трогать после этого нельзя, поэтому удаляем только внешние.
    marked = {node.mem_id for node in nodes}
    seen: set[int] = set()
    outer: list[LexborNode] = []
    for node in nodes:
        parent = node.parent
        while parent is not None and parent.mem_id not in marked:
            parent = parent.parent
        if parent is None and node.mem_id not in seen:
            seen.add(node.mem_id)
            outer.append(node)
    return outer


def html_to_text(html: str) -> str:
    """Видимый текст HTML-письма: без скриптов, стилей, скрытых блоков и цитат.

    Args:
        html: HTML письма.

    Returns:
        Текст с переводами строк на границах блоков.
    """
    tree = LexborHTMLParser(html)
    hidden = [node for node in tree.css('[style]') if _HIDDEN_STYLE.search(node.attributes.get('style') or '')]
    for node in _outermost([*tree.css(_DROP_TAGS), *hidden]):
        if node.tag in _BLOCK_TAGS or node.tag == 'blockquote':
            # Убранный блок (цитата, скрытый div) всё равно разделял строки текста.
            node.insert_after(_BREAK)
        node.decompose()
    root = tree.body or tree.root
    if root is None:
        return ''
    # Границы блоков помечаются символом-разделителем абзацев: пробелы и переводы
    # строк исходного HTML схлопываются в пробел, а разделитель становится строкой.
    for node in root.css(','.join(sorted(_BLOCK_TAGS))):
        node.insert_before(_BREAK)
        node.insert_after(_BREAK)
    for node in root.css('td, th'):
        node.insert_after(' ')
    flat = _INLINE_WS.sub(' ', root.text(deep=True))
    lines = [line.strip() for line in flat.split(_BREAK)]
    return '\n'.join(line for line in lines if line)


def _is_quote_header(line: str, following: list[str]) -> bool:
    if _ORIGINAL_MARK.match(line):
        return True
    stripped = line.rstrip()
    if stripped.endswith(':') and (_ON_WROTE.match(line) or ('@' in line and _DATE_ADDR_COLON.match(line))):
        return True
    # Gmail переносит длинную строку «On …, Name <\naddr> wrote:» на две.
    if following and stripped.lower().lstrip().startswith('on ') and not stripped.endswith(':'):
        joined = f'{line} {following[0]}'
        if len(joined) <= _HEADER_LINE_LIMIT and _ON_WROTE.match(joined):
            return True
    short = [nxt for nxt in following if len(nxt) <= _HEADER_LINE_LIMIT]
    return bool(_OUTLOOK_FROM.match(line)) and any(_OUTLOOK_NEXT.match(nxt) for nxt in short)


def split_reply(text: str) -> tuple[str, str]:
    """Отрезает цитату предыдущего письма и отделяет подпись.

    Подпись не выбрасывается: в ней часто телефон и компания отправителя, поэтому
    она возвращается отдельно — модель видит её как подпись, а не как текст письма.

    Args:
        text: Текст письма.

    Returns:
        Текст без цитаты и подписи; подпись.
    """
    lines = text.split('\n')
    cut = len(lines)
    for index, line in enumerate(lines):
        if len(line) > _HEADER_LINE_LIMIT:
            continue
        if _FORWARD_MARK.match(line):
            break
        if _is_quote_header(line, lines[index + 1 : index + 5]):
            cut = index
            break
    kept = [line for line in lines[:cut] if not line.lstrip().startswith('>') and not _MOBILE_FOOTER.match(line)]
    while kept and (not kept[-1].strip() or set(kept[-1].strip()) <= {'_', '-'}):
        kept.pop()

    signature: list[str] = []
    for index, line in enumerate(kept):
        if _SIGNATURE_MARK.match(line):
            signature = kept[index + 1 :]
            kept = kept[:index]
            break
    body = _normalize_text('\n'.join(kept))
    if not body:
        # Всё письмо оказалось «цитатой» (ответ снизу) — лучше показать его целиком.
        return _normalize_text('\n'.join(line for line in lines if not _MOBILE_FOOTER.match(line))), ''
    return body, _normalize_text('\n'.join(signature))


def _attachment_names(msg: Message) -> list[str]:
    names: list[str] = []
    for part in msg.walk():
        if part.is_multipart() or not _is_attachment(part):
            continue
        filename = part.get_filename()
        name = _one_line(decode_header_value(filename)) if filename else ''
        if not name:
            name = f'attachment.{part.get_content_subtype()}'
        # Имя вложения — тоже чужой ввод: без путей и управляющих символов.
        name = re.sub(r'[\\/\x00-\x1f]', '_', name).strip()[:100]
        names.append(name)
        if len(names) >= _ATTACHMENT_LIMIT:
            break
    return names


def _is_auto_reply(msg: Message, from_addr: str, subject: str) -> bool:
    auto_submitted = _header(msg, 'Auto-Submitted').lower()
    if auto_submitted and auto_submitted != 'no':
        return True
    if any(msg.get(name) is not None for name in ('X-Autoreply', 'X-Autorespond', 'X-Autoresponder')):
        return True
    if _header(msg, 'Precedence').lower() == 'auto_reply':
        return True
    if msg.get_content_type() == 'multipart/report':
        return True
    if from_addr.partition('@')[0] in _BOUNCE_SENDERS:
        return True
    return bool(_AUTO_SUBJECT.match(subject))


def _is_bulk(msg: Message) -> bool:
    if msg.get('List-Unsubscribe') is not None or msg.get('List-Id') is not None:
        return True
    return _header(msg, 'Precedence').lower() in _BULK_PRECEDENCE


# --- черновик ответа ----------------------------------------------------------------------

_REPLY_PREFIX: Final = re.compile(r'^\s*(re|aw|ответ|отв)\s*:', re.I)
_QUOTE_HEADER: Final[dict[str, str]] = {
    'ru': '{when}, {who} пишет:',
    'en': 'On {when}, {who} wrote:',
}


def reply_subject(subject: str) -> str:
    """Тема ответа: `Re: ` один раз, без «Re: Re: Re:»."""
    clean = _one_line(subject)
    return clean if _REPLY_PREFIX.match(clean) else f'Re: {clean}'.rstrip()


def _address_header(name: str, addr: str) -> Address | str:
    try:
        return Address(display_name=_one_line(name), addr_spec=addr)
    except (ValueError, IndexError):
        return addr


def _quoted(original: ParsedEmail, language: str, zone: ZoneInfo) -> str:
    when = original.date.astimezone(zone).strftime('%Y-%m-%d %H:%M')
    who = f'{original.from_name} <{original.from_addr}>' if original.from_name else original.from_addr
    header = _QUOTE_HEADER.get(language, _QUOTE_HEADER['en']).format(when=when, who=who)
    body = original.text[:_QUOTE_LIMIT]
    return header + '\n' + '\n'.join(f'> {line}'.rstrip() for line in body.split('\n'))


def compose_reply(
    original: ParsedEmail,
    *,
    body: str,
    from_addr: str,
    from_name: str,
    now: dt.datetime,
    language: str,
    zone: ZoneInfo,
) -> bytes:
    """Собирает черновик ответа (RFC 5322) так, чтобы Gmail и Outlook сложили его в ту же цепочку.

    Args:
        original: Письмо, на которое отвечаем.
        body: Текст ответа уже с подписью.
        from_addr: Адрес ящика (поле From).
        from_name: Имя в поле From (название компании).
        now: Момент создания (Date и стабильный Message-ID).
        language: Язык ответа — для строки над цитатой.
        zone: Часовой пояс клиента — для даты в строке над цитатой.

    Returns:
        Байты письма для IMAP APPEND.
    """
    msg = EmailMessage(policy=email.policy.SMTP.clone(cte_type='7bit'))
    msg['From'] = _address_header(from_name, from_addr)
    msg['To'] = _address_header('' if original.reply_to else original.from_name, original.reply_address)
    msg['Subject'] = reply_subject(original.subject)
    msg['Date'] = format_datetime(now.astimezone(dt.UTC))
    domain = from_addr.rpartition('@')[2] or 'localhost'
    seed = f'{original.message_id}|{now.isoformat()}'.encode()
    # Message-ID детерминирован: повтор того же запуска даёт тот же черновик, а не новый.
    msg['Message-ID'] = f'<draft-{hashlib.sha256(seed).hexdigest()[:24]}@{domain}>'
    if not original.message_id_generated:
        msg['In-Reply-To'] = original.message_id
        msg['References'] = ' '.join([*original.references[-(_REFERENCES_KEPT - 1) :], original.message_id])
    msg['X-Autostudio-Draft'] = 'inbox; review before sending'
    text = body.rstrip() + '\n\n' + _quoted(original, language, zone) + '\n'
    msg.set_content(text)
    return msg.as_bytes()


def find_addresses(texts: Iterable[str]) -> list[str]:
    """Все адреса e-mail, буквально встречающиеся в текстах (в порядке появления)."""
    found: list[str] = []
    for text in texts:
        found += [match.lower() for match in _ADDRESS.findall(text)]
    return list(dict.fromkeys(found))
