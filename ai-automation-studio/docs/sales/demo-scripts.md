# Демо-ролики и живой показ

> **Оператору.** Здесь три ролика по 60 секунд (inbox, booking, prices), один необязательный ролик
> про агента выписок и протокол живого показа на данных клиента на 5 минут. Инструкции — на
> русском. Голос за кадром и подписи — на английском, их можно читать как есть.
>
> Обозначения:
> - `{{NAME}}`, `{{EMAIL}}`, `{{DOMAIN}}`, `{{CALENDAR_LINK}}`, `{{TELEGRAM}}` — твои данные, заполни
>   сам. На Upwork и Fiverr ни один из них в кадр не попадает (раздел 1.7).
> - **П / И / НП** — статусы фактов из `docs/research/DECISION_BRIEF.md`. **НП** — не подтверждено,
>   проверь до того, как на это опираться.
> - 🔄 — функция дописывается или команда не прогнана целиком. Перед записью проверь её.
>
> «AutoStudio» — рабочее имя. Не ставь его в финальную карточку, пока не проверил, что имя и
> домен свободны (`docs/ACTION_PLAN.md`, раздел 2).
>
> Сцены опираются на `docs/sales/upwork.md` (2.6) и `docs/sales/fiverr.md` (4.7, 5.7, 6.7). Там же
> подписи для версии без голоса. Поменял сцену здесь — поправь и там.

## 0. Что проверено 20.10.2026

Команды прогнаны в чистой копии репозитория без ключей и без сети.

| Команда | Результат | Заметка |
|---|---|---|
| `uv run autostudio <pkg> --help` для booking, prices, inbox | ✓ | Команды: booking — `agenda`, `chat`, `check` (`--offline`, `--online`), `demo`, `run`; prices — `check` (`--strict`), `demo`, `probe`, `run`; inbox — `check` (`--connect`), `demo`, `dry-run`, `run` |
| `uv run autostudio booking demo --out var/demo/booking` | ✓ ≈ 5 с | `transcript.md`, `Bookings.csv`, `Schedule.csv`, `report.md` |
| `uv run autostudio booking chat examples/booking/client.toml --language en` | ✓ | Запись кнопками, свободный текст, FAQ, вопрос владельцу; `--admin` → `/today` |
| `uv run autostudio prices demo --out var/demo/prices` | ✓ < 1 с | `Prices_now.csv`, `History.csv`, `Changes.csv`, `report.md` |
| `uv run autostudio inbox demo --out var/demo/inbox` | ✓ < 1 с | `Inbox.csv`, 6 черновиков `.eml` в `drafts/` |
| `uv run autostudio report … --format html` по событиям демо | ✓ | Рецепт в разделах 3–5 |
| `uv run autostudio prices probe <url>` | 🔄 | `--help` ✓. Живой прогон не сделан: в среде проверки нет сети |
| `uv run autostudio inbox dry-run <config>` | 🔄 | `--help` ✓. Нужен настоящий ящик: прогони на своём тестовом |
| booking: Google Calendar, WhatsApp; inbox: вход Workspace через OAuth, поля из PDF-счетов | 🔄 | 20.10.2026 в коде этого нет. Сцены с ними — только после влития и зелёных тестов |

**Демо меняются**, пока пакеты дописываются. За один день 20.10 поменялись подписи уведомлений
inbox, prices начал писать `report.md`, а в образце booking появились `console = false` и
`csv_dir = "out"` (путь от каталога данных клиента). Перед каждой записью прогони команду и сверь вывод со
сценой. Строки, которые цитирует сценарий, могут звучать иначе — снимай то, что реально на экране.

## 1. Подготовка записи (общая для всех роликов)

### 1.1 Отдельная копия и чистый терминал

1. Склонируй репозиторий в отдельную папку, например `~/autostudio-demo`. В ней не должно быть
   клиентов: `ls clients/` показывает только `README.md`. Боевая копия с `clients/` в кадр не
   попадает никогда.
2. Лучше записывать под отдельным пользователем ОС `demo`. Абсолютные пути в выводе показывают имя
   пользователя: например, `inbox check` печатает путь к данным клиента. Демо с относительным
   `--out` печатают относительные пути (проверено).
3. Перед каждой записью открой чистую оболочку без твоих переменных:

   ```bash
   cd ~/autostudio-demo
   env -i HOME="$HOME" PATH="$PATH" TERM="$TERM" LANG=en_US.UTF-8 bash --noprofile --norc
   export PS1='$ ' AUTOSTUDIO_LLM_PROVIDER=fake
   unset HISTFILE
   env | grep -ciE 'key|token|pass|secret'   # вне кадра; печатает только число, ждём 0
   ```

   `AUTOSTUDIO_LLM_PROVIDER=fake` — модель-заглушка: без ключей, без сети, ничего не уходит
   провайдеру. Переменная оболочки важнее файла из `uv run --env-file`: файл не перекроет `fake`
   (проверено на uv 0.11).
4. Прогрей демо вне кадра: `uv run autostudio demo-all --out var/demo`. Первая команда `uv run`
   может ставить зависимости, это не нужно в ролике.

### 1.2 Терминал

- Отдельный профиль «Demo»: моноширинный шрифт 20 pt для чата бота, тёмная или светлая
  контрастная тема, без прозрачности.
