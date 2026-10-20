"""Какие адреса монитор вообще имеет право открыть (защита от SSRF).

Список URL приходит от клиента, а редиректы — от чужих сайтов. Без проверки сервер
оператора можно заставить постучаться в собственную сеть: в метаданные облака
(169.254.169.254), в Redis на localhost, в админку роутера. Поэтому каждый адрес — и из
конфига, и после каждого редиректа — проходит одни и те же правила.
"""

import ipaddress
import re
from typing import Final

import httpx

_SCHEMES: Final = frozenset({'http', 'https'})
_PORTS: Final = frozenset({None, 80, 443, 8080, 8443})
_LOCAL_NAMES: Final = frozenset({'localhost', 'localhost.localdomain', 'ip6-localhost', 'ip6-loopback'})
_LOCAL_SUFFIXES: Final = ('.localhost', '.local', '.internal', '.lan', '.home.arpa', '.intranet', '.corp')
# Имя хоста: буквы любого алфавита, цифры, дефисы и точки. Всё прочее (`%20`, `_`-хаки) — отказ.
_HOSTNAME: Final = re.compile(r'^[^\W_](?:[\w-]*[^\W_])?(?:\.[^\W_](?:[\w-]*[^\W_])?)*$')
# Последняя метка из цифр или в hex (`127.1`, `0x7f.0.0.1`) — числовая запись IP, которую
# системный резолвер (inet_aton) превратит в адрес. Настоящих доменов верхнего уровня таких нет.
_NUMERIC_LABEL: Final = re.compile(r'^(?:\d+|0x[0-9a-f]+)$')

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def ip_is_public(address: IPAddress) -> bool:
    """Адрес из публичного интернета (не частная сеть, не loopback, не link-local, не мультикаст).

    Args:
        address: IPv4 или IPv6; IPv4, завёрнутый в IPv6 (`::ffff:127.0.0.1`), разворачивается.

    Returns:
        True, если по адресу можно ходить.
    """
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def host_problem(host: str) -> str | None:
    """Проверяет имя хоста или IP-литерал без обращения к DNS.

    Args:
        host: Хост из URL (IPv6 — без квадратных скобок).

    Returns:
        Причина отказа или None, если хост допустим.
    """
    name = host.strip().rstrip('.').lower()
    if not name:
        return 'в адресе нет хоста'
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        address = None
    if address is not None:
        return None if ip_is_public(address) else f'{name} — не публичный адрес'
    if not _HOSTNAME.match(name):
        return f'{name!r} — недопустимое имя хоста'
    if _NUMERIC_LABEL.match(name.rsplit('.', 1)[-1]):
        return f'{name} — числовая запись IP-адреса'
    if name in _LOCAL_NAMES or name.endswith(_LOCAL_SUFFIXES):
        return f'{name} — локальное имя'
    if '.' not in name:
        # Одно слово без точки — имя из внутренней сети («router», «2130706433»).
        return f'{name} — имя без домена'
    return None


def url_problem(url: str) -> str | None:
    """Проверяет URL по правилам монитора: http(s), без логина в адресе, публичный хост.

    Args:
        url: Адрес страницы.

    Returns:
        Причина отказа или None, если адрес допустим.
    """
    try:
        parsed = httpx.URL(url)
        host = parsed.host  # IDNA-имя декодируется лениво — и падает именно здесь
    except (httpx.InvalidURL, ValueError):  # ValueError — это и IDNAError на «xn--»-мусоре
        return 'не URL'
    if parsed.scheme not in _SCHEMES:
        return f'схема {parsed.scheme or "(нет)"} не разрешена, только http/https'
    if parsed.userinfo:
        return 'логин и пароль в URL запрещены'
    if parsed.port not in _PORTS:
        return f'порт {parsed.port} не разрешён'
    return host_problem(host)


def origin(url: str) -> str:
    """`scheme://host[:port]` — ключ кэша robots.txt и паузы между запросами."""
    parsed = httpx.URL(url)
    port = f':{parsed.port}' if parsed.port is not None else ''
    return f'{parsed.scheme}://{parsed.host}{port}'


def redact(url: str) -> str:
    """Адрес без query и fragment — для логов и текстов ошибок (в query бывают токены)."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError):
        return '(битый URL)'
    return str(parsed.copy_with(userinfo=b'', query=None, fragment=None))
