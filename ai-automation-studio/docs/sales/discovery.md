# Discovery: опросник перед заказом и бланк заказа

> **Оператору.** Опросник — второй разговор после 15-минутного созвона (`outreach.md`, раздел 6),
> до предложения и счёта. Цель — понять, подходит ли пакет, какой нужен тариф и что клиент должен
> дать. И вовремя увидеть заказ, который брать нельзя.
>
> - Вопросы клиенту даны на английском и на русском, их можно задавать как есть: голосом, письмом
>   или формой. Остальное — инструкции для тебя.
> - Состав тарифов и цены — из `docs/sales/offers.en.md`. Разошлись — прав `offers.en.md`.
> - **П / И / НП** — статусы фактов из `docs/research/DECISION_BRIEF.md`. **НП** — не подтверждено.
> - 🔄 — функция дописывается. Не обещай её, пока она не влита и не описана в `docs/packages/<пакет>.md`.
> - `{{NAME}}` — твоё имя, заполни сам.

## 0. Как пользоваться

1. **Когда.** После первого созвона, если клиенту интересно. Занимает 20–30 минут. Можно отправить
   вопросы письмом, а созвон оставить на уточнения.
2. **Порядок.** Общий блок (раздел 1) → красные флаги (раздел 2) → блок пакета (разделы 3–5) →
   бланк заказа (раздел 6).
3. **Красный флаг — стоп сразу.** Не дожимай опросник до конца, если уже понятно, что заказ брать
   нельзя. Откажи вежливо (шаблоны в 2.2) и запиши причину в `notes`: это данные для гипотез брифа.
4. **Доступов здесь не берём.** Ни паролей, ни токенов, ни примеров писем до подписанного заказа и
   DPA. На площадках — только через формы и сообщения площадки (`upwork.md`, `fiverr.md`, раздел 1).
5. **Храним минимум.** Записываем данные бизнеса, а не его клиентов. Ответы — в папку клиента
   `clients/<id>/` (она в `.gitignore`) или в список продаж (`outreach.md`, раздел 3). Не стал
   клиентом — удаляем через 90 дней (решение `outreach.md`).
6. **Площадки.** Клиент с Upwork или Fiverr платит только через площадку, включая поддержку. Свои
   контакты ему не даём (НП, бриф 3.2).

## 1. Общий блок — для любого пакета

```text
EN:
1. What does your business do? Where is it registered, and where are most of your customers?
2. Who will use this every day, and who decides on the order?
3. Which languages do your customers write in?
4. What do you use today: email provider, calendar, spreadsheets, a CRM or booking software?
5. Will the system see any health, legal or payment details about people? For example diagnoses, case files, card numbers.
6. Do you have customers in the EU?
7. When do you need it running? Is there a deadline, such as a season or an opening?
8. Who on your side can create accounts and share access: a Google Sheet, a Telegram bot, mailbox settings?
9. What would make the first month a success for you? Please describe it in your own words.

RU:
1. Чем занимается ваш бизнес? Где он зарегистрирован и где большинство ваших клиентов?
2. Кто будет пользоваться этим каждый день и кто принимает решение о заказе?
3. На каких языках вам пишут клиенты?
4. Чем вы пользуетесь сейчас: почта, календарь, таблицы, CRM или программа записи?
5. Будут ли в системе сведения о здоровье, судебных делах или платежах людей? Например, диагнозы, материалы дел, номера карт.
6. Есть ли у вас клиенты в ЕС?
7. Когда это должно заработать? Есть ли срок: сезон, открытие?
8. Кто с вашей стороны может создать аккаунты и выдать доступ: Google-таблица, бот в Telegram, настройки почты?
9. Что для вас будет успехом через месяц? Опишите своими словами.
```

**Что записать:**

| Поле | Зачем |
|---|---|
| Страна регистрации бизнеса и страна клиентов | Россия и Беларусь — красный флаг (2.1, п. 1) |
| Языки клиентов | Бот записи говорит только `en` и `ru`; грузинский — доработка |
| Текущие инструменты | Совместимость: почтовый провайдер, второй календарь, CRM |
| Чувствительные данные | Здоровье, суды, карты — красный флаг или ограничение (2.1, п. 2) |
| Клиенты в ЕС | GDPR: DPA обязателен; холодные письма в ЕС не пишем (`outreach.md`, 0.3) |
| Кто решает, кто даёт доступы | Без человека с доступами срок не начнётся |
| Успех через месяц — словами клиента | Сверяем с месячным отчётом. Только то, что отчёт умеет показать. Выручку не обещаем |
| Канал: прямой, Upwork, Fiverr | Правила оплаты и контактов |

## 2. Красные флаги

### 2.1 Не берём заказ (или часть заказа)