- Окно чата — около 110 × 30 символов. Таблицы демо широкие: сводка inbox — около 150 символов,
  служебные строки — до 330. Для них окно 160 колонок, шрифт поменьше, а нужные строки увеличь
  в монтаже. Мелкий шрифт на весь экран на телефоне не читается.
- Перед каждым дублем: `clear`.

### 1.3 OBS

Цифры ниже — рабочие настройки, а не требование площадок.

- **Settings → Video:** Base и Output 1920 × 1080, 30 FPS.
- **Settings → Output → Recording:** формат MKV (запись не пропадёт при сбое), затем
  File → Remux Recordings → MP4. Кодировщик аппаратный (Apple VT H.264 или NVENC) или x264.
- **Audio:** только микрофон. Desktop Audio выключи: туда попадают звуки уведомлений. На микрофон —
  фильтры Noise Suppression и Noise Gate.
- **Источники — Window Capture, не Display Capture.** Так в кадр не попадут уведомления и чужие окна.
- **Сцены:** `Title` (картинка 1920 × 1080 с текстом), `Terminal`, `Browser`, `Phone`, `End card`.
  Адресную строку браузера обрежь фильтром Crop/Pad.
- Горячие клавиши на старт и стоп записи и на смену сцен.
- Снимай каждую сцену отдельным дублем и собирай в редакторе. Один дубль на 60 секунд почти никогда
  не получается.

### 1.4 Браузер, таблицы, телефон

- **Браузер:** отдельный профиль без закладок и расширений. Google-таблицы — с отдельного
  демо-аккаунта Google. Аватар и имя аккаунта в углу, адрес и ID таблицы в адресной строке, имя
  файла — обрезать.
- **CSV в таблицу:** File → Import → Upload, по вкладке на файл. Назови вкладки как в продукте:
  `Inbox`, `Bookings`, `Prices now`, `History`, `Changes`. Телефоны начинаются с апострофа
  (`'+995…`) — это защита от формул, а не ошибка.
- **Черновик `.eml`:** на macOS выдели файл в Finder и нажми пробел (Quick Look). Почтовая
  программа показала бы твои ящики в боковой панели.
- **HTML-отчёт:** открой в браузере. Путь `file://…` в адресной строке показывает имя
  пользователя — обрежь.
- **Телефон (сцены Telegram):** зеркалирование экрана (iPhone Mirroring или QuickTime на macOS,
  scrcpy для Android). Открыт только чат с ботом. Список чатов, твоё имя, номер и @username бота в
  кадр не попадают.

### 1.5 Что не должно попасть в кадр

- [ ] `.env`, ключи, токены, вывод `env`, история оболочки, папка `clients/`.
- [ ] Контакты: твои (на площадках — нигде; в прямой версии — только в финальной карточке),
      @username бота, ссылки `t.me`, QR-коды, URL таблиц, адрес сервисного аккаунта Google.
- [ ] **Телефоны из демо-данных.** Номера в `examples/` выглядят настоящими (`+995 555 12 34 56`,
      `+995 599 11 22 33`, `+995 32 200 00 00`). Они могут принадлежать реальным людям, а фильтры
      площадок ловят номера. Размывай их в монтаже.
- [ ] **Реальные бизнесы и адреса.** Проверь по Google Maps «Demo Barbers», «Demo Tours», «Tech
      Corner Tbilisi», «Hotel Rustaveli». В FAQ демо-барбершопа стоит «12 Rustaveli Ave» — это
      реальная улица. Совпадение — размой или переименуй в локальной копии `examples/` без коммита.
- [ ] **Русские служебные строки в английском ролике:** подсказка консольного чата («Введите
      текст…»), пометка `[→ администраторам]`, сигналы оператору в prices и inbox. Обрежь или
      размой. Грузинскому зрителю русский текст может быть неприятен (бриф, раздел 8, НП).
- [ ] Уведомления, вкладки, чужие окна. Перед записью включи «Не беспокоить».
- [ ] Реальные банковские выписки — никогда (раздел 6).

### 1.6 Честные пометки в кадре

- Первый кадр каждого ролика: **«Demo data, fictional business»**.
- Где показана работа модели, добавь мелкую строку: **«Offline demo: a rule-based stand-in plays
  the AI model.»** Офлайн-демо работают на заглушке, и вывод это пишет («stub model»,
  «FakeLLM»). Fiverr требует не скрывать использование ИИ (НП, бриф 3.2).
- Цифры демо — не результат клиента. Строку «Estimated time saved» из демо-отчёта не выдавай за
  обещание. В голосе — только «an estimate, with the method shown».
- В кадре только то, что есть в коде (раздел 0). Функции с 🔄 не показывать и не называть.
- «Придуманный» телефон в демо inbox подсказан заглушке нарочно. Не называй это ошибкой ИИ.

### 1.7 Финальная карточка: три версии

Ролик один, меняется только последняя карточка и последняя фраза.

| Канал | Текст карточки | Где публиковать |
|---|---|---|
| **Upwork** | `Demo data, fictional business.` / `Message me here on Upwork.` | Видео загружается в Upwork (портфолио, отклик). Никаких имён других площадок, ссылок и контактов (НП, `upwork.md`, раздел 1) |
| **Fiverr** | `Demo data, fictional business.` / `Order or message me here on Fiverr.` | Галерея гига, сообщения. Без ссылок и контактов (НП, `fiverr.md`, раздел 1). Лимит длины и размера видео в галерее проверь в форме гига (НП) |
| **Прямые клиенты** | `{{NAME}}` / `Book a 20-minute call: {{CALENDAR_LINK}}` / `{{DOMAIN}}` | YouTube без публикации в поиске (unlisted), лендинг, личное сообщение |

