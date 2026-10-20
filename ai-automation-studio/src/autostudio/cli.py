"""Командная строка оператора: `autostudio <пакет> ...`, отчёты, новые клиенты."""

import asyncio
import datetime as dt
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Annotated, Final, Literal

import cyclopts

from autostudio import __version__, logsafe
from autostudio.clientconf import events_path, load_client_info
from autostudio.errors import AutoStudioError, ConfigError
from autostudio.events import EventLog
from autostudio.registry import PACKAGES, demo_for, highlights_for, metrics_for
from autostudio.report import build_report, month_bounds, render_html, render_markdown
from autostudio.settings import Settings

app = cyclopts.App(
    name='autostudio', help='Пакеты ИИ-автоматизации: запуск, демо, отчёты клиентам.', version=__version__
)

for _info in PACKAGES.values():
    app.command(_info.cli, name=_info.name, help=_info.title)


def _parse_month(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(f'{value}-01')
    except ValueError:
        raise ConfigError(f'--month={value!r}: ожидается ГГГГ-ММ, например 2026-10') from None


@app.command
def report(
    config: Path,
    *,
    month: str,
    fmt: Annotated[Literal['md', 'html'], cyclopts.Parameter(name='--format')] = 'md',
    out: Path | None = None,
) -> None:
    """Месячный отчёт клиенту из журнала событий.

    Args:
        config: Конфиг клиента (любого пакета — нужна секция [client]).
        month: Отчётный месяц, ГГГГ-ММ.
        fmt: md — для письма/Telegram, html — для печати в PDF.
        out: Куда записать; по умолчанию stdout.
    """
    settings = Settings.from_env(os.environ)
    client = load_client_info(config)
    log = EventLog(events_path(settings.data_dir, client))
    start, end = month_bounds(_parse_month(month), client.zone)
    events = log.between(start, end)
    specs = [spec for info in PACKAGES.values() for spec in metrics_for(info)]
    highlights: list[str] = []
    for info in PACKAGES.values():
        hook = highlights_for(info)
        if hook is not None:
            highlights += hook([e for e in events if e.package == info.name], client.language)
    built = build_report(
        client=client.name,
        month=start.date(),
        language=client.language,
        events=events,
        specs=specs,
        highlights=highlights,
    )
    text = render_markdown(built) if fmt == 'md' else render_html(built)
    if out is None:
        sys.stdout.write(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding='utf-8')
        print(f'отчёт записан: {out}')


@app.command(name='new-client')
def new_client(
    client_id: str,
    *,
    package: Literal['booking', 'prices', 'inbox'],
    root: Path = Path('clients'),
    examples: Path = Path('examples'),
) -> None:
    """Заводит каталог клиента из образца: конфиг пакета и .env-шаблон.

    Args:
        client_id: Идентификатор клиента (a-z, 0-9, дефис).
        package: Какой пакет подключаем.
        root: Где лежат каталоги клиентов.
        examples: Где лежат образцы конфигов.
    """
    target = root / client_id
    config = target / f'{package}.toml'
    if config.exists():
        raise ConfigError(f'{config} уже существует — правьте его, а не перезаписывайте')
    source = examples / package / 'client.toml'
    if not source.exists():
        raise ConfigError(f'нет образца {source}')
    target.mkdir(parents=True, exist_ok=True)
    text = source.read_text(encoding='utf-8')
    # id клиента входит не только в `[client].id`, но и в пути данных (`/data/demo-inbox/out`).
    config.write_text(text.replace(f'demo-{package}', client_id), encoding='utf-8')
    env_example = examples / package / '.env.example'
    if env_example.exists() and not (target / '.env').exists():
        shutil.copyfile(env_example, target / '.env')
    print(
        f'готово: {config}\n'
        f'дальше: замените в {config} демо-данные (адреса, домены, e-mail), заполните {target / ".env"}\n'
        f'и проверьте: autostudio {package} check {config}'
    )


@app.command(name='demo-all')
def demo_all(*, out: Path = Path('var/demo')) -> None:
    """Прогоняет офлайн-демо всех пакетов: без ключей, без сети, на заглушке модели.

    Args:
        out: Каталог для выгрузок демо (CSV, черновики, отчёты).
    """
    failed: list[str] = []
    for info in PACKAGES.values():
        print(f'\n=== {info.title} ===')
        try:
            print(asyncio.run(demo_for(info)(out / info.name)))
        except AutoStudioError as exc:
            failed.append(f'{info.name}: {exc}')
    if failed:
        raise AutoStudioError('демо упали: ' + '; '.join(failed))


# httpx и httpcore на уровне INFO пишут каждый запрос с полным URL, а в URL Bot API
# Telegram лежит токен бота (`/bot<token>/sendMessage`), в ссылках сайтов — метки
# и токены в query. Выше WARNING они ничего такого не пишут.
_LOG_FORMAT: Final = '%(asctime)s %(levelname)s %(name)s: %(message)s'
_NOISY_HTTP_LOGGERS: Final = ('httpx', 'httpcore', 'hpack', 'aiogram.event')


def quiet_http_loggers() -> None:
    """Поднимает уровень HTTP-логгеров до WARNING, чтобы URL с секретами не попадали в логи."""
    for name in _NOISY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def main() -> None:
    """Точка входа `autostudio`: ошибки пакета печатаются коротко, без трассировки."""
    logging.basicConfig(level=os.environ.get('AUTOSTUDIO_LOG_LEVEL', 'INFO'), format=_LOG_FORMAT)
    logsafe.install(_LOG_FORMAT)
    quiet_http_loggers()
    try:
        app()
    except AutoStudioError as exc:
        print(f'ошибка: {exc}', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
