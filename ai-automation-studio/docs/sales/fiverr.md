# Fiverr: launch kit

> **Оператору.** Тексты для покупателей — на английском, их копируют в Fiverr как есть: блоки `text`,
> названия и описания пакетов, FAQ, вопросы требований. Всё остальное — инструкции для тебя, на
> русском. Обозначения:
> - `{{NAME}}`, `{{YEARS}}`, `{{PHOTO}}` — твои личные данные, заполни сам; ничего не выдумывай.
>   Остальные плейсхолдеры (`{{TELEGRAM}}`, `{{WHATSAPP}}`, `{{EMAIL}}`, `{{DOMAIN}}`,
>   `{{CALENDAR_LINK}}`, `{{GITHUB}}`) на Fiverr не используются нигде (раздел 1).
> - `[в квадратных скобках]` — подставь под конкретного покупателя.
> - **П / И / НП** — статусы фактов из `docs/research/DECISION_BRIEF.md`. **НП** — не подтверждено:
>   fiverr.com и help.fiverr.com из среды проверки не открылись. 20.10.2026 я сверил часть правил по
>   сниппетам справки Fiverr в поиске; сниппет — это тоже **НП**, проверь в кабинете (раздел 9).
>
> Цены и состав пакетов совпадают с `docs/sales/offers.en.md` и `docs/sales/upwork.md`. Если они
> разошлись, прав `offers.en.md`: поправь этот файл. Плейсхолдеры и их формат —
> `docs/sales/offers.md`, раздел 0.1.
>
> «AutoStudio» — рабочее название. На Fiverr оно не нужно: покупатель нанимает тебя лично.
> Имя пользователя Fiverr, по распространённым сведениям, потом не меняется (**НП**): бери его от
> своего имени, а не «autostudio», пока не проверено, что название свободно (`docs/ACTION_PLAN.md`).

## 1. Rules: read before anything else

> **Оператору.** Одно нарушение — предупреждение, снятие гига или бан (бриф 3.1–3.2, **НП**). Fiverr —
> вспомогательный канал (бриф, риск 11): ради одного заказа его не стоит терять.

| Правило | Что именно делать | Статус |
|---|---|---|
| **Всё внутри Fiverr** | Переписка, файлы, созвоны, оплата, поддержка — только через Fiverr, всегда, а не 24 месяца, как на Upwork. Не предлагай оплату мимо Fiverr, даже если покупатель просит сам («так дешевле»). Ответ один: «I keep all work with Fiverr clients on Fiverr; it protects us both». Пилотную скидку за кейс из `offers.en.md` на Fiverr не предлагаем никогда. | НП |
| **Никаких контактов нигде** | Ни в профиле, ни в гиге, ни в картинках, видео и PDF галереи, ни в сообщениях, ни в файлах сдачи. Под запретом `{{TELEGRAM}}`, `{{WHATSAPP}}`, `{{EMAIL}}`, `{{DOMAIN}}`, `{{CALENDAR_LINK}}`, `{{GITHUB}}`, телефон, @username демо-бота, QR-коды на свои ресурсы. Фиктивный телефон из демо-конфига (`contact_phone` в `examples/booking/client.toml`) в кадре тоже замажь: фильтр не знает, что он ненастоящий. `offers.en.md` покупателю с Fiverr не отправляй вовсе: гиг и есть прайс. | НП |
| **Ссылок в гиге нет** | Справка Fiverr (сниппет) разрешает ссылку на портфолио, старые правила — только fiverr.com. Мы ставим ноль ссылок: отказ в публикации гига дороже, чем ссылка. Ссылку на GitHub не даём ещё и потому, что в профиле и коммитах могут быть видны контакты. Доказательства — картинками, видео и PDF в галерее. | НП |
| **ИИ не скрываем** | В каждом гиге написано, в каком пакете работает модель (OpenAI или Anthropic) и в каком нет. Если форма гига спрашивает про ИИ — отвечай «да» во всех трёх гигах. Результат работы модели дорабатываешь под покупателя: свои конфиги, факты, проверки. Сообщения покупателю пишешь сам; если черновик сделал ИИ, проверь каждое утверждение. | НП, бриф 3.2 |
| **«100% без ИИ» не обещаем** | Код написан с помощью ИИ-инструментов и проверен тестами, поэтому формулировки «AI-free», «no AI used», «100% human» запрещены. Правда звучит так: «Basic doesn't call an AI model while it runs». Если покупатель просит работу без ИИ, это требование надо выполнить. Без модели работают booking Basic и prices Basic/Standard. inbox без модели не работает: такому покупателю inbox не продаём и честно говорим почему (`offers.md`, 10.4). | НП, бриф 3.2 |
| **Пароли не просим** | Ни основной пароль, ни коды 2FA, ни вход в аккаунты покупателя. Только отзываемые доступы и никогда в чате. Токен бота, который покупатель создал сам, — через форму требований заказа (Gig requirements). Пароль приложения почты — не через форму: ответ на требования остаётся на странице заказа. Пока поддержка Fiverr письменно не ответила (2.1, п. 1), пароль передаётся на созвоне Fiverr (раздел 6.6). Таблицу покупатель расшаривает на сервисный аккаунт. | НП, бриф 3.2; практика |
| **Запрещённые заказы** | Не берём: сайты за логином, обход CAPTCHA и антибот-защиты, сбор, который запрещают условия сайта, лид-листы и персональные данные, массовые рассылки, «ChatGPT в WhatsApp» (с 15.01.2026 Meta разрешает только бота конкретного бизнеса, **П**). Пакеты с ИИ для покупателя или его аудитории в России и Беларуси — отказ (бриф, риск 15). Отказывай до заказа, а не отменой: отмены портят статистику продавца. | НП / П |
| **Отзывы** | Скидку, бонус или поддержку в обмен на отзыв не предлагаем. После завершения заказа можно один раз попросить честный отзыв. | не проверено |
| **Демо до заказа** | Ссылку на демо-бота в Telegram до заказа не отправляем: это похоже на увод общения (`offers.md`, 4.5). Демо — видео в галерее и видео в сообщении. Ссылка на бота самого покупателя после начала заказа — часть работы. | НП |
| **Аккаунт** | Один аккаунт, настоящее имя как в документе, вход только из Грузии и без VPN. Верификация не прошла — второй аккаунт не создаём, снимаем канал (бриф, риск 1). | НП |
| **Поддержка — только через Fiverr** | Ежемесячная поддержка клиента с Fiverr — подпиской Fiverr или повторяющимся custom offer, иначе новым заказом каждый месяц. Gig extra для поддержки не годится. Подробно — раздел 7. | НП |
| **Обещаем только то, что в коде** | Строки про функции, которые дописываются сейчас, публикуются только после того, как функция влита и тесты зелёные. Список и запасные формулировки — раздел 2.1. | решение |

## 2. Before you publish

### 2.1 Gate: publish only when every line is checked

1. **Неделя 1 по брифу (3.3):** регистрация, проверка личности (документ и селфи, **НП**), экран
   Earnings → Withdraw со способами вывода для Грузии. Письменно спроси поддержку Fiverr, можно ли
   продавать с твоим гражданством, и сохрани ответ. В том же обращении спроси, можно ли в заказе
   передать покупателю адрес сервисного аккаунта Google (вида `…@….iam.gserviceaccount.com`): это
   технический адрес, не почтовый ящик, но фильтр контактов может принять его за e-mail. И третий
   вопрос: можно ли получить от покупателя пароль приложения почты через форму требований заказа
   или одноразовой ссылкой на сервис секретов (это внешняя ссылка), или только на созвоне Fiverr.
2. **Тестовый вывод.** До первого заказа выводить нечего. Поэтому первый вывод после первого заказа и
   есть тест: выведи всё и запиши фактические списания (бриф 2.2, п. 7: PayPal — 0 со стороны Fiverr,
   Payoneer — 3 USD, банк через Payoneer — 1 или 3 USD; всё **НП**).