В версиях для площадок нет цен в лари и нет пилотной скидки: это выглядит как приглашение уйти с
площадки (`offers.md`, раздел 1, п. 7).

### 1.8 Голос, субтитры, экспорт

- **Голос.** Строки ниже — 90–115 слов на ролик плюс финальная фраза, это спокойный темп. Если английский пока не
  рабочий, сделай версию без голоса: те же строки субтитрами (так в `fiverr.md`). Синтезированный
  голос — только с пометкой «AI voice-over» в описании. Голос-клон не используй.
- **Субтитры** вшивай в видео: ролики на площадках часто смотрят без звука. Файл `.srt` — из строк
  голоса.
- **Русская версия** для релокантов — тот же сценарий, строки переводишь сам. **Грузинская** —
  только носитель, и в ней не обещай грузинский интерфейс бота: его нет в коде.
- **Монтаж:** бесплатные DaVinci Resolve или Shotcut — размытие, увеличение, субтитры.
- **Экспорт:** MP4, H.264, 1920 × 1080, 30 FPS, AAC. При 5–6 Мбит/с минута весит около 40–45 МБ.
- **Перед публикацией** посмотри ролик на телефоне и пройди его по кадрам по списку 1.5.

## 2. Порядок записи

1. inbox — первый пакет по брифу (раздел 4.1): выше чек, есть доказательство.
2. booking.
3. prices.
4. Агент выписок — необязательно и только после ответа, вправе ли ты его показывать (раздел 6).

## 3. Ролик 1: inbox — «каждое письмо разобрано, ничего не уходит без вас»

**Вне кадра:**

```bash
uv run autostudio inbox demo --out var/demo/inbox     # прогрев; сверь вывод со сценами ниже
# HTML-отчёт по событиям демо (необязательная сцена):
mkdir -p var/demo/report-data/demo-inbox
cp var/demo/inbox/state/events.sqlite var/demo/report-data/demo-inbox/
AUTOSTUDIO_DATA_DIR=var/demo/report-data uv run autostudio report examples/inbox/client.toml \
  --month 2026-10 --format html --out var/demo/inbox/report.html
```

Импортируй `var/demo/inbox/Inbox.csv` в демо-таблицу (вкладка `Inbox`). Адреса в демо вымышлены
(домены `.example`), маскировать их не нужно.

**В кадре:**

```bash
clear
uv run autostudio inbox demo --out var/demo/inbox
```

Вывод появляется сразу целиком. Дальше работаешь увеличением в монтаже.

| Секунды | В кадре | Голос (EN) | Подпись в кадре |
|---|---|---|---|
| 0–5 | Сцена `Title` | Ten emails to a tour agency. A fictional business, demo data. | Demo data, fictional business. Offline demo: a rule-based stand-in plays the AI model. |
| 5–17 | Команда и сводная таблица: 10 писем → Category, Urgency, Actions | One run sorts them all: leads, a complaint, an invoice, a newsletter. Each gets a category and an urgency. | Every email sorted |
| 17–29 | Quick Look черновика `drafts/01-re-quote-request-…eml`: та же тема с «Re:», язык отправителя, цены из фактов, подпись | Leads and questions get a reply draft in your Drafts folder. Same thread, the sender's language, only facts you approved. It is never sent on its own. | Standard and Premium. Never sent automatically. |
| 29–37 | Вкладка `Inbox` в таблице: имя, компания, телефон, бюджет, запрос. Затем строки `Owner notifications` из вывода | Key details go to your Google Sheet, and a hot lead pings you on Telegram. | Alerts: Standard and Premium |
| 37–48 | Увеличение: строка 7 `BLOCKED: injection…`, строка 10 `dropped unverified: phone`, строка `Model proposed, code verified…` | AI suggests, code checks. This email tries to give orders to the AI: it's flagged, and nothing is done with it. This phone number isn't in the email, so it's dropped. | Phone planted in the demo to show the check |
| 48–53 | Строка `Run 2 … nothing done twice` | Run it again, and nothing is done twice. | Safe to re-run |
| 53–60 | Сцена `End card` (раздел 1.7) | Upwork: *Want this for your inbox? Message me here on Upwork.* Fiverr: *Order or message me here on Fiverr.* Прямые: *I can sort your last twenty emails first, with nothing changed. Book a call.* | Карточка канала |

**Заметки:**
- Строки `Owner notifications` — это текст сообщений, которые в работе уходят в Telegram. Офлайн
  Telegram не вызывается. Если хочешь живой кадр, отправь тот же текст своему тестовому боту, но
  подпиши его «example alert».
- Сигнал оператору (`Operator alerts`) — на русском. Обрежь его.
- Обещание «sort your last twenty emails first» — только в прямой версии. На Upwork и Fiverr
  пробный разбор ящика возможен лишь после старта контракта (`upwork.md`, раздел 1).
- 🔄 Когда влито извлечение из PDF-счетов: добавь 5 секунд с вкладкой `Invoices` за счёт сцены
  29–37 и сверь поля с `docs/packages/inbox.md`.
- Необязательная сцена вместо 48–53: `var/demo/inbox/report.html` — «Suspicious emails blocked: 1».

