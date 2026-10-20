## Project Overview

`autostudio` — набор «упакованных» услуг ИИ-автоматизации для малого бизнеса. Один фрилансер
(оператор) продаёт их по фиксированной цене плюс абонентская поддержка и разворачивает каждому
клиенту из одного репозитория, меняя только TOML-конфиг клиента и `.env`.

Пакеты:

| Пакет | Что делает для клиента | Канал |
|---|---|---|
| `booking` | ИИ-бот записи: услуги, слоты, напоминания, ответы на вопросы, передача человеку | Telegram (aiogram 3) + консоль |
| `prices` | Мониторинг цен конкурентов → Google Sheets + уведомления об изменениях | HTTP, по расписанию |
| `inbox` | Разбор входящей почты: категория, извлечение полей, черновик ответа, лог в таблицу | IMAP, по расписанию |

Абонентская поддержка держится на трёх вещах из ядра: журнал событий (`events.py`), месячный
отчёт клиенту (`report.py`) и плановый запуск под присмотром (`runlog.py`: пинг healthcheck,
сигнал оператору о сбое).

## Tech Stack

- Python 3.12+, uv + poe, cyclopts 5 (CLI), dishka (DI, Scope.APP), msgspec (схемы и JSON),
  httpx, selectolax (HTML), aiogram 3 (Telegram), gspread (Google Sheets), stdlib sqlite3/imaplib/email
- Модели: OpenAI Responses API или Anthropic Messages API за протоколом `LLMClient`;
  без ключа — `FakeLLM` с эвристиками пакета
- pytest + pytest-asyncio (auto), respx; Ruff (линт и формат, включая правила безопасности `S`) и pyrefly
  в strict-пресете — других линтеров и тайпчекеров в проекте нет

## Useful commands

- `uv sync` — окружение разработки (все extras)
- `uv run poe tests` — тесты с покрытием (ключи провайдеров обнуляются)
- `uv run poe check` — `ruff format --check`, `ruff check`, `pyrefly check` (strict)
- `uv run autostudio demo-all` — офлайн-демо всех пакетов (без сети и ключей)
- `uv run autostudio <package> --help` — команды пакета (`check`, `run`, `demo`, …)
- `uv run autostudio report clients/<id>/<pkg>.toml --month 2026-10 --format html --out report.html`

## Codebase Map

- `src/autostudio/settings.py` — настройки развёртывания из окружения (провайдер/модель/effort, data_dir,
  healthcheck, Telegram оператора)
- `src/autostudio/clientconf.py` — общие секции конфига клиента (`ClientInfo`, `NotifyConfig`, `SinkConfig`),
  `load_client_config`, `require_env`, `client_data_dir`
- `src/autostudio/llm/` — `LLMClient` (extract → msgspec-объект, write → текст), `output_schema`
  (строгая схема без `$ref`), `OpenAIClient`, `AnthropicClient`, `FakeLLM`, `make_llm`
- `src/autostudio/sinks/` — `TableSink` (append/replace вкладки), `CsvSink`, `GoogleSheetsSink`,
  `neutralize_formula` (защита от formula-инъекции), `make_sink`
- `src/autostudio/notify/` — `Notifier`, Telegram/вебхук/консоль/веер, `make_notifier`
- `src/autostudio/events.py` — `EventLog` (SQLite), `Event`
- `src/autostudio/report.py` — `MetricSpec`, `build_report`, Markdown/HTML-отчёт
- `src/autostudio/runlog.py` — `Heartbeat`, `supervised_run`
- `src/autostudio/clock.py` — `Clock`, `system_now`, `FrozenClock`
- `src/autostudio/providers.py` — `CoreProvider` (httpx, часы, heartbeat, `OperatorAlerts`)
- `src/autostudio/registry.py` — каталог пакетов; ядро импортирует пакеты только отсюда, лениво
- `src/autostudio/cli.py` — корневой CLI: подкоманды пакетов, `report`, `new-client`, `demo-all`
- `src/autostudio/<package>/` — пакеты (контракт ниже)
- `examples/<package>/` — образец конфига клиента, `.env.example`, фикстуры демо
- `docs/` — руководства по пакетам, продажи, план действий

## Package Contract

Каждый пакет `src/autostudio/<pkg>/` обязан иметь:

- `config.py` — `<Pkg>Config(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True)` с полями
  `client: ClientInfo`, `notify: NotifyConfig = NotifyConfig()`, `sink: SinkConfig = SinkConfig()` и своей секцией.
- `metrics.py` — `METRICS: list[MetricSpec]` (коды событий `<pkg>.<что>`, подписи `en`/`ru`, честная
  оценка минут на действие) и `highlights(events, language) -> list[str]`.
- `heuristics.py` — эвристики `FakeLLM` для каждой схемы ответа модели: демо и тесты работают без ключа.
- `providers.py` — dishka-провайдер пакета поверх `CoreProvider`.
- `demo.py` — `async def run_demo(out_dir: Path) -> str`: полный офлайн-сценарий на фикстурах из
  `examples/<pkg>/`, без сети и ключей; возвращает человекочитаемую сводку. Его вызывают
  `autostudio <pkg> demo` и `autostudio demo-all` (CI прогоняет его на каждый push).
- `cli.py` — `app = cyclopts.App(name='<pkg>')` с командами минимум `check <config>` (конфиг и окружение
  без побочных эффектов), `run <config>` (боевой запуск) и `demo [--out]`.
- `examples/<pkg>/client.toml` (с `id = "demo-<pkg>"`), `examples/<pkg>/.env.example`, фикстуры.
- `docs/packages/<pkg>.md` — что продаём, как подключить клиента, что клиент должен дать, ограничения.
- Тесты в `tests/<pkg>/`.