3. **Как покупатель:** открой оформление заказа на ≈ $99 и ≈ $150 и посмотри сбор за маленький заказ
   (бриф 3.3).
4. **Деньги — после ИП.** Не принимай заказ, пока не выполнены пункты 1–4 из `offers.md`, 10.6:
   ИП, статус малого бизнеса в rs.ge, ответы бухгалтера и юриста. Гиг можно поставить на паузу.
5. **Функции, которые дописываются сейчас.** На 20.10.2026 в коде их нет (`grep` по `src/autostudio`):

   | Функция | Где она в этом файле | Пока её нет в коде |
   |---|---|---|
   | booking: синхронизация с Google Calendar | 4.3: описание Standard и строка «Bookings in your Google Calendar»; 4.6, вопрос 8 | описание Standard «до Calendar»; строку таблицы убери; в вопросе 8 без слов «and your Google Calendar» |
   | booking: адаптер WhatsApp Cloud API | 4.1 заголовок; 4.2 последний абзац; 4.3 название, описание и строка Channel у Premium; 4.5 последний вопрос; 4.6, вопрос 9; метаданные гига | вариант «до WhatsApp» везде, где он дан; вопрос 9 не публикуй. Premium продаётся за $950 без WhatsApp, как Advanced в `upwork.md`, 3.1 |
   | inbox: поля из PDF-счетов | 6.1 заголовок и тег «invoice extraction»; 6.2 пункт про PDF; 6.3 название, описание и строка таблицы у Standard; 6.5 пятый вопрос; 6.7 кадр Invoices | вариант «до PDF» везде, где он дан; строку таблицы и кадр убери |
   | inbox: вход в Workspace через XOAUTH2 | нигде не назван, сказано только «your admin may need to allow access» | менять нечего |

   Для WhatsApp на Fiverr есть и нерешённый вопрос доступа: приглашение админом в Business portfolio
   покупателя требует твоего e-mail (контакт), а токен system user — это секрет. Реши его до того,
   как включишь WhatsApp в Premium (вопрос в конце ответа).
6. **Модель по умолчанию** — Haiku 5.5 с effort `low`: в коде это уже умолчание для Anthropic
   (`offers.md`, раздел 1, пункт 2). Не ставь покупателю `openai` на своём ключе: `gpt-5.4-mini`
   дороже, и лимиты поддержки уходят в минус.
7. **Проект выписок** (доказательство в гиге inbox) — тестовое задание. Убедись, что вправе его
   показывать (`offers.md`, 11.7). Если нет — убери абзац «Proof of approach» и PDF из галереи.
8. **Не покупать на старте:** Seller Plus ($15/мес, бриф 7.1). Fiverr Ads — только с Level 1
   (бриф 3.1). Level 1 по брифу: 5 заказов, 3 клиента, $400, рейтинг 4,4+, ответы на 80% сообщений
   (**НП**, пороги могли поменяться).
9. **Гигов три, лимит новичка — четыре (НП).** Четвёртый слот оставь свободным: он понадобится, если
   Fiverr откроет тебе подписки и ты решишь вынести поддержку в отдельный гиг (раздел 7.2).
   Порядок гигов на профиле: inbox первым (бриф 4.1: выше чек и есть доказательство), если Fiverr
   даёт менять порядок.

### 2.2 Profile

> **Оператору.** Лимит описания профиля не проверен (**НП**); текст ниже — 485 знаков вместе с
> плейсхолдерами. Фото `{{PHOTO}}` — настоящее лицо, похожее на фото в документе. Языки — честно. Если
> английский не рабочий, пиши в гиге и сообщениях коротко и проси переписку, а не созвон: для заказа с
> фиксированным составом письменное ТЗ всё равно лучше. Отвечай на сообщения в течение нескольких
> часов: скорость ответа видна покупателям и входит в пороги уровня.

```text
I'm {{NAME}}, a Python developer with {{YEARS}} years of experience, based in Tbilisi, Georgia. I set up three ready-made automations for small businesses: a booking bot, a competitor price monitor and an inbox assistant. The AI suggests; my code checks before anything reaches you. A phone number that isn't in the email is dropped, and a price without a matching quote from the page is never shown. I build and run each system myself, and I tell you up front what a package can't do.
```

## 3. Prices on Fiverr

> **Оператору.**
> - **Сейчас цена на Fiverr = долларовая витрина** (`offers.en.md`, бриф 4.2.2). На руки остаётся
>   80%: Fiverr берёт 20% со всего, включая чаевые (бриф 3.1, **НП**).
> - **Позже — витрина / 0,8.** После первых отзывов поставь цену так, чтобы нетто сравнялось с прямой
>   продажей (бриф 4.2.3). Колонка «позже» округлена вверх до $5: если поле цены принимает любые
>   целые доллары, бери точное значение (например, $188 вместо $190). Шаг цены в форме не проверен
>   (**НП**). Если форма не примет $49, $149 и $349, ставь $50, $150 и $350.
> - **Сбор за маленький заказ платит покупатель:** 3,50 USD при заказе до 200 USD или, по другой
>   странице, 3 USD до 100 USD (бриф 3.1, **НП**). Сейчас под него попадают inbox Basic ($150), prices
>   Basic ($120), поддержка Care ($49) и Pro ($149); после перехода на витрину / 0,8 — те же четыре.
> - **Когда поднимать витрину:** после 5–10 отзывов — на 30–50% (бриф 4.2.2). Подписанные заказы
>   остаются по старой цене.
> - Поменял цены на Fiverr — поменяй и суммы в FAQ «What happens after delivery?» и в шаблоне
>   раздела 7.4.

| Пакет | Basic | Standard | Premium | Нетто сейчас (80%) | Позже: витрина / 0,8 |
|---|---|---|---|---|---|
| booking | $200 | $450 | $950 | $160 / $360 / $760 | $250 / $565 / $1,190 |
| prices | $120 | $300 | $700 | $96 / $240 / $560 | $150 / $375 / $875 |
| inbox | $150 | $400 | $900 | $120 / $320 / $720 | $190 / $500 / $1,125 |
| Поддержка в месяц | Care $49 | Pro $149 | Business $349 | $39.20 / $119.20 / $279.20 | $65 / $190 / $440 |
| Час работы сверх пакета или тарифа | $40 | — | — | $32 | $50 |

**Срок сдачи.** Fiverr считает календарные дни с момента, когда покупатель ответил на требования
(**НП**). В `offers.en.md` — рабочие дни. Пересчёт: рабочие дни × 7/5, округлено вверх. Если в
выпадающем списке Fiverr нет такого числа, бери ближайшее большее (**НП**: набор значений не
проверен).

| Пакет | Basic | Standard | Premium |
|---|---|---|---|
| booking | 4 р. дня → **6 дней** | 7 → **10 дней** | 14 + время Meta → **21 день** |
| prices | 3 → **5 дней** | 5 → **7 дней** | 10 → **14 дней** |
| inbox | 3 → **5 дней** | 6 → **9 дней** | 12 → **17 дней** |

**Правки (revisions): 2 во всех пакетах** — «два круга правок до запуска» из `offers.en.md`, раздел
Terms. Бесплатные исправления после запуска (14 или 30 дней) — отдельно: это ошибки, а не правки.

**Оплата.** На Fiverr покупатель платит всю сумму при заказе, Fiverr держит её до завершения, поэтому
схема 50/50 не нужна. Milestones на Fiverr — только если доступны в категории (базовый пакет от
$100, этап от $50, **НП**): имеет смысл для Premium, если покупатель сам просит. Заказ закрывается
автоматически через 3 дня после сдачи; деньги доступны через 14 дней после завершения (бриф 3.1,
**НП**). Правило приёмки «5 рабочих дней» из `offers.en.md` на Fiverr не действует: действуют правила
Fiverr.

---

## 4. Gig 1: Booking bot

### 4.1 Title, category, tags

**Title** (до WhatsApp), 75 знаков вместе с «I will»:

```text
I will build a Telegram booking bot with reminders for your salon or clinic
```

**Title** (после выхода адаптера WhatsApp), 74 знака:

```text
I will build a Telegram or WhatsApp booking bot with appointment reminders
```

**Category** (**НП**: дерево категорий Fiverr не проверено): Programming & Tech → Chatbot Development
(подкатегория для чат-ботов; в метаданных — платформа Telegram, после адаптера ещё WhatsApp).
Запасной вариант: Programming & Tech → AI Development → AI Agents.

**Search tags** (5, у каждого до 20 знаков):

```text
telegram bot
booking bot
appointment booking
appointment reminder
salon booking
```

> **Оператору.**
> - Как выбрать подкатегорию: открой её как покупатель. Подходит, если в топе продают то же самое
>   (боты записи) и если в метаданных есть Telegram. При равных вариантах бери ту, где Fiverr
>   предлагает Subscriptions (раздел 7).
> - Метаданные заполняй честно: только Telegram, пока нет WhatsApp; «AI» — да (Standard и Premium).
> - «Clinic» в заголовке не означает медицинских данных: бот хранит только имя, телефон, услугу и
>   время. Клиника хочет хранить диагнозы — отказ от этой функции (бриф, риск 16). Но в Standard и
>   Premium всё, что пациент напечатал, уходит модели как есть, а данные о здоровье — особая
>   категория (НП). Клинике по умолчанию предлагай Basic (без модели); если нужен Standard —
>   строка в приветствии бота «please don't write about your health here» (`offers.md`, 4.1) и
>   вопрос юристу (`docs/legal/README.md`, раздел 4, п. 13).
> - Лимиты: заголовок 80 знаков и 5 тегов — сниппет справки и сторонние гайды (**НП**); 20 знаков на
>   тег — только сторонний гайд (**НП**).

### 4.2 Description

> **Оператору.** 1 141 знак с переносами строк при лимите 1 200 (**НП**). После выхода WhatsApp
> замени последний абзац на вариант ниже: получится 1 186 знаков.

```text
Customers book themselves in, 24/7, without calling you. Reminders before each visit help them remember.

What your customers get:
• Booking with buttons: service, staff, day, time, confirm
• Only free times are shown. The same slot can't be booked twice
• Reminders 24 h and 2 h before, with a "change or cancel" button
• They cancel or move a booking themselves, up to your cut-off

What you get:
• A Telegram message for every booking, cancellation and change
• Your schedule in the bot: /today, /tomorrow, /week
• A monthly report, including bookings made while you were closed

Where AI is used: Standard and Premium use an AI model (OpenAI or Anthropic) to read typed messages like "haircut tomorrow after 5?" and to answer from your FAQ only. Other questions come to you. Only a tap on "Confirm" makes a booking. Basic runs without an AI model.

The bot is created on your own Telegram account, so it's yours. Languages: English and Russian.

From launch, a monthly support plan ordered here on Fiverr keeps it hosted. Free fixes (14 or 30 days) come on top.

Need WhatsApp, Instagram or online prepayment? Message me before ordering.
```

Последний абзац после выхода адаптера WhatsApp:

```text
Premium adds WhatsApp on your own WhatsApp Business account; Meta bills its fees to your card. Please message me first.
```

### 4.3 Packages

> **Оператору.** Название пакета — до 30 знаков, описание — до 100 (лимиты сторонних гайдов, **НП**).
> Галочки и строки, которые Fiverr предлагает для категории, ставь только там, где это правда; всё,
> чего нет среди них, уже сказано в описании пакета.

| | Basic | Standard | Premium |
|---|---|---|---|
| **Name** | `Starter: buttons and reminders` | `AI and Google Sheets` | `Telegram and WhatsApp` · до WhatsApp: `Team bot: up to 10 staff` |
| **Description** | `Telegram bot, 1 staff member, booking with buttons, reminders 24 h and 2 h before. No AI model.` | `Up to 3 staff, AI reads typed requests, FAQ answers, bookings log in Google Sheets and Calendar.` · до Calendar: `Up to 3 staff, AI reads typed requests, answers from your FAQ, bookings log in Google Sheets.` | `Up to 10 staff, WhatsApp on your own account, your own bot texts, staff chat, 30 days of fixes.` · до WhatsApp: `Up to 10 staff, your own wording for every bot message, a staff group chat, 30 days of fixes.` |
| **Price** | $200 | $450 | $950 |
| **Delivery** | 6 days | 10 days | 21 days |
| **Revisions** | 2 | 2 | 2 |
| Channel | Telegram | Telegram | Telegram + WhatsApp (your WABA) · до WhatsApp: Telegram |
| Staff with own schedules | 1 | up to 3 | up to 10 |
| Buttons booking, free slots, no double booking | ✓ | ✓ | ✓ |
| Reminders 24 h and 2 h, self cancel and reschedule | ✓ | ✓ | ✓ |
| Owner messages, `/today`, `/tomorrow`, `/week` | ✓ | ✓ | ✓ |
| Typed requests | simple rules, no AI | AI | AI |
| FAQ answers, other questions to you | — | ✓ | ✓ |
| Google Sheets log and schedule | — | ✓ | ✓ |
| Bookings in your Google Calendar | — | ✓ (после Calendar) | ✓ (после Calendar) |
| Your own bot texts, staff group chat | — | — | ✓ |
| Monthly report | ✓ | ✓ | ✓ |
| Free fixes after launch | 14 days | 14 days | 30 days |

> **Оператору.**
> - 21 день в Premium — это 20 по формуле плюс день запаса. Время Meta (одобрение шаблонов
>   напоминаний) ты не контролируешь. Поэтому до заказа Premium проверь в переписке, что у покупателя
>   есть Business portfolio, номер и карта (раздел 4.6, вопрос 9). Если Meta задерживает, проси у
>   покупателя продление срока через заказ (**НП**: как работает продление, не проверено).
> - WhatsApp: сообщения Meta оплачивает карта покупателя в WhatsApp Manager; с 01.10.2026 платные
>   и ответы в 24-часовом окне (бриф 5.2, **И**). Тарифов Meta покупателю не называй: тариф для +995
>   неизвестен (**П**). Номер из приложения WhatsApp Business — только через партнёра Meta, это
>   отдельная оценка (`offers.en.md`).

### 4.4 Gig extras

| Extra | Текст для Fiverr | Цена | + дней | Решение |
|---|---|---|---|---|
| Additional revision (встроенная опция) | — | $40 | +1 | Публиковать. Один круг правок ≈ 1 час по ставке $40 из `offers.en.md`. |
| Extra hour of work (своя опция) | Title: `One extra hour of setup work` · Description: `More services, staff hours, FAQ entries or texts within your package.` | $40 | +1 | Публиковать. Только правки в рамках пакета; новый канал или мастер сверх лимита — это следующий пакет. |
| Extra fast delivery | — | — | — | Не включать. Срок зависит от ответов покупателя и от Meta. |
| WhatsApp channel | — | — | — | Не делать опцией. WhatsApp — часть Premium (`offers.en.md`); отдельной цены за него в прайсе нет, а одобрение Meta не укладывается в фиксированный срок опции. Покупателю Standard с WhatsApp — Premium или custom offer по оценке. |
| Extra language | — | — | — | Не сейчас. В коде только `en` и `ru` (`SUPPORTED_LANGUAGES`). Грузинский или другой язык — новый набор текстов и вычитка носителем; продаётся custom offer по оценке. Опцией — только когда набор текстов в коде, цену решаешь ты. |
| Monthly support | — | — | — | Не опция: опция разовая, а поддержка идёт после закрытия заказа. Подписка или custom offer — раздел 7. |

### 4.5 FAQ

> **Оператору.** Каждый ответ короче 300 знаков (лимит FAQ не проверен, **НП**).