## 4. Ролик 2: booking — «запись круглосуточно, меньше неявок»

Два способа снять переписку. Финальную версию лучше снять на телефоне (Б). Терминал (А)
работает без сети и токена.

**А. Терминал, без сети**

```bash
rm -rf var/rec/chat
uv run autostudio booking chat examples/booking/client.toml --language en --data-dir var/rec/chat
```

Ввод, проверенный 20.10.2026 (номера кнопок зависят от дня, сверяй с экраном):
`1` (Book a visit) → `1` (Haircut) → `1` (Giorgi) → `2` (Tomorrow) → номер времени → `1` (имя) →
`/share +995 5XX XX XX XX` (вымышленный номер; в монтаже размыть) → `1` (Confirm). Затем
свободным текстом: `haircut tomorrow after 5?` → `Do you take cards?` →
`Do you do hair coloring for kids?` (уходит владельцу). `/quit`. Расписание владельца:

```bash
uv run autostudio booking chat examples/booking/client.toml --admin --language en --data-dir var/rec/chat
# /tomorrow  (или /today, если записывался на сегодня), затем /quit
```

- В воскресенье салон закрыт. Если «завтра» — воскресенье, бери другой день из списка и меняй фразу.
- Первая строка чата и пометка `[→ администраторам]` — на русском. Обрежь их.
- Голос говорит «in Telegram», а на экране терминал. Добавь подпись: «Test console — customers see
  the same buttons in Telegram.»
- Напоминаний в консольном чате нет. Кадр с напоминанием бери из варианта Б или из
  `var/demo/booking/transcript.md` (разделы 8–9) — открой его в просмотрщике Markdown.

**Б. Тестовый бот в Telegram (лучше выглядит)**

1. В @BotFather создай бота с вымышленным именем «Demo Barbers». Описание: «Demo: bookings are not
   real.»
2. Вне кадра:

   ```bash
   uv run autostudio new-client rec-booking --package booking
   # clients/rec-booking/booking.toml:
   #   admin_chat_ids = [<твой числовой id из @userinfobot>]
   #   reminders_min_before = [1440, 70]   # только для записи: напоминание придёт через 5 минут
   #   csv_dir = "out" не трогай: путь считается от каталога данных клиента
   # clients/rec-booking/.env:
   #   BOOKING_BOT_TOKEN=<токен тестового бота>
   #   AUTOSTUDIO_LLM_PROVIDER=fake
   #   AUTOSTUDIO_DATA_DIR=var/rec/data
   uv run --env-file clients/rec-booking/.env autostudio booking check clients/rec-booking/booking.toml --online
   uv run --env-file clients/rec-booking/.env autostudio booking run clients/rec-booking/booking.toml
   ```

3. Запишись на время через 75 минут или позже: в образце нельзя записаться раньше чем за 60 минут.
   Напоминание за 70 минут придёт минут через пять.
4. Имя введи вымышленное. Телефон набери вымышленный. **Не нажимай «Share my number»**: кнопка
   отправит твой настоящий номер. Кнопка «Use "<имя>"» показывает твоё имя в Telegram — размой.
5. После записи: Ctrl+C, затем удали `clients/rec-booking/` и `var/rec/`. Бота удали в @BotFather
   или оставь для живых показов (`outreach.md`, 9.2).

**Отчёт и таблица (вне кадра):**

```bash
uv run autostudio booking demo --out var/demo/booking
AUTOSTUDIO_DATA_DIR=var/demo/booking/data uv run autostudio report examples/booking/client.toml \
  --month 2026-10 --format html --out var/demo/booking/report.html
```

`var/demo/booking/Bookings.csv` импортируй во вкладку `Bookings`.

| Секунды | В кадре | Голос (EN) | Подпись в кадре |
|---|---|---|---|
| 0–5 | `Title` | A booking bot for a barbershop. A fictional business, demo data. | Demo data, fictional business. |
| 5–20 | Меню → Haircut → Giorgi → Tomorrow → сетка времени → имя → телефон → Confirm → «You're booked! ✅» | Customers book in Telegram with buttons: service, barber, day, time, confirm. Only free times are shown, so no slot is booked twice. | Booking with buttons |
| 20–30 | `haircut tomorrow after 5?` → свободное время кнопками → нажатие | Or they just type: haircut tomorrow after five? The bot offers matching free times. Nothing is booked until they press Confirm. | Basic: simple rules. Standard and Premium: AI. |
| 30–37 | `Do you take cards?` → ответ из FAQ; вопрос не из FAQ → «passed it to our team» | Questions are answered from your own FAQ. Anything else is passed to you. | FAQ: Standard and Premium |
| 37–45 | Напоминание «⏰ Reminder…» с кнопкой «Change or cancel» | Reminders go out a day and two hours before the visit, with a button to change or cancel. | Reminder times are yours to set |
| 45–54 | Уведомление владельцу «🆕 New booking» → `/today` или `/tomorrow` → вкладка `Bookings` → `report.html` | You get a message for every booking, your day in one tap, and a monthly report. | Google Sheets: Standard and Premium |
| 54–60 | `End card` | Upwork: *Message me here on Upwork.* Fiverr: *Order or message me here on Fiverr.* Прямые: *Try the demo bot, or book a call.* | Карточка канала |

**Заметки:**
- В голосе нет слов «AI understands»: на экране заглушка, а тариф Basic разбирает такие фразы
  простыми правилами. Честно для обоих случаев.