| # | Флаг | Как это звучит | Почему нет | Решение |
|---|---|---|---|---|
| 1 | **Россия или Беларусь** | «Мы в Москве», «наши клиенты в основном в России» | России и Беларуси нет в списках стран Anthropic и OpenAI (НП); Telegram в России почти недоступен (НП); бриф, риск 15 | Бизнес в РФ или РБ — отказ во всём. Клиенты или аудитория там — отказ в пакетах с моделью: inbox, booking Standard и Premium, prices Premium |
| 2 | **Данные о здоровье** | «Бот пусть спросит симптомы», «сохраняйте диагноз», ящик клиники с письмами пациентов | Данные о здоровье — особая категория по закону Грузии о персональных данных (НП, бриф 5.9); бриф, риск 16 | booking: только имя, телефон, услуга и время, функцию с диагнозом не делаем. inbox: ящик с перепиской пациентов не берём |
| 3 | **Массовые рассылки** | «Разошлите акцию всей базе», «напишите всем, кто был в прошлом году», «загрузим номера, пусть бот всем напомнит» | WhatsApp требует согласия до сообщений бизнеса (П); бот Telegram не может написать первым (П); прямой маркетинг в Грузии требует согласия (НП) | Отказ. Напоминания — только тем, кто сам записался через бота |
| 4 | **Обход CAPTCHA, логина, антибота** | «Цены видны после входа», «они блокируют ботов — обойдёшь?», «возьми прокси» | Бриф 5.8; площадки запрещают заказы, нарушающие чужие правила (НП) | Сайт снимаем. Все сайты такие — отказ во всём заказе |
| 5 | **Сайты, где цену рисует JavaScript, или антибот-защита** | `probe` не находит цену ни на одной ступени, а в браузере она есть; «Checking your browser» | В v1 браузера нет (`docs/packages/prices.md`, «Ограничения v1») | Сайт исключаем из оценки до оплаты. JS без антибота — только доработка с отдельной оценкой. Антибот — никогда |
| 6 | **«ChatGPT в WhatsApp»** | «Пусть бот отвечает на любые вопросы», «как ChatGPT, только в нашем WhatsApp» | С 15.01.2026 Meta запрещает в WhatsApp ИИ-ассистентов общего назначения (П) | Отказ. Бот — только запись и FAQ этого бизнеса |
| 7 | **Автоотправка писем** | «Пусть ИИ сам отвечает клиентам» | Черновики никогда не уходят сами — правило пакета (AGENTS.md) | Отказ в этой части. Если это суть заказа — отказ во всём |
| 8 | **Чужой ящик** | «Подключите почту сотрудников, я хочу видеть, что они пишут», ящик, которым клиент не владеет | Нет права на эти данные | Отказ |
| 9 | **Основной пароль, коды 2FA, вход в аккаунт клиента** | «Вот мой пароль от Gmail» | Только отзываемый доступ: пароль приложения, свой токен бота, «Поделиться» таблицей (бриф 3.2, НП) | Не принимаем. Ждём пароль приложения. Пароль прислали в чат — удали сообщение у себя и попроси клиента сменить пароль |
| 10 | **Лид-листы, сбор персональных данных** | «Соберите почты всех салонов Тбилиси», «имена продавцов с маркетплейса» | GDPR и закон Грузии (НП, бриф 4.1) | Отказ |
| 11 | **Обман** | «Пусть бот говорит, что он администратор Нина», «напишите отзывы от клиентов» | Введение в заблуждение | Отказ |
| 12 | **Оплата мимо площадки** | Клиент с Upwork или Fiverr: «давайте напрямую, так дешевле» | Бан аккаунта (НП, бриф 3.2) | Отказ. Ответ: «I keep all work with clients from this platform on the platform» |
| 13 | **Нет своего аккаунта Meta для WhatsApp** | «Подключите на ваш номер», «через WhatsApp Web» | Номер и WABA должны быть у клиента (бриф 5.2). Неофициальные способы (WhatsApp Web, сторонние библиотеки) нарушают правила WhatsApp, номер могут заблокировать (НП) | Только Telegram (бриф, риск 6) |
| 14 | **Гарантия результата в договоре** | «Пропишем: неявок станет вдвое меньше», «рост выручки на 20%» | Это зависит от клиентов бизнеса (`offers.en.md`, «What I don't promise») | Пункт не подписываем. Настаивает — отказ |
| 15 | **Запрещённый вид бизнеса** | Ставки, оружие, товары для взрослых, рецептурные препараты и т. п. | Правила Meta и площадок (НП: список сверить с их правилами) | Отказ |
| 16 | **Отказ от DPA** | «Давайте без бумаг» для inbox или booking | Мы обрабатываем персональные данные по поручению клиента: письма, имена и телефоны покупателей (`docs/packages/inbox.md`, «Приватность»; бриф 5.9) | inbox — отказ. booking — DPA (`docs/legal/dpa.*.md` после проверки юристом), без него не запускаем. prices персональных данных не обрабатывает, но шаблон договора даёт DPA каждому клиенту (`docs/legal/README.md`) — уточни у юриста, нужен ли он для prices |

Не красный флаг, а **«не помещается»**: 1 000 ссылок на одном сайте каждый час, грузинский
интерфейс, Outlook напрямую, группы больше одного человека на слот. Это меньшая частота, доработка
с оценкой или другой тариф — скажи прямо.

### 2.2 Как отказать

Коротко, без лекции, с альтернативой, если она есть.

```text
EN (general): Thank you for explaining it in detail. I can't take this on: [one-line reason]. What I can do instead is [alternative]. If that's useful, I'm happy to continue.
EN (part of the job): I can do [the rest], but not [the part]: [one-line reason]. Shall I quote it without that part?
EN (site can't be read): I checked [site]. Its prices load only in a full browser / behind a check that blocks automated visits. I don't get around such checks, so I've left it out of the quote.
RU (общий): Спасибо, что подробно рассказали. Это я взять не могу: [причина одной строкой]. Вместо этого могу [альтернатива]. Если подходит — продолжим.
RU (часть заказа): [Остальное] сделаю, а [эту часть] — нет: [причина одной строкой]. Посчитать без неё?
RU (сайт не читается): Я проверил [сайт]. Цены там появляются только в полном браузере / за проверкой, которая блокирует автоматические заходы. Такие проверки я не обхожу, поэтому этот сайт в оценку не вошёл.
```

Причины одной строкой:
- «Our customers are in Russia» → *The AI providers I use don't serve that region, so I can't run AI features for it.* / «Провайдеры ИИ, с которыми я работаю, не обслуживают этот регион».
- «Store diagnoses» → *I don't store health details. The bot keeps only the name, phone, service and time.* / «Сведения о здоровье я не храню. Бот хранит только имя, телефон, услугу и время».
- «Send to the whole list» → *I only message people who asked for it, such as customers who booked through the bot.* / «Пишу только тем, кто сам об этом попросил».
- «Get around the block» → *I only read public pages, the way the site allows.* / «Читаю только публичные страницы так, как разрешает сайт».

## 3. booking: бот записи

### 3.1 Вопросы

```text
EN:
1. How do customers book today: phone, Instagram, WhatsApp, Telegram, your website, booking software? Roughly what share comes through each?
2. Do your customers use Telegram? Would they open a bot from a link or a QR code?
3. About how many bookings a week? How many arrive in the evening or when you're closed?
4. Your services: name, duration, price. Who does which service?
5. How many staff take bookings? Does each have their own schedule?
6. Opening hours, breaks, days off, holidays. One location or several?
7. Booking rules: how far ahead, the minimum notice, a break between customers, the latest time to cancel.
8. When should reminders go out? The default is 24 hours and 2 hours before.
9. About how many no-shows a week now? What is an average visit worth to you?
10. The 5–10 questions customers ask most often, with your answers.
11. Who should get a message about each booking: you, staff, a staff group chat?
12. Do you keep bookings anywhere else: another app, a paper book, a Google Calendar? Will the bot be the only place where bookings are made?
13. Do several people ever book the same slot, like a group class?
14. Do you need prepayment?
15. Clinics: what do you need to know before a visit? Only the name, phone, service and time are stored.
16. Standard and Premium: can you create an empty Google Sheet and share it with a service address I give you?
17. WhatsApp: do you have a Meta Business account? Which number would the bot use? Is it in the WhatsApp Business app now? Can you add a payment card in WhatsApp Manager? How many reminders a day would you send?

RU:
1. Как клиенты записываются сейчас: телефон, Instagram, WhatsApp, Telegram, сайт, программа записи? Примерно какая доля через каждый канал?
2. Пользуются ли ваши клиенты Telegram? Откроют ли бота по ссылке или QR-коду?
3. Сколько примерно записей в неделю? Сколько приходит вечером или когда вы закрыты?
4. Ваши услуги: название, длительность, цена. Кто какую услугу делает?
5. Сколько мастеров принимают записи? У каждого свой график?
6. Часы работы, перерывы, выходные, праздники. Одна точка или несколько?
7. Правила записи: на сколько дней вперёд, за сколько минимум, перерыв между клиентами, до какого времени можно отменить.
8. Когда напоминать? По умолчанию — за 24 часа и за 2 часа.
9. Сколько примерно неявок в неделю сейчас? Сколько для вас стоит средний визит?
10. 5–10 вопросов, которые задают чаще всего, и ваши ответы.
11. Кому сообщать о каждой записи: вам, мастерам, в общий чат сотрудников?
12. Ведёте ли вы записи где-то ещё: другое приложение, журнал, Google Calendar? Будет ли бот единственным местом записи?
13. Бывает ли, что на одно время записываются несколько человек, как на групповое занятие?
14. Нужна ли предоплата?
15. Для клиник: что вам нужно знать до визита? Храним только имя, телефон, услугу и время.
16. Standard и Premium: сможете создать пустую Google-таблицу и открыть доступ служебному адресу, который я пришлю?
17. WhatsApp: есть ли у вас бизнес-аккаунт Meta? Какой номер будет у бота? Он сейчас в приложении WhatsApp Business? Сможете привязать карту в WhatsApp Manager? Сколько напоминаний в день вы бы отправляли?
```

**Что значат ответы:**

| Вопрос | Ответ | Что делать |
|---|---|---|
| 1–2 | Клиенты почти не пишут в Telegram | Telegram-бот, скорее всего, не взлетит. Скажи это честно. WhatsApp — Premium 🔄; Instagram и Messenger — доработка. Для грузинских владельцев одного Telegram мало (бриф, раздел 8, НП) |
| 3, 9 | Числа клиента | Запиши как точку отсчёта. Доля записей «пока вы закрыты» — в месячном отчёте. Снижение неявок не обещаем |
| 5 | 1 / до 3 / до 10 мастеров | Basic / Standard / Premium (`offers.en.md`) |
| 6 | Несколько точек или часовых поясов | Каждая точка — отдельная настройка |
| 12 | Записи идут и в другую систему | Бот не видит другую систему: синхронизации с YCLIENTS, Altegio, Fresha нет. Видит ли бот занятое время в Google Calendar — 🔄, проверь после влития. Без этого записи по телефону владелец вносит через бота (`docs/packages/booking.md`), иначе будут двойные записи |
| 13 | Групповые занятия | Не поддерживается (вместимость слота — 1) |
| 14 | Нужна предоплата | Нет в v1. Доработка, условия BOG Payments — НП |
| 15 | Нужны симптомы, диагноз | Красный флаг 2 |
| 3 и язык | Пишут по-грузински | Грузинских текстов в боте нет: доработка с носителем |
| 17 | Номер в приложении WhatsApp Business | Либо номер переносится в бота и история чатов в приложении на этом номере пропадает, либо подключение через партнёра Meta за его плату — отдельная оценка (П, бриф 5.2) |
| 17 | Больше 250 разных людей в сутки получают напоминания | Новый бизнес-аккаунт пишет первым не больше чем 250 уникальным людям за 24 часа (П). Сначала верификация бизнеса в Meta |

WhatsApp в целом — 🔄: адаптер дописывается. Не называй сроки, пока он не влит
(`outreach.md`, 0.2).

### 3.2 Что собрать

- [ ] Таблица услуг: название `en`/`ru`, длительность в минутах, цена, валюта, кто делает.
- [ ] Мастера и их часы, если отличаются от часов салона.
- [ ] Часы работы, перерывы, выходные, праздники; часовой пояс.
- [ ] Правила: на сколько дней вперёд, минимум до записи, перерыв, срок отмены, время напоминаний.
- [ ] 5–10 вопросов FAQ с ответами (Standard, Premium) — так, как их задают клиенты: «Можно
      оплатить картой?», а не «Способы оплаты».
- [ ] Телефон для связи, который бот покажет покупателю.
- [ ] Логотип, короткое описание и желаемое имя бота (`@..._bot`).
- [ ] Язык уведомлений и месячного отчёта владельцу: русский или английский.
- [ ] Числовые Telegram ID владельца и сотрудников (из @userinfobot), ID общего чата, если нужен.
      Каждый один раз нажимает `/start` в боте.
- [ ] Бот создан на аккаунте клиента в @BotFather — на коротком созвоне после заказа. Токен — через
      одноразовую ссылку для секрета или форму требований на Fiverr, не в обычном чате.
- [ ] Пустая Google-таблица, открытая сервисному аккаунту (Standard, Premium). Календарь — 🔄.
- [ ] WhatsApp (Premium, 🔄): бизнес-аккаунт Meta, номер, карта в WhatsApp Manager, решение по
      номеру из приложения, согласованный текст согласия (opt-in).

### 3.3 Быстрая проверка до оценки

Перенеси 2–3 услуги и часы в копию демо-конфига и покажи клиенту `booking chat`
(`demo-scripts.md`, 7.3). Пять минут показа лучше любых слов. Если бот на его прайсе выглядит
странно — ты узнаешь об этом до заказа.

## 4. inbox: разбор почты

### 4.1 Вопросы

```text
EN:
1. Which mailbox: Gmail, Google Workspace, Microsoft 365 or Outlook, Zoho, Yandex, Mail.ru, or your hosting? What is the address? Is it a shared address like info@ or sales@?
2. Who manages the mailbox settings? For Google Workspace: does your admin allow app passwords?
3. About how many emails a day? How many of them are newsletters?
4. What kinds of emails do you get, and what should happen with each: a reply draft, an alert to you, a folder?
5. Which emails are urgent? How fast do you answer them now?
6. Which languages do people write to you in?
7. The facts a reply draft may use: prices, hours, how to book or order, cancellation and refund rules, address. Who approves them?
8. Where do new requests go now: a spreadsheet, a CRM, nowhere?
9. Attachments: do invoices arrive as PDF files, or as scans and photos?
10. Do emails contain health details, legal cases, card numbers or ID documents?
11. Do people from the EU write to you?
12. Should email addresses, names and phone numbers be masked in the sheet? (The subject and the one-line summary are not.)
13. Who gets alerts about hot leads: in Telegram, from a bot you own, or by a webhook to Slack or Make?
14. Where do you write your replies: in Gmail on the web, in Outlook, on the phone?
15. To tune the sorting, after we sign the data agreement: can you share 20–50 typical emails, or would you prefer a test run on your last 20 emails, with nothing changed in the mailbox?

RU:
1. Какая почта: Gmail, Google Workspace, Microsoft 365 или Outlook, Zoho, Яндекс, Mail.ru, почта хостинга? Какой адрес? Это общий адрес вроде info@ или sales@?
2. Кто управляет настройками почты? Для Google Workspace: разрешает ли администратор пароли приложений?
3. Сколько примерно писем в день? Сколько из них рассылки?
4. Какие бывают письма и что делать с каждым: черновик ответа, сообщение вам, папка?
5. Какие письма срочные? Как быстро вы отвечаете на них сейчас?
6. На каких языках вам пишут?
7. Факты, на которые может опираться черновик: цены, часы, как заказать, правила отмены и возврата, адрес. Кто их утверждает?
8. Куда сейчас попадают новые заявки: таблица, CRM, никуда?
9. Вложения: счета приходят PDF-файлами или сканами и фото?
10. Бывают ли в письмах сведения о здоровье, судебные дела, номера карт, документы?
11. Пишут ли вам из ЕС?
12. Маскировать ли в таблице e-mail, имена и телефоны? (Тема и суть письма не маскируются.)
13. Кому сообщать о горячих лидах: в Telegram, через вашего собственного бота, или вебхуком в Slack или Make?
14. Где вы пишете ответы: Gmail в браузере, Outlook, телефон?
15. Для настройки, после подписания соглашения о данных: пришлёте 20–50 типичных писем или лучше пробный разбор последних 20 писем, без изменений в ящике?
```

**Что значат ответы:**

| Вопрос | Ответ | Что делать |
|---|---|---|
| 1 | Gmail, Zoho, Яндекс, Mail.ru, IMAP хостинга | Пароль приложения. Подходит |
| 1–2 | Workspace, пароли приложений запрещены | Админ разрешает их для этого ящика, или автопересылка на отдельный ящик. Вход через OAuth — 🔄 |
| 1, 14 | Outlook или Microsoft 365 | Прямого входа нет. Только автопересылка на отдельный ящик, и черновики появятся там, а не в Outlook. Админ M365 может запрещать внешнюю пересылку. Скажи до оценки (`docs/packages/inbox.md`). Прямое подключение — доработка |
| 1 | Личный ящик сотрудника | Предложи общий адрес. Чужой ящик — флаг 8 |
| 3 | Число писем в день | Оценка LLM-операций: письмо = 1, с черновиком = 2 (`offers.en.md`, «Support plans») |
| 4 | До 5 / до 8 / больше 8 своих категорий | Basic / Standard / Premium |
| 4 | Нужны черновики или сигналы о лидах | Не меньше Standard |
| 7 | Фактов нет или «у нас лучшие цены» | Без проверяемых фактов черновики пустые. Попроси короткие факты с цифрами и письменное «да» |
| 8 | Нужна CRM | Вебхук в Make или Zapier — доработка в Premium. QuickBooks, Xero, HubSpot — доработка с оценкой |
| 9 | Счета PDF | Извлечение полей из PDF-счетов — 🔄. Сканы и фото — доработка |
| 9 | Банковские выписки → таблица | Доработка с оценкой, только после теста на образцах клиента. Подход — из твоего агента выписок (сверка с напечатанными итогами), но это отдельная кодовая база, и грузинские банки на нём не проверялись (`offers.md`, 3.4) |
| 10 | Здоровье, суды | Флаг 2. Номера карт — не берём ящик или исключаем такие письма правилами до модели; решай с юристом |
| 11 | ЕС | DPA обязателен, провайдер модели — субподрядчик (`docs/packages/inbox.md`, «Приватность») |
| 15 | Пробный разбор | Только после письменного согласия и DPA (`demo-scripts.md`, 7.4). На площадках — после старта контракта |

**Скажи клиенту на этом шаге:**
- Письма читает модель OpenAI или Anthropic через API. Тема, отправитель и текст письма уходят
  провайдеру. Рассылки и автоответы модели не показываются.
- Почта проверяется раз в 10–15 минут, не мгновенно.
- Черновики никогда не уходят сами. Письма не удаляются и остаются непрочитанными.
- Тексты писем на нашем сервере не хранятся.

### 4.2 Что собрать

- [ ] Провайдер, адрес IMAP-сервера, адрес ящика, контакт администратора.
- [ ] Таблица категорий: `id`, описание словами клиента, действие (черновик, сигнал, папка).
- [ ] Факты для черновиков — письменно утверждённые; подпись. На каждом языке, на котором пишут
      клиенты.
- [ ] Подписанный DPA. До него — ни примеров писем, ни доступа к ящику.
- [ ] 20–50 примеров писем (клиент убирает лишнее) или согласие на пробный разбор. Примеры храним
      в `clients/<id>/` и удаляем после настройки.
- [ ] Пароль приложения — только при подключении, через одноразовую ссылку для секрета или форму
      требований на Fiverr. Сразу в `.env`.
- [ ] Google-таблица, открытая сервисному аккаунту.
- [ ] Telegram ID владельца и его бот-уведомитель или URL вебхука (Standard, Premium).
- [ ] Решение по маскированию данных в таблице.

## 5. prices: цены конкурентов

### 5.1 Вопросы

```text
EN:
1. What do you sell, and how many products should we watch?
2. Which competitors? How many websites in total?
3. Do you have links to the exact same products on their sites, in the right variant: color, size, volume?
4. How often do you need to know: once a day, every 6 hours, every hour? How fast can you react?
5. What change matters to you? The default alert is a 3% price change.
6. Which currency do you and they use? Do their prices depend on the city or region?
7. Where do your own prices come from, and how often do they change?
8. Can anyone see these prices without logging in, choosing a city or adding to the cart?
9. What will you do with the data: your own pricing decisions, or something else?
10. Are any of these sites marketplaces?
11. Has any of these sites ever blocked you or asked you to stop?
12. Who gets the alerts in Telegram? Which language for the sheet: English or Russian?

RU:
1. Что вы продаёте и за сколькими товарами следить?
2. Какие конкуренты? Сколько всего сайтов?
3. Есть ли у вас ссылки на те же товары у них, в нужном варианте: цвет, размер, объём?
4. Как часто вам нужно знать: раз в день, раз в 6 часов, каждый час? Как быстро вы реагируете?
5. Какое изменение для вас важно? По умолчанию сообщаем об изменении цены на 3%.
6. В какой валюте ваши цены и их? Зависят ли их цены от города или региона?
7. Откуда берутся ваши цены и как часто меняются?
8. Видны ли эти цены любому человеку без входа, выбора города и корзины?
9. Что вы будете делать с данными: решать о своих ценах или что-то другое?
10. Есть ли среди сайтов маркетплейсы?
11. Блокировал ли вас кто-то из этих сайтов или просил прекратить?
12. Кому присылать уведомления в Telegram? На каком языке таблица: английский или русский?
```

**Что значат ответы:**

| Вопрос | Ответ | Что делать |
|---|---|---|
| 1–2 | Сайтов и ссылок (ссылка = товар × сайт) | Basic: до 3 сайтов и 200 ссылок; Standard: до 10 и 1 000; Premium: до 20 и 2 000 (`offers.en.md`) |
| 3 | Ссылок нет, «найдите сами» | Подбор совпадающих товаров — доработка. Клиент даёт ссылки |
| 4 | Каждый час | Посчитай ёмкость (5.3). Чаще раза в час — нет |
| 6 | Цена зависит от региона | Монитор видит цену, которую сайт показывает серверу. Регион выбрать нельзя. Предупреди |
| 7 | Свои цены меняются часто | Ваша цена хранится в конфиге и сама с сайта клиента не читается. Клиент присылает изменения, ты обновляешь конфиг в течение рабочего дня — это часть поддержки (`docs/packages/prices.md`, «Что нужно от клиента», п. 7). Меняет цены каждый день — закладывай часы: скорее Pro, чем Care |
| 8 | Нужен вход, город, корзина | Флаг 4 для этого сайта |
| 9 | Перепубликовать каталог, описания, фото конкурента | Отказ: мы собираем цену, наличие и короткую цитату, а не контент (решение этого файла) |
| 10 | Маркетплейсы | Прочитай их условия использования. Запрещают автоматический сбор — сайт снимаем |
| 11 | Да, блокировали или просили | Сайт не берём (бриф 5.8: претензия — стоп в тот же день) |

### 5.2 Что собрать

- [ ] Файл товаров: ID, название, ваша цена, валюта.
- [ ] Для каждого товара — ссылки на тот же товар у конкурентов, в нужном варианте.
- [ ] Частота, порог изменения, язык таблицы, часовой пояс.
- [ ] Как клиент сообщает об изменении своих цен: сообщением или таблицей, кто отвечает.
- [ ] Telegram для уведомлений: бот клиента и его числовой ID.
- [ ] Google-таблица, открытая сервисному аккаунту.
- [ ] Письменное подтверждение: страницы публичные, и, насколько клиенту известно, условия сайтов
      не запрещают мониторинг (`docs/packages/prices.md`, «Право и этика»).

### 5.3 Проверка до оценки (делаешь сам)

Перед оценкой — по одной странице с каждого сайта: нечитаемый сайт исключается до оплаты, а не
после (`offers.en.md`). Клиенту, который ещё не решил, бесплатно проверяешь до трёх страниц.

1. Открой страницу в браузере. Цена видна без входа, города и корзины? Нет «Checking your
   browser», CAPTCHA, «Access denied»?
2. Открой `https://<сайт>/robots.txt`. Закрыты ли страницы товаров (`Disallow`)? Есть ли
   `Crawl-delay`?
3. Найди «Terms» или «Условия» в подвале сайта. Ищи слова «automated», «scraping», «robots»,
   «crawl», «автоматизированный сбор». Явный запрет — сайт снимаем.
4. Запусти `probe` (одна страница и robots.txt):

   ```bash
   uv run autostudio prices probe "<ссылка на товар>" --contact-email {{EMAIL}}
   ```

   Цена в браузере есть, а `probe` не видит её ни на одной ступени — скорее всего, её рисует
   JavaScript (флаг 5). 🔄 Живой прогон `probe` в проверке не делался: в среде не было сети.
5. **Ёмкость.** Монитор делает не больше одного запроса к сайту раз в 5 секунд, а если robots.txt
   просит больше (`Crawl-delay`, до 60 с) — соблюдает это (`docs/packages/prices.md`). Условие:
   ссылок на одном сайте × пауза ≤ интервал проверки.
   - Каждый час при паузе 5 с — не больше ≈ 720 ссылок с одного сайта. Но проход ограничен ещё и
     `max_run_minutes` (по умолчанию 30): с сайта за проход — не больше min(720, `max_run_minutes` ×
     12), при умолчании ≈ 360, всего ≈ 1 440 (4 сайта параллельно). Что не успели — «в этот раз не
     проверено».
   - Больше ≈ 1 400 ссылок всего или больше 360 на одном сайте — в конфиге клиента
     `max_run_minutes` ≈ 50, в systemd-юните `TimeoutStartSec` ≥ 55min. Ежечасный проход должен
     укладываться меньше чем в 60 минут: план — не больше ≈ 600 ссылок с сайта (`offers.md`, 5.1).
   - `Crawl-delay: 30` — не больше 120 ссылок с сайта в час.
   - Не помещается — реже проверки или меньше ссылок, а не «быстрее».
6. Итог клиенту: какие сайты читаются, какие нет и почему. Нечитаемые сайты в оценку не входят.

## 6. Бланк заказа (одна страница)

> **Оператору.**
> - **Юридически действует бланк Заказа из договора:** `docs/legal/service-agreement.en.md` /
>   `.ru.md`, Schedule 1 / Приложение 1, после проверки юристом (`docs/legal/README.md`). Поля
>   этого бланка совпадают с ним по смыслу. Разошлись — прав договор.
> - **Когда нужен этот бланк.** (1) Предложение до подписания договора: клиент видит на одной
>   странице, что покупает, сколько это стоит и что он должен дать. (2) Чек-лист «What you
>   provide» к Заказу из договора: в Приложении 1 его нет. (3) Площадки: описание объёма в
>   сообщении площадки. Условия оплаты, приёмки и споров там — правила площадки. Строку
>   «Provider» и контакты из версии для площадок убери.
> - Цены, сроки и состав — из `offers.en.md`. Грузинским клиентам — суммы в лари из того же файла.
>   Пилотная скидка −50% — только прямым клиентам в Грузии, только первым трём, и никогда на
>   площадках.
> - Из раздела 5 оставь только блок своего пакета. Тогда бланк помещается на страницу.
> - До пунктов 🔒 из `docs/ACTION_PLAN.md` (ИП, статус малого бизнеса, ответы бухгалтера и юриста)
>   бланк с суммой не отправляем (бриф, риск 5).
> - Формулировка предмета — «разработка, настройка и интеграция ПО», «техническое сопровождение и
>   доработка ПО», не «консалтинг» (НП, бриф 4.2).

**Перед отправкой (для тебя, не для клиента):**
- [ ] Красные флаги (раздел 2) пройдены, ответы записаны.
- [ ] В заказе нет функций с 🔄, которые ещё не влиты.
- [ ] Ёмкость посчитана (prices), нечитаемые сайты исключены.
- [ ] Сумма и сроки совпадают с `offers.en.md`; доработки оценены в часах.
- [ ] Канал: для клиента с площадки нет контактов и нет оплаты мимо площадки.

```text
ORDER FORM No. [2026-001]                                      Date: [YYYY-MM-DD]
Channel: [ ] Direct   [ ] Upwork, contract [ID]   [ ] Fiverr, order [ID]

Client:   [business name], [country]. Contact: [name, role]
Provider: {{NAME}}, individual entrepreneur, Georgia, ID [number]          (direct clients only)

1. PACKAGE
   [ ] Inbox   [ ] Booking bot   [ ] Price monitor
   Tier: [ ] Basic   [ ] Standard   [ ] Premium
   Scope as described in the price list dated [date], attached.
   Key numbers: [mailboxes, categories / staff, services / sites, links, check frequency]

2. OPTIONS (custom work, fixed price from an hour estimate)
   [ ] [item] — [N] h × [rate] = [amount]
   [ ] None

3. PRICE
   Setup: [amount] [USD/GEL] + options [amount] = [total]   [Pilot: 50% off setup, case study terms attached]
   Support plan: [ ] Care  [ ] Pro  [ ] Business — [amount] per month from launch; free fixes come on top. Monthly, cancel any month.
   You pay directly, not through me: [WhatsApp message fees (Meta) / a Meta partner's monthly fee / none].
   AI limit: [ ] the plan's limit as a monthly cap: at the limit, the AI pauses until the 1st, no charges above it   [ ] none: your own AI account
   Change requests and extra hours: [USD 40 / GEL 100] per hour, in 30-minute steps, after you approve the estimate.
   Payment: 50% before work starts, 50% after acceptance. [Upwork/Fiverr: platform milestones only.]

4. TIMELINE
   Delivery: [N] business days, counted from the later of: the first payment, and everything in section 5.
   Approvals by others (Meta, your Google Workspace admin) are not counted.
   Target launch: [date]. Acceptance: 5 business days after I show it working on your data.
   Free fixes after launch: [14 / 30] days.

5. WHAT YOU PROVIDE (keep the block for your package)
   Inbox:   [ ] mailbox address and server   [ ] app password, sent via a one-time secret link
            [ ] categories and what to do with each   [ ] approved facts and signature
            [ ] 20–50 example emails or OK for a test run   [ ] Google Sheet shared with my service address
            [ ] alerts: your own Telegram bot (token via a one-time secret link) and your Telegram ID, or a webhook
            [ ] signed data processing agreement
   Booking: [ ] services: name, duration, price   [ ] staff and hours   [ ] opening hours, holidays
            [ ] booking and cancellation rules, reminder times   [ ] 5–10 FAQ answers
            [ ] numeric Telegram IDs of everyone who gets booking messages   [ ] a 15-minute call to create the bot on your account
            [ ] Google Sheet shared with my service address   [ ] WhatsApp: Meta Business account, number, card in WhatsApp Manager, approved consent text
   Prices:  [ ] products: ID, name, your price, currency   [ ] competitor links per product, exact variant
            [ ] frequency, alert threshold, language, time zone   [ ] how you send me changes to your own prices
            [ ] Telegram bot token (via a one-time secret link) and your Telegram ID for alerts
            [ ] Google Sheet shared with my service address   [ ] written confirmation: public pages, terms allow monitoring

6. AI AND DATA
   AI provider: [ ] OpenAI  [ ] Anthropic  [ ] none (Booking Basic, Price monitor Basic/Standard)
   AI account: [ ] mine, included up to the plan's limit   [ ] yours, you pay the provider directly
   What the AI receives: [Inbox: sender, subject, date, attachment names, email text without quoted history /
   Booking: the customer's typed message, your services, FAQ and hours, no names or phones /
   Prices: the visible text of competitor pages without price data]
   Hosting: [my shared server in [country] / a dedicated server, [cost] per month]
   Booking records kept: [while the agreement is in force / bookings older than [12] months deleted monthly]
   Data processing agreement: [ ] signed on [date]. Required before any access to your mailbox or customer data.
   Accounts created for this order (bot, sheet, WhatsApp) are yours. You can withdraw my access at any time.

7. NOT INCLUDED (main points from the price list)
   [3 lines from "Not included" for your package]

Accepted:  [client name, role, date]          {{NAME}}, [date]
```

## 7. Открытые вопросы к тебе

1. **Пароль приложения через одноразовую ссылку.** Каким сервисом пользуешься для одноразовых
   секретов? Он должен удалять секрет после первого открытия. Впиши его в бланк.
2. **Ящик с номерами карт** (4.1, вопрос 10): отказ или исключение таких писем правилами до модели?
   Вопрос юристу вместе с DPA.
3. **Перепубликация данных конкурента** (5.1, вопрос 9) — отказ решён в этом файле, в брифе его нет.
   Подтверди.
4. **Бланк на русском** для релокантов и на грузинском для местных компаний. Грузинский — носитель
   и юрист.
5. **Флаг 15** (запрещённые виды бизнеса): сверь список с правилами Meta, Upwork и Fiverr и
   допиши конкретные категории.