- **Do my customers need to install anything?** No. They use Telegram and start the bot from your link or QR code. You get both for your Instagram, your Google Maps profile and your front desk.
- **Can the bot message a customer first?** No. A Telegram bot can only reply to people who started it. That's why booking starts from your link. After that, reminders work.
- **Will this stop no-shows?** No tool can promise that. Reminders 24 h and 2 h before, plus a one-tap cancel, mean a customer who can't come frees the slot instead of just not showing up.
- **What does the AI see, and can it book on its own?** In Standard and Premium, the AI gets the customer's message and your services, FAQ and hours, not the name and phone from the booking. It can't book or cancel: only the customer's tap on "Confirm" does. Basic uses no AI model.
- **What happens after delivery?** From launch, a monthly support plan here on Fiverr keeps the bot hosted and up to date: Care $49, Pro $149 or Business $349. Free fixes for 14 days (Premium: 30) come on top. Prefer your own server? Ask me for a quote.
- **WhatsApp, Instagram or Messenger?** Not in these packages. Message me before ordering, and I'll tell you what is possible for your business.

После выхода адаптера WhatsApp замени последний вопрос:

- **Can the bot work on WhatsApp?** Yes, in Premium. It runs on your own WhatsApp Business account through Meta's Cloud API. Meta bills message fees to your card; I add no margin. Reminders use templates Meta must approve. Instagram and Messenger aren't included.

### 4.6 Requirements

> **Оператору.** Это вопросы формы требований заказа. Тип поля и обязательность выставляешь в форме;
> набор типов (текст, выбор, файл) — по памяти, **НП**. Fiverr запускает срок, когда покупатель
> ответил, поэтому всё, без чего не начать, — обязательное. Уведомления владельцу в booking идут
> через того же бота, отдельный бот-уведомитель не нужен.

| # | Question to the buyer | Тип | Обязательно |
|---|---|---|---|
| 1 | Your business name, address and time zone. Which languages do your customers write in: English, Russian or both? | текст | да |
| 2 | Your services: name, duration and price. If you have several staff, who does which service? A list or a spreadsheet file is fine. | текст или файл | да |
| 3 | Opening hours for each day, breaks, days off and holidays. Staff hours, if they differ. | текст | да |
| 4 | Booking rules: how far ahead customers can book, the minimum notice, your cancellation cut-off (for example "up to 2 hours before") and reminder times (default: 24 h and 2 h before). | текст | да |
| 5 | Standard and Premium: 5–10 common questions with your answers (address, parking, payment, how to prepare). The bot answers only from these. | текст | нет |
| 6 | Who should get booking messages: you, your staff, a staff group chat? For each person, their numeric Telegram ID: in Telegram, search for "userinfobot", press Start and copy the number it shows. | текст | да |
| 7 | Create the bot on your own Telegram account: search for "BotFather", send /newbot, choose a name and a username ending in "bot". BotFather replies with a token. Paste it here. You can revoke it in BotFather at any time. Never send your Telegram password or login codes. | текст | да |
| 8 | Standard and Premium: can you create an empty Google Sheet and share it, and your Google Calendar, with a service address I'll give you in a setup file? | выбор: Yes / No / Not sure | нет |
| 9 | Premium, WhatsApp: do you have a Meta Business account, a number for the bot and a card to add in WhatsApp Manager? Is that number in the WhatsApp Business app now? | текст | нет |
| 10 | Do you already use a booking system or a calendar the bot must respect? | текст | нет |

> **Оператору.**
> - Вопрос 8 — «после Calendar»; до неё убери слова «and your Google Calendar». Вопрос 9 — только
>   после выхода адаптера WhatsApp.
> - Адрес сервисного аккаунта отправляй файлом настройки (PDF) на странице заказа, а не строкой в
>   чате. В файле — только шаги и этот адрес, никаких твоих контактов. Подпиши его как «a service
>   address, not a mailbox: messages sent to it are not read».
> - Токен из ответа на вопрос 7 сразу переноси в `.env` клиента (`BOOKING_BOT_TOKEN`). Ответ на
>   требования остаётся на странице заказа: это отзываемый токен, а не пароль, и при отказе от
>   поддержки покупатель отзывает его в BotFather.
> - На вопросе 6 покупатель часто путает ID и @username. Проверь до начала: ID — только цифры.
>   Владелец и каждый сотрудник должны один раз нажать `/start` в боте (`docs/packages/booking.md`).

### 4.7 Gallery: images and video

> **Оператору.** Размер картинки 1280 × 769 px (**НП**). Везде: подпись «Demo data, fictional
> business», никаких контактов, @username бота, URL таблиц, логотипов Telegram, WhatsApp и Google —
> только слова. Демо-бизнес — «Demo Barbers» из `examples/booking/client.toml`.

**Images:**

1. **Cover.** Слева крупно: «Bookings 24/7. Reminders before every visit.» Ниже мелко: «Telegram
   booking bot · Demo data». Справа — скриншот телефона: сетка времени и ответ «Booked ✅». Не больше
   8 слов крупным шрифтом: обложку смотрят в выдаче размером с марку.
2. **How it works.** Четыре шага слева направо: Service → Staff → Time → Confirm. Под ними две
   плашки: «Reminder 24 h and 2 h before» и «You get a message for every booking».
3. **What you see.** Три скриншота: уведомление владельцу о записи, ответ на `/today`, вкладка
   `Bookings` в Google Sheets с вымышленными именами. Подпись: «Plus a monthly report».

**Video (≤ 60 s, без голоса, с английскими субтитрами):**

| Время | В кадре | Субтитр |
|---|---|---|
| 0–5 s | Обложка | A booking bot for your business. Demo data, fictional barbershop. |
| 5–20 s | `/start` → услуга → мастер → день → время → имя и телефон кнопкой → «Confirm» → «Booked ✅» | Booking with buttons. Only free times are shown. |
| 20–32 s | Покупатель пишет «haircut tomorrow after 5?», бот предлагает свободные слоты кнопками, нажатие, «Confirm» | Standard and Premium: AI reads the message. Code checks the time is free. |
| 32–42 s | Напоминание с кнопкой «change or cancel», отмена, слот снова свободен | Reminders 24 h and 2 h before. One tap to cancel frees the slot. |
| 42–52 s | Уведомление владельцу, затем `/today` | You get every booking. Your day in one tap. |
| 52–60 s | Вкладка `Bookings`, страница месячного отчёта, финальная карточка | Basic · Standard · Premium. Order or message me here on Fiverr. |

> **Оператору.** Снимать на тестовом боте с демо-конфигом (или `uv run autostudio booking chat
> examples/booking/client.toml --language en` для записи терминала). Для кадра с напоминанием
> поставь тестовую запись на ближайшее время и дождись напоминания. Перед записью замени
> `contact_phone` в локальной копии конфига на пустой или замажь его в кадре. Отчёт:
> `uv run autostudio report examples/booking/client.toml --month 2026-10 --format html`.

---

## 5. Gig 2: Competitor price monitor

### 5.1 Title, category, tags

**Title**, 70 знаков:

```text
I will monitor competitor prices in Google Sheets with Telegram alerts
```

**Category** (**НП**): Data → Data Collection → Web Scraping (или подкатегория «Data Scraping»,
как она названа в списке). Запасной вариант: Programming & Tech → Software Development → Scripting
& Automation.

**Search tags:**

```text
price monitoring
competitor prices
price tracker
web scraping
google sheets
```

> **Оператору.**
> - Тег «web scraping» приводит и покупателей, которые хотят обхода защиты. Отказывай им в чате до
>   заказа (раздел 1). Средний заказ на сбор данных на Fiverr ≈ $106 (бриф 4.1, **НП**): деньги
>   здесь в поддержке, а не в настройке.
> - Состав пакетов — по `offers.en.md` (сайты 3/10/20, ссылки 200/1 000/2 000), как и в `upwork.md`.
>   В таблице тарифов `docs/packages/prices.md` стоят 20/100/300+ ссылок: `offers.en.md` здесь
>   главнее, расхождение записано в `offers.md`, 5.5.
> - Premium «до раза в час» — проверь ёмкость до заказа: пауза 5 с на сайт, это не больше ≈ 720
>   страниц в час с одного сайта, а при длине прохода по умолчанию (`max_run_minutes` = 30) — ≈ 360
>   ссылок с сайта и ≈ 1 440 всего. Больше — подними `max_run_minutes` и `TimeoutStartSec`
>   (`offers.md`, 5.1). Premium со ступенью модели ежечасно — до 14 400 операций в месяц,
>   больше лимита Pro: нужен Business, меньше страниц на модель или реже проверки (`offers.md`, 10.3).
>   Поэтому в описании просьба написать до заказа.