- Подписи о тарифах следуют `offers.en.md`: FAQ и Google Sheets — Standard и Premium. С 20.10
  `docs/packages/booking.md` отвечает из FAQ правилами и в Basic, а Sheets ставит в Premium
  (`offers.md`, раздел 11, вопрос 2). Решишь иначе — поправь подписи.
- «Try the demo bot» — только для прямых клиентов, когда демо-бот на VPS работает
  (`outreach.md`, 9.2). На площадках ссылку на бота до заказа не даём (НП, `fiverr.md`, раздел 1).
- 🔄 Google Calendar (Standard): кадр «запись появилась в календаре» — после влития и проверки.
- 🔄 WhatsApp (Premium): отдельный ролик только после влития адаптера и теста на настоящем WABA, с
  шагом согласия (opt-in) в кадре. Без opt-in шаблоны напоминаний слать нельзя (П, бриф 5.2).
- Грузинский интерфейс не показываем и не обещаем: в коде только `en` и `ru`.

## 5. Ролик 3: prices — «цены конкурентов сами приходят в таблицу»

**Вне кадра:**

```bash
uv run autostudio prices demo --out var/demo/prices
mkdir -p var/demo/report-data/demo-prices
cp var/demo/prices/state/events.sqlite var/demo/report-data/demo-prices/
AUTOSTUDIO_DATA_DIR=var/demo/report-data uv run autostudio report examples/prices/client.toml \
  --month 2026-10 --format html --out var/demo/prices/report.html
```

С 20.10 демо кладёт и `var/demo/prices/report.md` — для Markdown-версии хватит его.
`Prices_now.csv`, `History.csv`, `Changes.csv` импортируй во вкладки `Prices now`, `History`,
`Changes`.

**В кадре:**

```bash
clear
uv run autostudio prices demo --out var/demo/prices
```

| Секунды | В кадре | Голос (EN) | Подпись в кадре |
|---|---|---|---|
| 0–5 | `Title` | Competitor prices for an electronics shop. Fictional shops, demo data. | Demo data, fictional business. Offline demo: a rule-based stand-in plays the AI model. |
| 5–17 | Вкладка `Prices now`: товар, ваша цена, цены Shop A/B/C, разница в % | Each check fills one Google Sheet: your price, each competitor's price, and the difference. | One sheet, every check |
| 17–29 | Вывод день 2: «Price changes / Cheaper than you / Availability» | You get one short Telegram message, only when something changed: a new price, a competitor cheaper than you, out of stock. | Standard: after each check. Basic: daily. |
| 29–41 | Колонка `Source` («schema.org data», «AI, verified quote»), затем строка `History` с текстом страницы | Every price shows where it came from: the page's product data, or the exact text it was read from. | AI reads pages without price data only in Premium, and only with a matching quote |
| 41–52 | Строки «page unavailable» и «skipped: the site asks robots not to visit this page» | A page that can't be read is marked, never filled with a guess. A site that asks robots not to visit is skipped. Public pages only. | Read before? You see the last known price, marked. No logins, no CAPTCHA tricks. |
| 52–60 | `End card` | Send me up to three competitor pages, and I'll tell you for free if the price can be read. Upwork: *Message me here on Upwork.* Fiverr: *Send them here on Fiverr.* Прямые: *Book a call.* | Карточка канала |

**Заметки:**
- В демо страница Shop A для AirPods не читается оба дня, поэтому прошлой цены у неё нет и ячейка
  пустая. Голос говорит ровно это: «marked, never filled with a guess». Про «last known price» —
  только в подписи.
- Блок `[operator channel, internal — the client does not see this]`: заголовок английский,
  содержимое на русском, и он не для клиента. Обрежь его.
- Бесплатная проверка трёх страниц — из `offers.en.md`. Это публичные ссылки, не доступы, поэтому
  её можно предлагать и на площадках через сообщения площадки.
- `probe` в ролик не входит: офлайн его показать не на чем, а настоящая страница конкурента в
  публичном видео — лишний риск. Если очень нужно — 🔄 одна статичная страница товара на твоём
  `{{DOMAIN}}` и `uv run autostudio prices probe https://{{DOMAIN}}/<страница> --contact-email {{EMAIL}}`.
  Вывод `probe` частично на русском («загрузка», «не спрашивали»), на английском ролике его
  придётся размыть.

## 6. Ролик 4 (необязательный): доказательство для inbox — агент выписок

**Условия, без которых не снимаешь:**
1. **Право показывать.** Проект делался как тестовое задание для компании. Получи ответ, что
   показывать код и описывать проект публично можно (`offers.md`, раздел 11, вопрос 7). Нет
   ответа — нет ролика.
2. **Только синтетическая выписка.** Реальные выписки из замера — чужие финансовые данные, их нет
   в репозитории. Ни номеров счетов, ни имён, ни сумм из них в кадре.
3. Интерфейс и вывод CLI проекта — на русском. Для английского ролика нужны подписи. Ключи JSON
   английские, их можно показывать без подписей.

**Вне кадра, в репозитории выписок:**

```bash
cd ~/BankStatementAITest         # путь к твоей копии проекта
uv sync
mkdir -p ~/demo-rec
uv run python -c "import sys; sys.path.insert(0, 'tests'); from pathlib import Path; \
from conftest import write_statement_pdf; write_statement_pdf(Path.home() / 'demo-rec' / 'Sample Bank 2025.pdf')"
uv run poe extract ~/demo-rec/"Sample Bank 2025.pdf" --json-out ~/demo-rec/sample.json
```

