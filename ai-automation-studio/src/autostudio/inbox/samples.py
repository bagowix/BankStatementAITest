"""Маленькие PDF-счета для демо и тестов: PDF собирается в коде, а не хранится в репозитории.

PDF написан вручную по ISO 32000 (одна страница, шрифт Helvetica, текстовый слой) — без
сторонних генераторов и без бинарных файлов в git: спецификация счёта лежит текстом в
`examples/inbox/invoices/*.toml`, а байты PDF получаются при запуске демо или теста.
"""

import tomllib
from collections.abc import Sequence
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from typing import Final

import msgspec

from autostudio.errors import ConfigError

_FONT_SIZE: Final = 11
_LEADING: Final = 15
_TOP: Final = 800
_LEFT: Final = 50


def _escape(line: str) -> bytes:
    text = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    # WinAnsiEncoding шрифта ≈ cp1252: символ вне неё становится «?», а не ломает PDF.
    return text.encode('cp1252', errors='replace')


def minimal_pdf(lines: Sequence[str]) -> bytes:
    """Одностраничный PDF с текстовым слоем: по строке текста на строку списка.

    Args:
        lines: Строки страницы сверху вниз; пустой список — PDF без текста (как скан).

    Returns:
        Байты PDF.
    """
    content = b'BT\n/F1 %d Tf\n%d TL\n%d %d Td\n' % (_FONT_SIZE, _LEADING, _LEFT, _TOP)
    content += b''.join(b'(' + _escape(line) + b') Tj T*\n' for line in lines)
    content += b'ET'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
        b'/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
        b'<< /Length %d >>\nstream\n' % len(content) + content + b'\nendstream',
    ]
    out = bytearray(b'%PDF-1.4\n')
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    out += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n' % (len(objects) + 1, xref)
    out += b'%%EOF\n'
    return bytes(out)


class InvoiceSample(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Письмо со счётом из `examples/inbox/invoices/*.toml`.

    Attributes:
        message_id: Message-ID письма.
        date: Заголовок Date.
        sender: Заголовок From.
        to: Заголовок To.
        subject: Тема.
        body: Текст письма.
        filename: Имя PDF-вложения.
        pdf: Строки страницы PDF.
    """

    message_id: str
    date: str
    sender: str = msgspec.field(name='from')
    to: str
    subject: str
    body: str
    filename: str
    pdf: list[str]


def invoice_email(sample: InvoiceSample) -> bytes:
    """Письмо RFC 5322 с PDF-счётом во вложении.

    Args:
        sample: Спецификация письма и страницы счёта.

    Returns:
        Байты письма, как их отдал бы IMAP-сервер.
    """
    message = EmailMessage(policy=SMTP)
    message['Message-ID'] = sample.message_id
    message['Date'] = sample.date
    message['From'] = sample.sender
    message['To'] = sample.to
    message['Subject'] = sample.subject
    message.set_content(sample.body)
    message.add_attachment(minimal_pdf(sample.pdf), maintype='application', subtype='pdf', filename=sample.filename)
    return message.as_bytes()


def load_invoice_samples(directory: Path) -> list[bytes]:
    """Письма со счетами из каталога спецификаций (по алфавиту имён файлов).

    Args:
        directory: Каталог с `*.toml`.

    Returns:
        Байты писем.

    Raises:
        ConfigError: Спецификация битая.
    """
    emails: list[bytes] = []
    for path in sorted(directory.glob('*.toml')):
        try:
            sample = msgspec.convert(tomllib.loads(path.read_text(encoding='utf-8')), type=InvoiceSample)
        except (tomllib.TOMLDecodeError, msgspec.ValidationError) as exc:
            raise ConfigError(f'{path}: {exc}') from None
        emails.append(invoice_email(sample))
    return emails