### 5.2 Description

> **Оператору.** 1 173 знака с переносами строк.

```text
Stop checking competitor prices by hand in dozens of tabs. The monitor checks their product pages on a schedule and keeps one Google Sheet: your price, their price, the difference in %, stock, and the time of the check.

You get a short Telegram message only when something changed: a price moved, a competitor went below you, an item went out of stock or came back.

Why you can trust the numbers:
• Prices are read from the page's own product data first
• In Premium, AI reads pages without that data. A price counts only if the AI's quote is found on the page and gives the same number
• A page that can't be read shows the last known price, marked as such. Never a made-up number
• A History tab keeps every new price with the page text it was read from

Polite by design: public pages only, robots.txt rules followed, a pause between requests to each site. No logins, no CAPTCHA bypass, no proxies to hide.

Before you order, send me up to 3 competitor links here. I'll tell you for free whether the price can be read.

From launch, a monthly support plan ordered here on Fiverr keeps it hosted and repairs it when sites change. Free fixes (14 or 30 days) come on top.
```

### 5.3 Packages

| | Basic | Standard | Premium |
|---|---|---|---|
| **Name** | `Daily: 3 sites` | `Every 6 hours: 10 sites` | `Hourly with AI: 20 sites` |
| **Description** | `Up to 3 sites and 200 product links, checked once a day. Sheet, history, daily Telegram digest.` | `Up to 10 sites and 1,000 links, checked every 6 hours. Alert after each check with changes.` | `Up to 20 sites, 2,000 links, up to hourly within site limits. AI reads pages without price data.` |
| **Price** | $120 | $300 | $700 |
| **Delivery** | 5 days | 7 days | 14 days |
| **Revisions** | 2 | 2 | 2 |
| Competitor sites | up to 3 | up to 10 | up to 20 |
| Links (one product on one competitor site) | up to 200 | up to 1,000 | up to 2,000 |
| Checks | once a day | every 6 hours | up to once an hour |
| Tabs: Prices now, History with the page quote, Changes | ✓ | ✓ | ✓ |
| Telegram message, only when something changed | once a day | after each check | after each check |
| Last known price, marked, when a page can't be read | ✓ | ✓ | ✓ |
| AI reads pages without price markup, quote-checked | — | — | ✓, up to 20 pages per check |
| Monthly report | ✓ | ✓ | ✓ |
| Free fixes after launch | 14 days | 14 days | 30 days |

### 5.4 Gig extras

| Extra | Текст для Fiverr | Цена | + дней | Решение |
|---|---|---|---|---|
| Additional revision | — | $40 | +1 | Публиковать. |
| Extra hour of work | Title: `One extra hour of setup work` · Description: `Add or replace competitor links, change alert rules or sheet layout within your package.` | $40 | +1 | Публиковать. Больше сайтов или ссылок, чем в пакете, — следующий пакет, а не часы. |
| Free link check before ordering | — | $0 | — | Не опция: это сообщение до заказа (`autostudio prices probe <url>` по 3 ссылкам). |
| JavaScript sites, product matching, dashboards | — | — | — | Не опция: доработка по оценке (`offers.en.md`, Custom work), custom offer. |
| Extra language | — | — | — | Не нужна: таблица и сообщения — английский или русский, это настройка. |
| Monthly support | — | — | — | Раздел 7. |

### 5.5 FAQ

- **Can you monitor Amazon or other big marketplaces?** Many large marketplaces forbid automated collection in their terms. I check each site's terms and robots.txt before adding it, and I skip the ones that forbid it.
- **Can it find the matching products for me?** In these packages, you give me the links, in the exact variant (color, size, volume). Automatic product matching is custom work, quoted separately.
- **What if a competitor redesigns their site?** The monitor alerts me, not you. Until it's fixed, the sheet shows the last known price, marked as such. Repairs are part of monthly support.
- **Will competitors know it's me?** Not from the monitor. It identifies itself with its own name and my contact, so a site owner can reach me. Your name is never in the requests. If a site owner asks me to stop, I stop the same day.
- **What about prices behind a login, or drawn by JavaScript?** Prices behind a login, paywall or CAPTCHA are never collected. Prices that appear only after JavaScript runs in a browser are custom work. I check your links before you order.
- **Does it use AI?** Basic and Standard use no AI model: prices come from the page's product data or a tested selector. Premium uses an AI model (OpenAI or Anthropic) only for pages without that data, and keeps a price only if the AI's quote is on the page.

### 5.6 Requirements

| # | Question to the buyer | Тип | Обязательно |
|---|---|---|---|
| 1 | Your products: ID, name, your price and currency. A spreadsheet file is best. | файл или текст | да |
| 2 | For each product, links to the same product on competitor sites, in the exact variant (color, size, volume). The same file is fine. | файл или текст | да |
| 3 | How often to check (within your package), the price change that should alert you (default 3%), the language of the sheet (English or Russian) and your time zone. | текст | да |
| 4 | Alerts come from a Telegram bot you own. Search for "BotFather", send /newbot, paste the token here. Then add your numeric Telegram ID: search for "userinfobot", press Start, copy the number. You can revoke the token at any time. | текст | да |
| 5 | Can you create an empty Google Sheet and share it with a service address I'll give you in a setup file? | выбор: Yes / No / Not sure | да |
| 6 | Please confirm: these pages are public (no login), and as far as you know the sites' terms don't forbid monitoring them. I also check robots.txt and the terms, and skip sites that forbid it. | выбор: I confirm / Let's discuss | да |

> **Оператору.** Бот-уведомитель — собственный бот покупателя: так ты не отправляешь ему ссылку на
> своего бота, а токен идёт в `AUTOSTUDIO_NOTIFY_BOT_TOKEN` клиента. После ответов — `prices check`,
> затем `prices probe` по каждому новому сайту (`docs/packages/prices.md`). Сайт не читается — сразу
> скажи покупателю и предложи замену ссылок. Молча пропускать сайт нельзя.

### 5.7 Gallery: images and video

1. **Cover.** «Competitor prices in your sheet. Alerts only when they change.» Скриншот вкладки «Prices
   now» с колонками ваша цена, их цена, разница в %. Подпись «Demo data».
2. **The alert.** Сообщение-дайджест из `docs/packages/prices.md` («Price changes / Cheaper than you /
   Availability»). Строку «Full table: https://docs.google.com/…» обрежь: в кадре не должно быть URL.
3. **Why trust the numbers.** Колонка Source («schema.org data», «AI, verified quote», «skipped: the
   site asks robots…»), строка History с цитатой, пометка «last known price».

**Video (≤ 60 s):**

| Время | В кадре | Субтитр |
|---|---|---|
| 0–6 s | Обложка | Competitor prices, checked for you. Demo data, fictional shop. |
| 6–20 s | Вкладка «Prices now»: ваша цена, цены трёх магазинов, разница | One sheet: your price, theirs, the difference. |
| 20–32 s | Сообщение в Telegram после второго дня демо | A message only when something changed. |
| 32–44 s | Вкладка History: цена и цитата со страницы | Every price shows the page text it came from. |
| 44–54 s | Строка «skipped: robots.txt» и «last known price» | Public pages only. No guesses when a page fails. |
| 54–60 s | Финальная карточка | Send me 3 links here. I'll check them for free. |

> **Оператору.** Источник кадров: `uv run autostudio prices demo` (три магазина, два дня, без сети) и
> `uv run autostudio prices probe` по демо-странице. Демо-магазин «Tech Corner Tbilisi» перед записью
> найди в Google Maps: если такой магазин есть, переименуй его в локальной копии
> `examples/prices/client.toml` без коммита (`upwork.md`, 2.6).