Проверено 20.10.2026: синтетическая выписка на одну страницу сходится классом A («ПОДТВЕРЖДЕНО»,
код возврата 0), в JSON `"opinion": "clean"` и `sources` у каждого итога.

🔄 **Несведённый период на синтетике.** Скопируй `STATEMENT_LINES` из `tests/conftest.py` в
отдельный скрипт вне репозитория и замени последнюю строку на `'04/02/2025 PAYMENT -35.00 115.00'`.
Напечатанные итоги останутся 30.00 и 120.00, поэтому период не должен сойтись на 5.00. Перед
вердиктом программа перечитает страницу через Tesseract, поэтому нужен `brew install tesseract`. В
среде проверки Tesseract не было, и этот путь не прогнан. Прогони у себя и снимай только то, что
увидишь.

🔄 **Веб-стенд** `uv run poe ui` → `http://127.0.0.1:8000` в проверке не запускался. Загрузи в него
синтетический PDF. Стенд показывает заключение, объём проверки и вырезку страницы с рамкой вокруг
строки (README проекта).

| Секунды | В кадре | Голос (EN) | Подпись в кадре |
|---|---|---|---|
| 0–6 | `Title` | A tool I built that reads bank statements. The screen shows a synthetic statement. | Synthetic statement. Not a client document. |
| 6–20 | Загрузка PDF в стенд → разбор | It extracts every transaction from a PDF, including scans. Then it checks the statement's own arithmetic: opening balance plus deposits minus withdrawals must equal the closing balance. | Opening + deposits − withdrawals = closing |
| 20–32 | Заключение «подтверждено»; 🔄 затем несведённый период с расхождением | If the numbers match to the cent, the period is confirmed. If not, it is flagged with the gap and a likely cause, and never passed as correct. | Confirmed / not confirmed |
| 32–42 | Клик по номеру страницы → вырезка с рамкой; фрагмент JSON с `sources` | Every number keeps its page and position. Click the page number, and you see that line on the original page. | Every number traceable |
| 42–52 | Таблица замера из README, имена файлов заменены на «Statement 1–4» | On real statements from four banks, two AI models re-reading the hard pages reconciled all thirteen periods, for sixty-nine cents. A small sample, not a benchmark. | 13 of 13 with AI, $0.69. Without AI: 5 of 13. |
| 52–60 | `End card` | My inbox setup follows the same rule: a number that isn't in the source never reaches your sheet. + фраза канала из 1.7 | Карточка канала |

**Как говорить о проекте** (как в `offers.md`, 3.4): не говори, что он работает с TBC или Bank of
Georgia (не проверялось), с мультивалютными выписками и кредитными картами (не поддерживаются) и
что он встроен в inbox (это отдельная кодовая база). Цифры 13 из 13 и $0,69 — из README проекта
(прогон с `gpt-5.4-mini,gpt-5.6-sol`). Перезапустил замер — поправь строку.

## 7. Живой показ на данных клиента: протокол на 5 минут

Это одна сцена по главной боли клиента, а не всё демо. Место в разговоре — минуты 9–12 созвона
(`outreach.md`, 6.1).

### 7.1 Правила для всех трёх пакетов

1. **Согласие до показа.** Для booking и prices достаточно устного «да» на созвоне. Для inbox —
   письменное согласие и подписанный DPA (раздел 7.4).
2. **Только данные самого клиента** и только то, что он сам назвал: его услуги, ссылки на страницы
   конкурентов, его ящик.
3. **Не записываешь.** Ни видео, ни скриншотов. Клиент видит всё, что видишь ты.
4. **Модель — заглушка по умолчанию** (`AUTOSTUDIO_LLM_PROVIDER=fake`): ничего не уходит
   провайдеру. Настоящая модель — только если клиент согласился, что его текст уйдёт в OpenAI или
   Anthropic по API.
5. **Рабочая папка `var/live/`** (она в `.gitignore`), в конце удаляется целиком.
6. **На площадках** созвон — средствами площадки. Доступы к ящику до старта контракта не берём
   (НП, `upwork.md`, раздел 1). Показ prices и booking доступов не требует.
7. Не получилось вживую — показывай записанный ролик, а не чини на глазах у клиента дольше минуты.

### 7.2 За 10 минут до созвона

```bash
cd ~/autostudio-demo                         # демо-копия, без clients/
env -i HOME="$HOME" PATH="$PATH" TERM="$TERM" LANG=en_US.UTF-8 bash --noprofile --norc
export PS1='$ ' AUTOSTUDIO_LLM_PROVIDER=fake
unset HISTFILE
uv run autostudio demo-all --out var/demo    # всё проходит без ошибок
rm -rf var/live && mkdir -p var/live
```

Окна с клиентами, почтой и Telegram закрыты, включено «Не беспокоить». Показываешь одно окно
терминала, не весь экран.

### 7.3 booking: прайс клиента в копии демо-конфига

Прайс клиента лучше перенести до созвона, если он публичный (сайт, Instagram). Иначе внеси 2–3
услуги на созвоне, это минута.

```bash
cp examples/booking/client.toml var/live/booking.toml
```