## Architecture Conventions

- **Бизнес-логика — чистые функции.** Расписание слотов, разбор цены, классификация письма не знают про
  Telegram, IMAP и HTTP. Адаптер (aiogram, imaplib, httpx) — тонкий слой вокруг них. Это и тестируемость,
  и возможность сменить канал (WhatsApp) без переписывания логики.
- **Модель — не источник истины.** Ответ модели всегда проходит схему (`extract`), а всё, что можно
  посчитать детерминированно (свободен ли слот, какая цена в JSON-LD), считается кодом. Модель
  предлагает, код проверяет.
- **Ввод посторонних — данные, не инструкции.** Письма, реплики покупателей и HTML сайтов передаются
  модели как данные; системный промпт говорит об этом прямо. Никаких действий по тексту письма:
  черновики не отправляются сами, ссылки не открываются.
- **Ничего не строится при импорте.** Контейнер dishka собирает команда CLI или тест.
- **Секреты только в окружении.** В конфиге — имя переменной (`*_env`), в `.env` — значение. Токен
  не попадает в текст исключения и в лог (httpx кладёт URL в исключение — ловим и пишем только тип).
- **Деньги — `Decimal`.** Никаких float в арифметике цен.
- **Время — всегда с часовым поясом.** Внутри UTC, на границах — `ClientInfo.zone` (zoneinfo).
  ruff `DTZ` следит за этим.
- **Каждое действие для клиента — событие в журнале.** Из журнала строится месячный отчёт; чего нет
  в журнале, того нет в отчёте.
- **Персональные данные — минимум.** Храним то, что нужно для работы (имя/телефон записи, извлечённые
  поля письма), а не сырые письма.
- **Logging.** `logger = logging.getLogger(__name__)`, ленивые `%s`-аргументы (ruff `G`).
- **Errors.** Доменные ошибки наследуют `AutoStudioError`; `supervised_run` ловит только их.

## Libraries & Documentation

- **Сверяйте API с документацией, а не с памятью.** Перед использованием сторонней библиотеки —
  Context7 (`mcp__Context7__resolve-library-id` → `mcp__Context7__query-docs`) или исходники в `.venv`.
  Версии здесь свежие (cyclopts 5, openai 3, anthropic 1.x на httpx2, aiogram 3.31): API из
  обучающих данных может быть устаревшим.
- **Проверенная библиотека лучше самописного парсера**, если она жива (релиз за последний год) и
  решает именно нашу задачу. Уже подключены и предпочтительны:
  - `protego` — robots.txt по RFC 9309 (одинаково на всех версиях Python; у `urllib.robotparser`
    поведение зависит от версии и есть ловушка с `modified()`);
  - `price-parser` (Zyte) — разбор цены и валюты из строки; наша обёртка добавляет `Decimal`,
    грузинские обозначения (₾, ლ, GEL) и правила неоднозначности;
  - `phonenumbers` — проверка и нормализация телефонов (E.164, `+995`, местные форматы);
  - `imap-tools` — IMAP: UID, `mark_seen=False`, кодировка папок modified UTF-7 («Черновики»),
    append/copy/move.
  Новая зависимость — только через `uv add` и только с причиной в коммите.
- **Готовые примеры «как сделать X».** Для незнакомой задачи сначала ищите эталонную реализацию
  (официальные examples библиотеки, `build-your-own-x`, `awesome-*` списки, `public-apis` для
  бесплатных API — проверяя их лицензию на коммерческое использование), а не изобретайте протокол.
- **Внешние API и MCP** подключаются, только если они реально нужны клиенту, с проверкой условий
  использования и цены; ключи — в `.env`.

## Rules For Agents

- Новые зависимости не добавлять без крайней необходимости (и тогда — через `uv add`).
- Работая параллельно с другими агентами, запускайте ruff/format только на своих путях:
  `uv run ruff check src/autostudio/<pkg> tests/<pkg>`; `uv run pyrefly check` можно всегда.
- Не трогайте ядро и чужие пакеты; если ядру чего-то не хватает — опишите это в ответе.
- Коммиты — Conventional Commits (`feat(booking): описание`), русский в коммитах и комментариях — норма.
- Комментарии объясняют «почему», а не «что».

## Testing Guidance

- Тест рядом с зеркальным путём: `tests/<pkg>/test_<module>.py`.
- Имена: `test__<что>__<сценарий>__<результат>` (двойное подчёркивание между частями).
- Сеть в тестах — только через respx / `httpx.MockTransport`; модель — только `FakeLLM` или заглушки SDK.
- Покрытие проекта ≥ 90% (`tool.coverage.report.fail_under`).
- Ловушки реального мира — это спецификация: смена летнего времени, запись на прошедшее время,
  двойная запись в один слот, цена «1 299,00 ₾» и «$1,299.99», письмо с prompt-инъекцией,
  HTML-письмо без text/plain, повторный запуск по тем же письмам.

## Style Notes

- Ruff (line-length 120, одинарные кавычки), pyrefly strict без подавлений там, где можно типизировать;
  подавление — только `# pyrefly: ignore[<код>]` с причиной рядом.
- Генераторные контекст-менеджеры аннотируются `Generator[T]`, не `Iterator[T]` (перегрузка
  `contextmanager` с `Iterator` помечена в typeshed устаревшей).
- `X | None`, `list[]`, `dict[]`; без `Any` и `object` в своём коде, без getattr/setattr/hasattr.
- Константы модуля — `Final[...]`; docstrings — Google style (`Args:`/`Returns:`/`Raises:`).
- Без `from __future__ import annotations`.