---

## 6. Gig 3: Inbox and invoices

### 6.1 Title, category, tags

**Title** (после выхода извлечения из PDF-счетов), 76 знаков:

```text
I will automate your inbox with AI: sort emails, log leads, extract invoices
```

**Title** (до PDF-счетов), 73 знака:

```text
I will automate your inbox with AI: sort emails, log leads, draft replies
```

**Category** (**НП**): Programming & Tech → AI Development → AI Integrations. Запасной вариант:
Programming & Tech → AI Development → AI Agents.

**Search tags:**

```text
email automation
ai automation
email parsing
gmail automation
google sheets
```

> **Оператору.**
> - После выхода PDF-счетов замени тег «email parsing» на «invoice extraction».
> - Это главный гиг (бриф 4.1). Пакет без модели не работает: просьбу «без ИИ» встречай отказом до
>   заказа (раздел 1).
> - Outlook и Microsoft 365 в v1 — только через автопересылку на отдельный ящик, черновики появятся
>   там (`offers.en.md`), и только если админ M365 разрешает пересылку вовне (часто запрещено по
>   умолчанию, НП; `offers.md`, раздел 1, пункт 9). Говори это до заказа и проверь до заказа.
> - Пробный разбор 20 писем до заказа на Fiverr не делаем: для него нужны пароль приложения и
>   подписанный DPA. Здесь это шаг заказа после ответа «Yes» на вопрос 9 и передачи пароля на
>   созвоне (`autostudio inbox dry-run … --limit 20`, раздел 6.6).

### 6.2 Description

> **Оператору.** 1 130 знаков с переносами строк при лимите 1 200 (**НП**). Это вариант до выхода
> извлечения из PDF. После выхода сделай три правки, иначе текст не влезет (выйдет 1 199 знаков):
> - после строки «• A hot lead pings you…» добавь строку
>   `• PDF invoices: supplier, number, date, total and currency go to an Invoices tab (Standard, Premium)`;
> - абзац «Nothing is sent without you. …» замени на «Nothing is sent without you: it reads, files
>   and drafts, and never sends or deletes email.»;
> - из последней строки убери «Hosting: see the FAQ.».

```text
Leads, invoices and questions all land in one inbox. Some get a late answer, some get none. I set up an assistant that sorts each new email for you.

Every 10–15 minutes:
• Each new email gets a category (lead, support, invoice, newsletter or your own) and an urgency
• Key details go to a Google Sheet: name, company, phone, request, budget
• A reply draft in the sender's language waits in your Drafts folder (Standard, Premium)
• A hot lead pings you on Telegram (Standard, Premium)

Nothing is sent without you. The assistant reads, files and drafts. It never sends or deletes email.

AI suggests, code checks. It uses AI models from OpenAI or Anthropic. A phone number or budget that isn't in the email is dropped. An email that tries to give orders to the AI is flagged, and nothing is done with it.

Proof of approach: I built a bank statement extractor that checks every period against the totals printed on the statement. If they don't match to the cent, it flags the gap instead of passing it.

Works with Gmail, Google Workspace (your admin may need to allow access), Zoho and most IMAP mailboxes. Hosting: see the FAQ.
```

> **Оператору.** Абзац «Proof of approach» — правда только в таком виде: это отдельный проект, а не
> часть inbox. Не пиши, что он работает с грузинскими банками, с мультивалютными выписками или
> кредитными картами (`offers.md`, 3.4). Цифры замера — только в PDF галереи (6.7), и только из
> README проекта выписок.

### 6.3 Packages

| | Basic | Standard | Premium |
|---|---|---|---|
| **Name** | `Sort and log` | `Sort, draft, invoices` · до PDF: `Sort, draft, alert` | `3 mailboxes and one add-on` |
| **Description** | `1 mailbox, 5 categories, key details to Google Sheets, newsletters filtered without AI.` | `Up to 8 categories, reply drafts never sent on their own, hot-lead alerts, PDF invoice fields.` · до PDF: `Up to 8 of your categories, reply drafts never sent on their own, hot-lead alerts on Telegram.` | `Up to 3 mailboxes, unlimited categories, 1 custom add-on up to 6 hours, 30 days of fixes.` |
| **Price** | $150 | $400 | $900 |
| **Delivery** | 5 days | 9 days | 17 days |
| **Revisions** | 2 | 2 | 2 |
| Mailboxes | 1 | 1 | up to 3, each with its own sheet and report |
| Categories | 5 standard: lead, support, invoice, marketing, other | up to 8 of your own | as many as you need |
| Urgency, sheet row, phone and budget only if in the email | ✓ | ✓ | ✓ |
| Newsletters and auto-replies filtered without AI; copies to folders or labels | ✓ | ✓ | ✓ |
| Emails that try to give orders to the AI are flagged | ✓ | ✓ | ✓ |
| Reply drafts in Drafts, never sent automatically | — | ✓ | ✓ |
| Hot-lead alerts in Telegram, or a webhook to Slack or Make | — | ✓ | ✓ |
| PDF invoices to an Invoices tab (text PDFs only) | — | ✓ (после PDF) | ✓ (после PDF) |
| One custom addition, up to 6 hours | — | — | ✓ |
| Monthly report | ✓ | ✓ | ✓ |
| Free fixes after launch | 14 days | 14 days | 30 days |

> **Оператору.** Доработка в Premium — одна на выбор из `offers.en.md`: свои поля извлечения, лид в
> CRM, Zapier или Make вебхуком, или данные счетов в CSV для бухгалтера. В v1 их нет: договорись о
> выборе в чате до заказа и делай как общую функцию пакета через конфиг (`offers.md`, 3.1).

### 6.4 Gig extras

| Extra | Текст для Fiverr | Цена | + дней | Решение |
|---|---|---|---|---|
| Additional revision | — | $40 | +1 | Публиковать. |
| Extra hour of work | Title: `One extra hour of setup work` · Description: `Adjust categories, facts for drafts, folders or alert rules within your package.` | $40 | +1 | Публиковать. Второй ящик в Basic или Standard — это Premium, а не часы. |
| QuickBooks, Xero, HubSpot; scans; Outlook directly; bank statements | — | — | — | Не опция: доработка по оценке, custom offer (`offers.en.md`, Custom work). Выписки — только после теста на образцах покупателя. |
| Extra language | — | — | — | Не нужна: черновики пишутся на языке отправителя; таблица и сообщения — английский или русский. |
| Monthly support | — | — | — | Раздел 7. |

### 6.5 FAQ

- **Will it send emails on its own?** No. Drafts only, in your Drafts folder. You read, edit and send. There is no code in it that sends or deletes email.
- **Does my email go to an AI company? Can it work without AI?** The sender, subject and text of each email go to the AI provider, OpenAI or Anthropic, through their API. Newsletters and auto-replies don't. Sorting needs AI, so there is no AI-free version of this service.
- **What do you store?** Not your emails. Only what's needed to avoid doing anything twice: message IDs, category and actions taken. Extracted fields are deleted once they are in your sheet. Records older than 90 days are removed.
- **Which mailboxes work?** Gmail, Google Workspace, Zoho and most IMAP mailboxes. Your Workspace admin may need to allow access. Outlook and Microsoft 365 can work through forwarding to a separate mailbox, if your admin allows forwarding outside your company; drafts then appear there. I check this before you order.
- **Can it read invoices, or export to QuickBooks or Xero?** Invoice emails are sorted and filed. Reading invoice PDFs, scans and photos, and exports to QuickBooks, Xero or HubSpot are custom work. I quote them after I see a sample.
- **What happens after delivery?** From launch, a monthly support plan here on Fiverr keeps it hosted and up to date: Care $49, Pro $149 or Business $349. Free fixes for 14 days (Premium: 30) come on top. Prefer your own server? Ask me for a quote.

После выхода извлечения из PDF замени пятый вопрос:

- **Scanned invoices, QuickBooks or Xero?** Invoice PDFs must contain text. Scans and photos, and exports to QuickBooks, Xero or HubSpot, are custom work. I quote them after I see a sample.