Что поменять в `var/live/booking.toml`:
- `[client]`: `id = "live-demo"`, `name = "<название клиента>"`;
- `[booking.hours]`: часы клиента; `closed_dates = []`;
- `[[booking.services]]`: 2–3 услуги клиента (название `en`/`ru`, длительность, цена, валюта).
  Строку `staff_ids` у комплекса удали;
- `[[booking.staff]]`: один мастер. Второго мастера и его блок `[booking.staff.hours]` удали
  целиком;
- `[[booking.faq]]`: 1–2 ответа клиента (адрес, оплата), остальные удали;
- `contact_phone`: публичный номер бизнеса клиента, чтобы бот не показал ему чужой номер.

```bash
uv run autostudio booking check var/live/booking.toml --offline
uv run autostudio booking chat var/live/booking.toml --language ru --data-dir var/live/chat   # или --language en
```

Проверено 20.10.2026 на урезанной копии (один мастер, две услуги, один вопрос FAQ): `check
--offline` проходит и показывает ближайшие слоты, запись проходит, `--admin` → `/tomorrow`
показывает её. Чат пишет базу и `Bookings.csv` только в песочницу `--data-dir`
(`var/live/chat/live-demo/`), боевые каталоги не трогает.

| Минута | Что делаешь | Что говоришь |
|---|---|---|
| 0–1 | `check --offline`: услуги и ближайшие свободные слоты | «Это ваш прайс и ваши часы в моём тестовом боте» |
| 1–3 | Клиент диктует, ты нажимаешь: услуга → день → время → вымышленное имя и номер → «Подтвердить» | «Свободное время считает программа по вашим часам. Одно время дважды не продать» |
| 3–4 | Фраза клиента свободным текстом; вопрос из FAQ | «Без модели бот понимает простые фразы. В тарифе Standard текст читает модель, но записывает всё равно только кнопка „Подтвердить“» |
| 4–5 | `--admin` → `/today` или `/tomorrow` | «Так вы видите свой день в Telegram» |

- Номера телефонов — только вымышленные. Попроси клиента не вводить данные его покупателей.
- Грузинского языка в боте нет. Спросили — так и скажи: доработка, отдельная оценка.
- В конце: `rm -rf var/live`.

### 7.4 inbox: dry-run на ящике клиента

**Все условия обязательны. Нет хотя бы одного — показываешь демо (раздел 3) или dry-run на своём
тестовом ящике с вымышленными письмами.**

1. Прямой клиент. На Upwork и Fiverr — только после старта контракта, первым этапом.
2. Письменное согласие (текст ниже) и подписанный DPA (`docs/legal/dpa.en.md` или `dpa.ru.md`).
   Шаблон DPA ещё не проверен юристом. Пока юрист его не проверил, на ящике клиента не
   показываешь (`docs/legal/README.md`: DPA — до подключения ящика, даже до `dry-run`).
3. В ящике нет переписки о здоровье, судебных делах и т. п. Ящик клиники — нет (`discovery.md`,
   раздел 2).
4. Общий рабочий адрес (`info@`, `sales@`), а не личный ящик сотрудника.
5. Клиент сам создаёт пароль приложения на этот показ и отзывает его сразу после. На встрече
   лично он вводит пароль сам. По видеосвязи он присылает его одноразовой ссылкой для секрета,
   и ты вставляешь его в `read -rs`. В чат, письмо или сообщение пароль не пишется. Основной
   пароль и коды 2FA не берёшь никогда.

**Согласие (клиент отправляет тебе письмом или сообщением):**

```text
EN: I agree that {{NAME}} connects to the mailbox [address] during our call on [date], reads up to [10] emails from the last 14 days and shows how they would be sorted. To sort them, each email's sender, subject and text are sent to [OpenAI / Anthropic] through its API, under its API terms. Nothing in the mailbox is changed, sent, deleted or marked as read. Nothing is saved or recorded after the call. I will revoke the app password right after the call.
RU: Я согласен, что {{NAME}} во время созвона [дата] подключится к ящику [адрес], прочитает до [10] писем за последние 14 дней и покажет, как они были бы разобраны. Для разбора отправитель, тема и текст каждого письма передаются в [OpenAI / Anthropic] через API, на условиях API этого провайдера. В ящике ничего не меняется, не отправляется, не удаляется и не помечается прочитанным. После созвона ничего не сохраняется и не записывается. Пароль приложения я отзову сразу после созвона.
```

**Ключ модели для показов** держи в отдельном файле `~/.autostudio/live-model.env`
(`AUTOSTUDIO_LLM_PROVIDER`, ключ провайдера), никогда не открывай его на экране. Ключ — отдельный,
с низким лимитом расходов в кабинете провайдера (бриф 5.7).

```bash
cp examples/inbox/client.toml var/live/inbox.toml
# правки: [client] id = "live-inbox", name = "<клиент>"; [inbox] imap_host = "<сервер провайдера>".
#         Категории можно оставить стандартными. [sink] не важен: dry-run в таблицу не пишет.
read -r INBOX_IMAP_USER           # адрес ящика
read -rs INBOX_IMAP_PASSWORD      # пароль приложения (условие 5); на экран не печатается
export INBOX_IMAP_USER INBOX_IMAP_PASSWORD
unset AUTOSTUDIO_LLM_PROVIDER     # иначе fake из оболочки перекроет файл с ключом (проверено)
uv run --env-file ~/.autostudio/live-model.env autostudio inbox check var/live/inbox.toml --connect
uv run --env-file ~/.autostudio/live-model.env autostudio inbox dry-run var/live/inbox.toml --limit 10
unset INBOX_IMAP_USER INBOX_IMAP_PASSWORD
export AUTOSTUDIO_LLM_PROVIDER=fake
rm -rf var/live
clear && printf '\033[3J'         # очистить и прокрутку терминала
```