> **Оператору.** Когда извлечение из PDF выйдет, проверь, что именно уходит модели (текст PDF или
> нет), и допиши это во второй ответ. Сейчас ответ верен для v1: вложения модели не передаются,
> только их имена (`docs/packages/inbox.md`).

### 6.6 Requirements

| # | Question to the buyer | Тип | Обязательно |
|---|---|---|---|
| 1 | Which mailbox should I connect? The provider (Gmail, Google Workspace, Zoho, other) and the mailbox address. Is there an admin for it? | текст | да |
| 2 | Please don't paste any password here or in chat. Once the order starts, I'll send you the steps to create an app password for this mailbox (not your main password), and you'll pass it to me on a short Fiverr call. You can revoke it at any time, and access stops at once. Does your admin block app passwords? | выбор: No / Yes, ask my admin / Not sure | да |
| 3 | What kinds of emails do you get, and what should happen with each: draft a reply, alert you, move to a folder? | текст | да |
| 4 | Standard and Premium: the facts a reply draft may use: prices, hours, how to book or order, cancellation and refund policy, address. Drafts won't promise anything that isn't here. | текст | нет |
| 5 | How should we tune the sorting: you attach 20–50 typical emails (remove anything sensitive), or, after you accept the data processing terms (question 9), I sort your last 20 emails without changing anything and we review them together? | выбор: Example emails / Dry run | да |
| 6 | The language of the sheet and alerts (English or Russian). Should email addresses, names and phone numbers be masked in the sheet? The subject and the one-line summary are not masked. | выбор | да |
| 7 | Standard and Premium, alerts: a Telegram bot you own (search "BotFather", send /newbot, paste the token) plus your numeric Telegram ID (from "userinfobot"). Or paste a Slack or Make webhook URL instead. | текст | нет |
| 8 | Can you create an empty Google Sheet and share it with a service address I'll give you in a setup file? | выбор: Yes / No / Not sure | да |
| 9 | Before I connect the mailbox, I'll attach the data processing terms: what is processed, where, for how long, and which AI provider receives it. Do you agree to review and accept them first? | выбор: Yes / I have questions | да |

> **Оператору.**
> - Ответ «Yes, ask my admin» на вопрос 2 означает Google Workspace с запретом паролей приложений. Варианты из
>   `docs/packages/inbox.md`: админ разрешает пароли приложений или автопересылка на отдельный ящик.
>   Когда выйдет вход через XOAUTH2, пароль для Workspace не нужен вовсе: вопрос 2 тогда допиши
>   вариантом «your admin grants access, no password».
> - **Как передаётся пароль приложения.** Не через форму требований и не в чате: ответ на требования
>   остаётся на странице заказа, а договор (12.3) обещает, что паролей в сообщениях нет. Пока
>   поддержка Fiverr письменно не ответила на вопрос из 2.1 (п. 1), — только на созвоне Fiverr:
>   покупатель диктует пароль, ты сразу кладёшь его в `.env` (`INBOX_IMAP_PASSWORD`); в лог и тексты
>   ошибок он не попадает. Есть ли у тебя встроенные созвоны Fiverr — раздел 9. Если поддержка
>   разрешит форму или одноразовую ссылку — можно так, но после настройки попроси покупателя
>   отозвать этот пароль и выпустить новый, переданный на созвоне. При отказе от поддержки
>   покупатель отзывает пароль.
> - Вопрос 9: DPA — из `docs/legal/` (черновик, покажи юристу). В версии для Fiverr не должно быть
>   твоих e-mail, телефона и адреса как контакта. Достаточно ли согласия кнопкой в форме и сообщением
>   в заказе — вопрос юристу (вопрос в конце ответа).
> - Не начинай разбор писем, пока покупатель не ответил «Yes» на вопрос 9 и не подтвердил условия в
>   заказе.

### 6.7 Gallery: images, PDF and video

1. **Cover.** «Every email sorted. Nothing sent without you.» Скриншот таблицы Inbox с категориями и
   замаскированными отправителями. Подпись «Demo data».
2. **The flow.** Email → category and urgency → sheet row → draft in Drafts → hot-lead alert. Плашка
   «AI suggests, code checks».
3. **The checks.** Три строки из демо: «dropped unverified: phone», «BLOCKED: injection, operator
   alerted», «second run: nothing done twice».
4. **PDF (если галерея принимает PDF, НП): «A bank statement extractor that checks its own
   numbers».** Одна страница: скриншот заключения веб-стенда, вырезка страницы с рамкой вокруг
   строки, таблица точности, где имена файлов заменены на «Statement 1–4». Только синтетическая
   выписка из `tests/conftest.py` проекта выписок, ни одной цифры реальных клиентов банков. Текст:

```text
A bank statement extractor that checks its own numbers. Every statement prints its own check: opening balance + deposits − withdrawals = closing balance. A period counts as correct only if the extracted transactions reconcile to the cent. Otherwise it is flagged with the difference and a likely cause. Every number keeps its page; numbers read from text or OCR also keep their exact position, and a corrected number shows the original reading. Result on 4 real statements from different banks (13 periods): all 13 reconciled, 2,258 transactions, $0.69 in AI costs for the whole run; without the AI step, 5 of 13. A small sample, not a benchmark. Screens show a synthetic statement.
```

> **Оператору.** Цифры — из README проекта выписок (прогон с `gpt-5.4-mini,gpt-5.6-sol`). Перезапустил
> замер и цифры изменились — поправь текст. Без права показывать проект (2.1, п. 7) этот PDF не
> публикуй.

**Video (≤ 60 s):**

| Время | В кадре | Субтитр |
|---|---|---|
| 0–5 s | Обложка | Ten emails to a tour agency. Demo data, fictional business. |
| 5–18 s | Сводка демо: 10 писем → категория, срочность, действия | Every email sorted: lead, support, invoice, newsletter. |
| 18–28 s | Таблица Inbox в Google Sheets, отправители замаскированы | Key details in your sheet. |
| 28–38 s | Черновик ответа в почтовой программе, та же цепочка, язык отправителя | Reply drafts in your Drafts folder. Never sent automatically. |
| 38–46 s | Сообщение о горячем лиде в Telegram | A hot lead pings you. |
| 46–54 s | «BLOCKED: injection» и «dropped unverified: phone» | An email that gives orders to the AI is blocked. A phone that isn't in the email is dropped. |
| 54–60 s | Второй запуск: «nothing done twice», финальная карточка | Order or message me here on Fiverr. |

> **Оператору.** Источник кадров: `uv run autostudio inbox demo --out var/demo/inbox`. В демо модель
> заменена заглушкой, и «вспомненный» телефон подсказан ей нарочно — так показана проверка; не
> называй это реальной ошибкой модели (`upwork.md`, 2.6). Кадр с вкладкой Invoices добавь, когда
> выйдет извлечение из PDF.

---

## 7. Monthly support on Fiverr

> **Оператору.** Хостинг входит только в тариф поддержки (`offers.en.md`, Support plans), и тариф
> начинается в день запуска; бесплатные исправления (14 / 30 дней) идут поверх него, а не вместо
> (`offers.md`, 7.4; договор 7.2). Бесплатного хостинга на период исправлений нет. Клиент с
> Fiverr платит за поддержку только через Fiverr (бриф 3.2, **НП**). Gig extra для этого не подходит:
> опция оплачивается один раз в заказе, а поддержка идёт месяцами после его закрытия.

### 7.1 Plans (same as `offers.en.md`)

| | Care | Pro | Business |
|---|---|---|---|
| Per month (now) | $49 | $149 | $349 |
| Hosting, uptime monitoring, daily backups, monthly security updates, monthly report | ✓ | ✓ | ✓ |
| I reply within | 2 business days | 1 business day | 4 business hours |
| Hours for changes | 1 hour | 4 hours | 10 hours |
| Repairs after outside changes | counts toward your 1 hour | included | included |
| AI operations per month | 2,000 | 10,000 | 30,000 |
| One new small automation per quarter, up to 6 hours | — | — | ✓ |

Рабочее время: пн–пт, 10:00–19:00 по Тбилиси (UTC+4), кроме праздников Грузии. Сверх часов — $40 в
час, шагом 30 минут, только после согласования оценки: на Fiverr это отдельный custom offer.
ИИ-операции — месячный потолок: на нём ИИ встаёт до 1-го числа, платы сверх лимита нет
(`offers.md`, 7.2). Покупателю: «AI operations are a monthly cap, with no charges above it.»

### 7.2 Three ways to bill it, in order of preference

| | A. Подписка (Subscription custom offer) | B. Custom offer на каждый месяц | C. Отдельный гиг поддержки с подпиской |
|---|---|---|---|
| Когда | Если в кабинете при создании custom offer есть тип заказа «Subscription» | Если подписок нет: работает у всех | Только если подписки доступны и ты решил занять четвёртый слот |
| Как устроено | Из чата покупателя: Create an offer → гиг этого пакета → тип «Subscription» → помесячно на 3 или 6 месяцев → цена тарифа. Каждый месяц — самостоятельный заказ. | Каждый месяц новый offer «Monthly support: [package], [plan], [month]», срок 30 дней. | Гиг «Monthly support for your booking bot, price monitor or inbox assistant», пакеты Care / Pro / Business, подписка включена в гиге. |
| Что сдаёшь | В конце месяца: месячный отчёт PDF, список правок, использованные часы | то же | то же |
| На что смотреть | Подписки — только для подходящих продавцов и категорий; цена гига от $10; срок сдачи не больше 30 дней; при подписке через custom offer скидку дать нельзя (сниппет справки 20.10.2026, **НП**). Бриф называет и «повторяющийся custom offer до 6 месяцев» — возможно, это та же функция (**НП**). | За 3 дня до начала месяца отправь offer. Нет оплаченного заказа — нет работы. | Новые покупатели такой гиг почти не ищут: он для своих клиентов. Не раньше Level 1. |

> **Оператору.** Как сдавать месяц: `uv run autostudio report clients/<id>/<pkg>.toml --month ГГГГ-ММ
> --format html` → в браузере «Печать → PDF» → Deliver в заказе с коротким текстом: что работало, что
> сломалось и починено, сколько часов ушло. Заказ закроется сам через 3 дня (**НП**). В отчёте и PDF
> нет твоих контактов — проверь перед первой отправкой.

### 7.3 When to offer it

1. **В гиге** — с первого дня: FAQ «What happens after delivery?» и строка про тариф с запуска в
   описании. Покупатель знает до заказа, что системе нужен уход.
2. **За 2–3 дня до сдачи** — когда показал работу на данных покупателя (раздел 8, шаг 4):
   предложение (шаблон 7.4). Заказ поддержки должен начаться в день запуска.
3. **В первый месяц** — раз в неделю короткая заметка из журнала событий, только настоящие числа:
   «This week the bot made [N] bookings, [N] of them after hours, and sent [N] reminders». В конце
   месяца — первый отчёт (7.2).

### 7.4 Offer message

```text
[The bot / The monitor / The inbox assistant] goes live on [date]. From that day, a monthly support plan here on Fiverr keeps it hosted and up to date. Free fixes until [date] come on top of any plan.
• Care, $49/month: hosting, monitoring, backups, a monthly report, 1 hour of changes or repairs after outside changes; my own bugs are always fixed free. Reply within 2 business days.
• Pro, $149/month: up to 4 hours of changes, repairs when [sites / your setup] change, reply within 1 business day.
• Business, $349/month: up to 10 hours, reply within 4 business hours, one new small automation per quarter.
Which plan would you like? I'll send an offer here that starts on [date]. If you'd rather not continue, we agree on a date here, I export your data as CSV files, and I switch the system off. Prefer your own server? I'll quote the handover.
```

### 7.5 If the buyer says no

- Дата отключения — письменно в сообщениях заказа. Бот, таблица и WhatsApp-аккаунт и так
  принадлежат покупателю.
- По запросу — выгрузка записей или истории цен в CSV через заказ. Данные с сервера удаляются в
  течение 30 дней (`offers.en.md`, Cancelling).
- Покупатель отзывает токен бота или пароль приложения. Напомни об этом в последнем сообщении.
- Установка на сервер покупателя — отдельная оценка (`offers.md`, 11.4: вопрос о передаче кода
  открыт).

---

## 8. One order, start to finish

1. **До заказа.** Отвечай в течение нескольких часов. prices — `prices probe` по ссылкам покупателя;
   booking Premium — готовность Meta; inbox — провайдер почты (Outlook → автопересылка) и согласие
   на ИИ. Чего не можешь сделать — скажи до заказа: отказ лучше отмены.
2. **Заказ начался** (покупатель ответил на требования). В тот же день проверь ответы. Чего-то не
   хватает — спроси на странице заказа. Отправь файл настройки PDF: адрес сервисного аккаунта и шаги,
   без твоих контактов.
3. **Настройка** — по чек-листам `docs/packages/<pkg>.md`: `check`, затем `chat`, `dry-run` или
   `probe`.
4. **Показ на данных покупателя.** Скриншоты или видео в заказе; ссылка на бота самого покупателя.
   Правки — два круга. Здесь же — предложение поддержки (7.3, шаблон 7.4).
5. **Сдача (Deliver).** Короткое сообщение плюс PDF «Handover»: что работает и по какому расписанию,
   какие данные где хранятся, как отозвать доступы, как устроена поддержка. Без твоих контактов.
   Заказ поддержки начинается в день запуска; не оформлен — система не запускается, договорись о
   выгрузке и дате отключения (7.5).
6. **После закрытия.** Бесплатные исправления 14 или 30 дней — в сообщениях, без нового заказа,
   поверх тарифа поддержки.
7. **Отзыв.** Один раз попроси честный отзыв, без условий.

## 9. Check in your account before relying on it (НП)

| Что | Где проверить |
|---|---|
| Лимиты полей: заголовок 80, описание 1 200, 5 тегов (и 20 знаков на тег), название и описание пакета, ответ FAQ, вопрос требований, описание профиля | формы создания гига и профиля |
| Названия категорий и подкатегорий для трёх гигов; метаданные (платформа Telegram, ИИ) | шаг Overview при создании гига |
| Вопрос формы про использование ИИ в гиге, если он есть | шаг Overview или Description |
| Значения в списке сроков сдачи (есть ли 6, 9, 17 и 21 день) | шаг Pricing |
| Шаг цены (любые доллары или кратно $5) и максимум цены пакета для нового продавца | шаг Pricing |
| Сбор за маленький заказ для покупателя ($3–3,50 до $100–200) | оформление заказа как покупатель |
| Subscriptions и тип «Subscription» в custom offer; milestones в твоей категории | создание custom offer из чата |
| Типы вопросов требований (текст, выбор, файл) | шаг Requirements |
| Галерея: число картинок, размер, длина видео, разрешены ли PDF | шаг Gallery |
| Можно ли передать покупателю адрес сервисного аккаунта Google в заказе | письменный ответ поддержки (2.1, п. 1) |
| Допуск продавца с твоим гражданством | письменный ответ поддержки (бриф 3.3) |
| Способы и цена вывода для Грузии: PayPal, Payoneer, банк через Payoneer | Earnings → Withdraw |
| Срок до вывода (14 дней, 7 для Top Rated, Pro и Seller Plus) и автозакрытие заказа через 3 дня | справка «How Fiverr works for freelancers» |
| Продление срока заказа и правила отмены | страница заказа, справка |
| Встроенные созвоны в Fiverr: доступны ли тебе (через них передаётся пароль приложения почты, 6.6) | раздел сообщений |
| Можно ли получить пароль приложения почты через форму требований или одноразовой ссылкой | письменный ответ поддержки (2.1, п. 1) |
| Пороги Level 1 и условия Fiverr Ads | справка «freelancer levels» и «Fiverr Ads» |
| Меняется ли имя пользователя после регистрации | форма регистрации |