- **Только `dry-run`. Никогда `run` на ящике клиента во время показа:** `run` кладёт черновики в
  ящик и копирует письма в папки, а клиент согласился только на просмотр.
- `dry-run` не создаёт черновиков, не пишет в таблицу и журнал и не помечает письма прочитанными
  (так написано в коде команды). На экран выводятся дата, отправитель, тема, категория,
  срочность, уверенность и «что бы сделали». Текстов писем на экране нет.
- 🔄 Живой прогон `check --connect` и `dry-run` в проверке не сделан. Прогони оба на своём тестовом
  ящике до первого показа и замерь время на 10 писем.
- Перед `dry-run` убедись, что вывод **не** начинается с «⚠ модель-заглушка»: значит, модель
  подключена. Клиент не согласен на передачу писем провайдеру — не снимай `AUTOSTUDIO_LLM_PROVIDER`
  и убери `--env-file`: разбор пойдёт на заглушке, колонка `Via` покажет `stub`. Качество тогда
  не показательно — так и скажи.
- Workspace с запретом паролей приложений: показ невозможен. 🔄 Вход через OAuth дописывается.
- Outlook и Microsoft 365: прямого входа нет. Показывай демо.
- После созвона напомни клиенту отозвать пароль приложения и упомяни это в итоговом письме.

### 7.5 prices: probe по ссылкам клиента

Клиент называет до трёх страниц товаров у конкурентов. Сначала открой каждую вместе с ним в
браузере:
- цена видна без входа, без выбора города и без корзины;
- нет «Checking your browser», CAPTCHA или «Access denied».

Хотя бы одно «нет» — этот сайт не проверяешь и не мониторишь, так и говоришь. Если уже известно,
что условия сайта прямо запрещают автоматический сбор, — тоже не запускаешь.

```bash
uv run autostudio prices probe "<ссылка на товар>" --contact-email {{EMAIL}}
```

- Одна страница и robots.txt, пауза и User-Agent с твоим адресом — по правилам пакета.
- ✓ на ступени `json_ld`, `microdata` или `meta` — сайт читается без селектора.
- Предложен селектор — сверь цену с браузером. Несколько кандидатов с разными ценами и «⚠ сверьте
  с сайтом» — смотри глазами вместе с клиентом.
- `robots_disallowed`, 403, 429 или «цены нет ни на одной ступени» — сайт в оценку не входит. Не
  повторяй запрос и не подбирай обходы.
- `--llm` с настоящей моделью на показе не включай: текст чужой страницы уйдёт провайдеру, а
  платная ступень — только Premium.
- Probe — одна загрузка, а не мониторинг. Мониторинг начинается только после письменного
  подтверждения клиента в заказе (`docs/packages/prices.md`, «Право и этика»).
- 🔄 В проверке probe не прогнан с сетью. Прогони на 2–3 публичных страницах заранее, чтобы знать
  вывод.

### 7.6 После показа

- В течение 24 часов — итог по шаблону `outreach.md`, 10.1.
- Данные клиента не храним: `var/live/` удалена, в списке продаж — только данные бизнеса.
- inbox: клиент отозвал пароль приложения. Подтверди это в итоговом письме.

## 8. Перед каждой записью и показом: короткий список

- [ ] Демо прогнаны сегодня, вывод сверен со сценой (раздел 0).
- [ ] Чистая оболочка, `AUTOSTUDIO_LLM_PROVIDER=fake`, `env | grep -ciE …` = 0.
- [ ] Window Capture, «Не беспокоить», лишние окна закрыты.
- [ ] Телефоны размыты, русские служебные строки обрезаны, адресная строка обрезана.
- [ ] Первый кадр — «Demo data, fictional business», пометка про заглушку модели.
- [ ] Финальная карточка своего канала (1.7). На площадках — без контактов и ссылок.
- [ ] Ни одной функции с 🔄 в кадре и в голосе.

## 9. Открытые вопросы к тебе

1. **Язык голоса.** Говоришь ли ты по-английски в кадре? Если нет — версия с субтитрами без голоса.
2. **Проект выписок.** Есть ли разрешение показывать его публично? Без него ролика 4 нет.
3. **Демо-бот на VPS** для прямых клиентов (`outreach.md`, 9.2): поднимать до записи ролика booking?
   От этого зависит последняя фраза прямой версии.
4. **Страница товара на `{{DOMAIN}}`** для показа `probe` в ролике: нужна или обойдёмся без `probe`?
5. **Вымышленные бизнесы.** Проверь «Demo Barbers», «Demo Tours», «Tech Corner Tbilisi» и «Hotel
   Rustaveli» по Google Maps до первой записи.
6. **Рецепт отчёта для inbox и prices** требует копировать `events.sqlite` (разделы 3 и 5): демо
   кладёт журнал в `<out>/state/`, а `report` ищет его в `<AUTOSTUDIO_DATA_DIR>/<client-id>/`.
   Выровнять это в ядре или в демо — задача разработки, а не продаж.
