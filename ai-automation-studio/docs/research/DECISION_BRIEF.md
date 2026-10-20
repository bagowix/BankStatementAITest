# Решение по запуску: студия ИИ-автоматизации из Грузии

**Дата:** 20.10.2026.
**Основа:** 8 тем ресерча и 8 состязательных проверок.
**Для кого:** соло Python-разработчик в Грузии. Иностранной компании нет, Stripe недоступен.

## 0. Как читать этот документ

У каждого факта стоит одна из трёх меток.

| Метка | Значение |
|---|---|
| **П** | Подтверждено проверкой по первоисточнику 20.10.2026. Первоисточник: официальная страница, исходник документации на GitHub или зеркало официальной документации в Context7 (снимок зеркала может быть старше этой даты). |
| **И** | Проверка исправила исходное утверждение. В тексте стоит исправленная версия. |
| **НП** | Не подтверждено. Исследователь видел только сниппет официальной страницы, зеркало или опирался на память модели, а проверяющий не смог открыть первоисточник или не перепроверял этот пункт. Это рабочая гипотеза: прежде чем на неё тратить деньги, проверьте её в личном кабинете или тестовым платежом. |

**Ограничения проверки.** Из среды проверки не открылись paypal.com, payoneer.com, wise.com, upwork.com, fiverr.com, matsne.gov.ge и rs.ge: мешали прокси, DNS и исчерпанный лимит поиска. Поэтому почти все цифры про деньги, площадки и налоги помечены НП. Подтвердить удалось в основном технику: Telegram, WhatsApp, Google, Cloudflare, цены Claude и лицензию n8n. Пока собирался этот бриф, искать дополнительно тоже было нельзя: лимит WebSearch на ход исчерпан. Поэтому неделя 1 плана начинается с проверки цифр в личных кабинетах.

**Цены пакетов в разделе 4 — это решение брифа, а не рыночный факт.** Рыночные ориентиры указаны рядом вместе со статусами.

## Главное

1. **Идея №1 держится:** пакеты ИИ-автоматизации с фиксированной ценой плюс абонентская поддержка. Спрос растёт, судя по данным самих площадок (НП).
2. **Три поправки к прошлому отчёту:**
   - Пакет «ИИ-разбор почты» стоит расширить до «почта + документы → таблица или учёт». Здесь у вас уже есть доказательство: агент, который извлекает данные из банковских выписок и сверяет итоги.
   - С 01.10.2026 в WhatsApp платные даже ответы в 24-часовом окне (И). Поэтому по умолчанию Telegram, а WhatsApp — платная опция: сообщения Meta оплачивает карта клиента.
   - Клиент, найденный на Upwork или Fiverr, платит за поддержку только через площадку. На Upwork это правило действует 24 месяца (НП).
3. **Порядок каналов.** Прямые продажи и Upwork — сразу. Fiverr — после того как пройдёт верификация личности и тестовый вывод денег.
4. **Деньги.** Порядок открытия:
   - счёт в TBC или BoG;
   - ИП и статус малого бизнеса;
   - Wise, если он разрешит принимать оплату за бизнес;
   - PayPal — запасной вариант для оплаты картой;
   - Payoneer — для выплат с площадок.

   Перед публикацией цен прогоните тестовые платежи по $5–10 через каждый канал.
5. **Налоги (всё НП).** 1% с оборота до 500 000 GEL. От трёх вопросов зависят законность и маржа:
   - не попадёт ли работа под исключение «консалтинг»;
   - платится ли обратный НДС 18% за зарубежные услуги;
   - нужно ли иностранцу разрешение на работу с 01.03.2026.
6. **Инфраструктура.** Около $1–10 в месяц у вас как оператора и около $1–3 в месяц на клиента при Claude Haiku 5.5 и общем VPS. Сборы WhatsApp и прокси сюда не входят. В репозитории модель по умолчанию — `claude-opus-5-5` ($4 / $20), это в 40 раз дороже Haiku 5.5 ($0,10 / $0,50).

## 1. Вердикт по ресерчу

| # | Что утверждал прошлый отчёт | Вердикт | Что верно на 20.10.2026 | Статус | Источник |
|---|---|---|---|---|---|
| 1 | Лучшая идея — пакеты ИИ-автоматизации по фиксированной цене плюс ежемесячная поддержка | Держится | Upwork по итогам 2025 года к 2024: заработки фрилансеров на AI Integration +178%, на AI Chatbot Development +71% (только клиенты из США). «AI automation» был самым частым AI-запросом клиентов два месяца подряд по август 2026 включительно. | НП | [Upwork IR 04.02.2026](https://investors.upwork.com/news-releases/news-release-details/upworks-demand-skills-2026-demand-top-ai-skills-more-doubles-ai), [Upwork Hiring Insights Aug 2026](https://www.upwork.com/research/upwork-monthly-hiring-insights-august-2026) |
| 2 | Продавать через Upwork, Fiverr и прямые выходы на клиентов | Частично | Обе площадки запрещают уводить клиента и брать оплату мимо них, поэтому поддержку таких клиентов можно продавать только внутри площадки. Fiverr во II кв. 2026: активных покупателей 2,7 млн против 3,4 млн годом раньше (−21,9%), зато траты на одного покупателя выросли на 15,6%. Опираться только на Fiverr нельзя. | НП | [Upwork Conversion Fee](https://support.upwork.com/hc/en-us/articles/360043723533-What-is-the-Upwork-Conversion-Fee), [Fiverr off-platform](https://help.fiverr.com/hc/en-us/articles/12792122691601-Stay-protected-Fiverr-s-off-platform-policy), [Fiverr 6-K Q2 2026](https://www.sec.gov/Archives/edgar/data/1762301/000117891326003624/exhibit_99-1.htm) |
| 3 | «Fiverr: AI automation +136%» — данные II кв. 2026 | Неверная дата | +136% взято из Fall 2025 Business Trends Index от 10.12.2025: это рост поисков за 6 месяцев начиная с мая 2025. Свежий индекс от 09.06.2026: Claude Code +938%, n8n AI automation +125%, AI voice agents +49%. Это поиски, а не заказы, и источник — пресс-релиз. | НП | [Fiverr BTI Fall 2025](https://www.fiverr.com/news/2025-fall-business-trends-index), [BTI 09.06.2026](https://aimagazine.com/globenewswire/3308866) |
| 4 | Stripe в Грузии не работает | Держится | Грузии нет в списке стран Stripe. | НП | [stripe.com/global](https://stripe.com/global) |
| 5 | PayPal в Грузии: 4,69% (EEA/UK) или 5,39% (остальные, включая США) + фикс.; 3,4% внутри страны; конвертация 3%; вывод 13,50 GEL | Не подтверждено, есть противоречие | Цифры совпадают в документах PayPal по сниппетам: ROW PDF от 28.09.2026, фиксированная часть 0,30 USD — по PDF апреля 2025. Противоречие: в таблице PayPal Payouts Грузия не входит в уровень «local currency», а строка «вывод GEL-баланса за 13,50 GEL» как раз предполагает этот уровень. На форуме пишут, что вывод на грузинский банк недоступен. | НП | [ROW 28.09.2026](https://www.paypalobjects.com/marketing/ua/OA/merchant-fees/en-ROW-092826.pdf), [PDF 21.04.2025](https://www.paypalobjects.com/marketing/web/shared/clac/en/merchant-fees/paypal-merchant-fees-en-21-Apr-2025.pdf), [PayPal GE fees](https://www.paypal.com/ge/webapps/mpp/paypal-fees), [Payouts countries](https://developer.paypal.com/docs/payouts/standard/reference/country-feature/), [expat.com](https://www.expat.com/en/forum/asia/georgia/882227-paypal-business-account-fees-in-georgia.html) |
| 6 | Payoneer берёт 29,95 USD в год, если за 12 месяцев пришло меньше 2 000 USD | Вероятно, устарело | Сниппет официальной статьи: для Грузии с 23.02.2026 порог 6 000 USD. Проверяющий это не подтвердил, поэтому закладывайте 29,95 USD в год. | НП | [Payoneer annual fee](https://payoneer.custhelp.com/app/answers/detail/a_id/43738/~/annual-account-fee) |
| 7 | Wise работает | Неполно | В Грузии можно держать деньги на Wise, и Грузии нет в списке стран, которым не выдают USD-реквизиты. Но личный аккаунт не предназначен для оплат за бизнес — для них нужен Wise Business. Дают ли Wise Business грузинскому ИП, не проверено. | НП | [Wise: где можно жить](https://wise.com/help/articles/2813542/where-do-i-need-to-live-to-hold-money-with-wise), [Wise help](https://wise.com/help/) |
| 8 | Комиссия Upwork около 10% | Устарело | Комиссия переменная, 0–15% на каждый контракт, видна до отклика. Сверху 18% НДС Грузии, если нет грузинского VAT ID. Худший случай — 17,7%. | НП | [Upwork fee](https://support.upwork.com/hc/en-us/articles/211062538-Learn-about-the-Freelancer-Service-Fee), [Georgia VAT](https://support.upwork.com/hc/en-us/articles/18521482302995-Georgia-VAT-for-freelancers) |
| 9 | Пакет (a): бот записи в Telegram или WhatsApp | Держится с оговорками | С 15.01.2026 в WhatsApp запрещены AI-ассистенты общего назначения, но бот конкретного бизнеса разрешён (П). С 01.10.2026 платные и ответы внутри 24-часового окна (И). Оставить клиенту номер из приложения WhatsApp Business (coexistence) можно только через Tech Provider или BSP (П). | П / И | [Meta AI Providers](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/ai-providers), [Meta non-template](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages), [Meta migrate number](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/migrate-existing-whatsapp-number-to-a-business-account) |
| 10 | Пакет (b): парсер цен конкурентов в Google Sheets | Держится с оговорками | Собирать только публичные страницы без логина, не обходить CAPTCHA и антибот-защиту. Upwork и Fiverr запрещают заказы, которые нарушают ToS третьих сторон. Средний заказ на сбор данных на Fiverr ≈ $106 (страница 2025 года), поэтому зарабатывать надо на сопровождении. | НП | [Upwork prohibited](https://support.upwork.com/hc/en-us/articles/17995622011667--What-kind-of-jobs-aren-t-allowed-on-Upwork), [Fiverr prohibited](https://help.fiverr.com/hc/en-us/articles/49174165608593-Prohibited-services-on-Fiverr), [Fiverr hire web scraping](https://www.fiverr.com/hire/web-scraping) |
| 11 | Пакет (c): ИИ-разбор почты | Держится, но архитектура другая | Скоупы Gmail readonly, modify и metadata — restricted. Мультиклиентскому приложению на своём GCP-проекте нужна оценка безопасности CASA и её ежегодное повторение. Решение — разворачивать приложение в аккаунте каждого клиента. | П | [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), [Restricted verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification) |
| 12 | ИП + статус малого бизнеса: 1% до 500 000 GEL; консалтинг исключён; роялти не входят | Не подтверждено | Совпадает со знаниями модели, но первоисточники matsne.gov.ge и rs.ge не открылись. | НП | [Налоговый кодекс](https://matsne.gov.ge/en/document/view/1043717), [Andersen](https://ge.andersen.com/1-tax-regime/) |
| 13 | Целевой сегмент — русскоязычный бизнес | Сузить | Только бизнес за пределами России. Telegram в России без VPN в апреле 2026 был доступен примерно на 5% (OSW, по прошлому отчёту). России и Беларуси нет в списках стран, где работают Anthropic и OpenAI. | НП | [OSW](https://www.osw.waw.pl/en), [Anthropic countries](https://www.anthropic.com/supported-countries), [OpenAI countries](https://developers.openai.com/api/docs/supported-countries) |
| 14 | n8n можно использовать для клиентов | Исправлено | Официальный FAQ от 22.09.2026 разрешает вести воркфлоу многих клиентов на своём инстансе как managed service, если у клиентов нет доступа к инстансу. Нельзя продавать «хостинг n8n», давать клиенту строить или править воркфлоу, делать white-label. | И | [n8n license FAQ](https://github.com/n8n-io/n8n-docs/blob/main/docs/n8n-community-license/license-faq.md), [LICENSE.md](https://raw.githubusercontent.com/n8n-io/n8n/master/LICENSE.md) |

## 2. Платёжные рельсы

### 2.1 Сравнение

| Рельс | Для кого | Входящий платёж | Вывод в Грузию | Ежемесячная поддержка | Статус |
|---|---|---|---|---|---|
| **Wise** ([приём](https://wise.com/help/articles/2978013/what-are-the-fees-and-limits-for-receiving-money), [GEL](https://wise.com/help/articles/2932350/guide-to-gel-transfers)) | прямые клиенты из США и ЕС | USD ACH — 0; USD wire/Swift — 6,11 USD | В GEL на любой грузинский счёт. Перевод 1 000 USD стоит ≈ 14,69 USD (≈ 1,5%) при курсе ≈ 2,60. Отправлять деньги из GEL нельзя. | Автосписания нет: клиент сам настраивает регулярный ACH или платит по счёту | НП. Для оплат от клиентов нужен Wise Business; доступен ли он грузинскому ИП, не проверено ([Wise help](https://wise.com/help/)) |
| **PayPal Business GE** ([ROW 28.09.2026](https://www.paypalobjects.com/marketing/ua/OA/merchant-fees/en-ROW-092826.pdf), [вывод](https://www.paypal.com/ge/cshelp/article/how-do-i-withdraw-money-to-my-bank-account-help394), [инвойсы](https://www.paypal.com/ge/webapps/mpp/email-invoice)) | клиенты, которые хотят платить картой | США и прочие страны — 5,39% + фикс.; EEA/UK — 4,69% + фикс.; фиксированная часть для USD — 0,30 USD ([PDF 04.2025](https://www.paypalobjects.com/marketing/web/shared/clac/en/merchant-fees/paypal-merchant-fees-en-21-Apr-2025.pdf)) | Конвертация 3%, вывод 13,50 GEL, до 7 рабочих дней. Работает ли вывод на грузинский банк, спорно. | Инвойсы есть, без абонплаты. Подтверждения, что Subscriptions доступны для Грузии, нет ([country codes](https://developer.paypal.com/api/rest/reference/country-codes/)) | НП |
| **Payoneer** ([цены](https://www.payoneer.com/pricing/), [годовая плата](https://payoneer.custhelp.com/app/answers/detail/a_id/43738/~/annual-account-fee)) | выплаты с площадок; клиенты — бизнесы из США | На receiving-счёт в USD или EUR — 1% (минимум 1 USD); оплата картой — до 3,99% + 0,49 USD; ACH — 1% | USD на грузинский банк — 2%, минимум 15 USD, минимальная сумма вывода 50 USD ([уведомление 2021 года](https://payoneer.custhelp.com/app/answers/detail/a_id/36365/~/upcoming-changes-to-limits-and-fees-in-your-payoneer-account)). Годовая плата 29,95 USD | Только если платит бизнес из США через ACH Debit ([recurring](https://payoneer.custhelp.com/app/answers/detail/a_id/31220/~/recurring-payments)) | НП |
| **Upwork Direct Contracts** ([справка](https://support.upwork.com/hc/en-us/articles/360025040794-Direct-Contracts-bring-a-client-to-Upwork)) | прямые клиенты, которые хотят платить картой | С вас 5% (0% при Freelancer Plus). Клиент платит marketplace fee и разовый Contract Initiation Fee 0,99–14,99 USD ([справка](https://support.upwork.com/hc/en-us/articles/26106318334611-What-is-the-Contract-Initiation-Fee-on-Upwork)) | как обычный вывод с Upwork | недельный платёж или milestone | НП. Только для клиентов, у которых никогда не было аккаунта Upwork |
| **Перевод в GEL на счёт ИП** (TBC или BoG) | местные клиенты | тарифы банков не найдены | — | счёт 1-го числа | НП |
| **BOG Payments, Link Payment API** ([документация](https://api.bog.ge/docs/en/link-payment/introduction)) | предоплата записи у местных клиентов | ссылки на оплату: Visa, Mastercard, Amex, Apple Pay, Google Pay, BOG App | требования к мерчанту и комиссии не найдены | — | П — API существует; НП — условия |
| **Stripe** ([список стран](https://stripe.com/global)) | — | Грузии нет в списке | — | — | НП |

Ещё один вариант для проверки: клиенты, которые платят подрядчикам через Deel. Способы вывода из Deel в Грузию и комиссии не проверены ([Deel help](https://help.letsdeel.com/)).

### 2.2 Рекомендуемая схема и порядок открытия

1. **Банк.** Откройте мультивалютный счёт (USD и GEL) в TBC или Bank of Georgia, затем счёт ИП. Сначала спросите банк о вашем гражданстве: с 2022 года к гражданам РФ применяют усиленные проверки (НП, [BoG](https://bankofgeorgia.ge/), [TBC](https://www.tbcbank.ge/)).
2. **ИП и статус малого бизнеса** — до первого платежа от клиента (раздел 6).
3. **Wise.** Откройте аккаунт и в приложении проверьте две вещи:
   - выдают ли реквизиты USD ACH/routing и EUR IBAN;
   - доступен ли Wise Business грузинскому ИП.

   Если Wise Business недоступен, не принимайте на Wise оплату от клиентов.
4. **PayPal Business GE.** Сделайте тестовый платёж $5–10 и выведите его в GEL, запишите все фактические списания. Отдельно проверьте:
   - можно ли вывести USD на долларовый счёт без конвертации 3%;
   - есть ли в аккаунте раздел Subscriptions.

   Принимайте только коммерческие платежи (инвойс или goods & services), никогда «друзьям и семье». Seller Protection обычно не покрывает услуги, а спор можно открыть в течение 180 дней (НП, [user agreement GE](https://www.paypal.com/ge/legalhub/paypal/useragreement-full)).
5. **Payoneer** — для выплат с площадок. Выводите пачками от 750 USD: при 2% и минимуме 15 USD мелкие выводы дороже (НП).
6. **Upwork.** В Settings → Get paid проверьте:
   - Direct to Local Bank (0,99 USD) для Грузии, вероятно, недоступен (НП, [справка](https://support.upwork.com/hc/en-us/articles/211060578-What-are-the-fees-limits-and-timing-of-Direct-to-Local-Bank-payments));
   - Wise USD подключается как Direct to U.S. Bank: 2,99 USD за вывод для налогового адреса вне США с 01.09.2026 (НП, [справка](https://support.upwork.com/hc/en-us/articles/227022468-Direct-to-U-S-Bank-timing-and-fees-explained), [digital bank](https://support.upwork.com/hc/en-us/articles/12928623064723-Use-a-digital-bank-to-get-paid-on-Upwork));
   - wire стоит 50 USD (в старых источниках 30), им стоит выводить только суммы от 2 000 USD (НП, [справка](https://support.upwork.com/hc/en-us/articles/211063898-How-do-wire-transfers-work-on-Upwork)).
7. **Fiverr.** Вывод на PayPal — 0 USD со стороны Fiverr; на Payoneer — 3 USD; банковским переводом через Payoneer — 1 или 3 USD. В Wise Fiverr не выводит (НП, [справка](https://help.fiverr.com/hc/en-us/articles/360010530058-Withdrawing-your-earnings-managing-payout-methods)).
8. **Расходы оплачивайте долларовой картой.** С 15.04.2026 TBC и BoG берут 1,5% (до 10 GEL) за оплату в GEL на зарубежных площадках (НП, [cbw.ge](https://www.cbw.ge/banking/georgian-banks-to-charge-15-fee-on-lari-payments-abroad-starting-april-15)).

**Счёт прямому клиенту:** USD-инвойс 1-го числа, оплата в течение 7 дней. Способы в порядке предпочтения: Wise ACH → Wise wire → PayPal. Комиссию PayPal заложите в цену или добавьте ≈ 6% к счёту.

### 2.3 Стоимость типовых платежей

Это расчёт. Все входные данные — НП, курс 2,62 GEL/USD — НП ([NBG через агрегатор](https://allratestoday.com/central-bank-rates-api/nbg.md)).

| Сценарий | Рельс | Потери | Нетто | Основа расчёта |
|---|---|---|---|---|
| Счёт 300 USD, клиент из США | Wise ACH | ≈ 4,4 USD (≈ 1,5% конвертации) | ≈ 295,6 | [Wise](https://wise.com/help/articles/2932350/guide-to-gel-transfers) |
| то же | Wise wire | 6,11 + ≈ 4,3 = ≈ 10,4 | ≈ 289,6 | [Wise](https://wise.com/help/articles/2978013/what-are-the-fees-and-limits-for-receiving-money) |
| то же | Payoneer | 3 (1%) + 15 (минимум за вывод) = 18 | ≈ 282 (≈ 6%) | [Payoneer](https://www.payoneer.com/pricing/) |
| то же | PayPal, вывод сразу | 16,47 + 8,51 (3%) + 13,50 GEL ≈ 5,15 = ≈ 30,1 | ≈ 269,9 (≈ 10%) | [PayPal ROW](https://www.paypalobjects.com/marketing/ua/OA/merchant-fees/en-ROW-092826.pdf) |
| Поддержка 99 USD/мес | PayPal, вывод раз в месяц пачкой | 5,64 + 2,80 = 8,44 | ≈ 90,6 (≈ 8,5%). Если выводить этот платёж отдельно, ещё ≈ 5,15, итого потери ≈ 13–14% | то же |
| то же | Wise ACH | ≈ 1,5 | ≈ 97,5 | Wise |
| Upwork, фикс. 1 000 USD, комиссия 10% | Wise (Direct to U.S. Bank) | 100 + 18 НДС + 2,99 + ≈ 12,9 конвертации | ≈ 866 | [Upwork fee](https://support.upwork.com/hc/en-us/articles/211062538-Learn-about-the-Freelancer-Service-Fee), [VAT](https://support.upwork.com/hc/en-us/articles/18521482302995-Georgia-VAT-for-freelancers) |
| то же, комиссия 15% | Wise | 150 + 27 + 2,99 + ≈ 12 | ≈ 808 | то же |
| то же, комиссия 10% | wire в USD | 118 + 50 + комиссии банков-корреспондентов (не проверены) | ≈ 832 минус сборы банков | [wire](https://support.upwork.com/hc/en-us/articles/211063898-How-do-wire-transfers-work-on-Upwork) |
| Fiverr, заказ 250 USD | PayPal | 50 (20%) + 6 (3% конвертации) + ≈ 5,15 | ≈ 189 | [Fiverr earnings](https://help.fiverr.com/hc/en-us/articles/9234443621137-Your-earnings-page) |
| то же | банковский перевод через Payoneer | 50 + 1–3 + конвертация 1,2–4% (НП) | ≈ 191–197 | [Fiverr payouts](https://help.fiverr.com/hc/en-us/articles/360010530058-Withdrawing-your-earnings-managing-payout-methods), [Payoneer fees](https://www.payoneer.com/fees/) |

## 3. Площадки

### 3.1 Upwork, Fiverr и прямые продажи

Всё в таблице — НП, если не указано иное.

| Параметр | Upwork | Fiverr | Прямые продажи |
|---|---|---|---|
| Можно ли работать из Грузии | Грузии нет в списке запрещённых стран. Запрет для РФ и Беларуси действует по местонахождению. Есть санкционный скрининг. ([eligibility](https://support.upwork.com/hc/en-us/articles/211067778-Who-s-eligible-to-join-and-use-Upwork), [sanctions](https://support.upwork.com/hc/en-us/articles/55329263532563-Sanctions-screening-on-Upwork)) | Список стран Fiverr не публикует. Формулировка «not available to citizens» — риск для владельца паспорта РФ ([payment methods](https://help.fiverr.com/hc/en-us/articles/37332701180945-Payment-methods)) | — |
| Верификация | Госудостоверение с фото, видео или фото, телефон. Если страна документа не совпадает со страной профиля, нужно подтвердить адрес в течение 7 дней, иначе аккаунт заморозят. ВНЖ в списке документов нет ([ID](https://support.upwork.com/hc/en-us/articles/211067788-How-to-verify-your-identity-with-a-government-ID-and-proof-of-address)) | Удостоверение и селфи (требование DSA, срок был 17.02.2025) ([verification](https://help.fiverr.com/hc/it/articles/22582440968849-Verifica-delle-informazioni-personali-e-aziendali)) | — |
| Комиссия с вас | 0–15% на контракт плюс 18% НДС Грузии, итого до 17,7% ([fee](https://support.upwork.com/hc/en-us/articles/211062538-Learn-about-the-Freelancer-Service-Fee), [VAT](https://support.upwork.com/hc/en-us/articles/18521482302995-Georgia-VAT-for-freelancers)) | 20% со всего, включая чаевые ([earnings](https://help.fiverr.com/hc/en-us/articles/9234443621137-Your-earnings-page)) | 0; потери на платёжном канале 1,5–10% (раздел 2.3) |
| Комиссия с клиента | marketplace fee плюс Contract Initiation Fee 0,99–14,99 USD ([init fee](https://support.upwork.com/hc/en-us/articles/26106318334611-What-is-the-Contract-Initiation-Fee-on-Upwork)) | 5,5% плюс сбор за маленький заказ: 3,50 USD при заказе до 200 USD, по другой странице 3 USD при заказе до 100 USD ([fee](https://help.fiverr.com/hc/en-us/articles/360050216133-Paying-for-orders-extras-or-custom-offers), [clients](https://help.fiverr.com/hc/en-us/articles/360010558038-How-Fiverr-works-for-clients)) | — |
| Цена откликов | Connects по 0,15 USD (покупка от 10). На отклик обычно 2–16 Connects (сторонний источник). На Basic 10 Connects в месяц бесплатно, Freelancer Plus за 19,99 USD/мес даёт 100 ([Connects](https://support.upwork.com/hc/en-us/articles/211062898-Understanding-and-using-Connects), [Plus](https://www.upwork.com/resources/is-upwork-free)) | Откликов нет. Реклама в Fiverr Ads доступна только с Level 1 ([Ads](https://help.fiverr.com/hc/en-us/articles/360017729338-Promoting-your-Gigs-with-Fiverr-Ads)) | ваше время |
| Когда приходят деньги | Фиксированная цена: 14 дней на приёмку плюс 5 дней защитной задержки. Почасовая оплата: примерно через 10 дней после конца недели. Новый способ вывода активируется через 3 дня ([timing](https://support.upwork.com/hc/en-us/articles/40368585192723-When-can-I-withdraw-my-earnings)) | Через 14 дней после завершения заказа (7 дней для Top Rated, Pro и Seller Plus). Заказ принимается автоматически через 3 дня после сдачи ([clearance](https://help.fiverr.com/hc/en-us/articles/34069565843985-How-Fiverr-works-for-freelancers)) | по счёту, срок оплаты 7 дней |
| Ежемесячная поддержка | Готового ретейнера нет. Варианты: (а) фиксированный контракт «Support — месяц» с milestone, который оплачивается в начале месяца; (б) почасовой контракт с еженедельным платежом = месячная цена / 4,33, списывается по понедельникам ([recurring](https://support.upwork.com/hc/en-us/articles/45819532067219-How-to-set-up-a-recurring-weekly-payment-for-your-freelancer)) | Подписки на 3 или 6 месяцев, только для «select freelancers» и в отдельных подкатегориях. Или повторяющийся custom offer до 6 месяцев. Почасовая работа — только с Level 1 ([subscriptions](https://help.fiverr.com/hc/en-us/articles/33272731177105-Subscription-orders-for-freelancers), [hourly](https://help.fiverr.com/hc/en-us/articles/27516246556945-Hourly-work-for-freelancers)) | счёт 1-го числа, ACH на Wise |
| Витрина пакетов | Project Catalog: 3 уровня плюс опции. Модерация для новичка — «несколько недель» ([catalog](https://support.upwork.com/hc/en-us/articles/4408644453395-How-we-review-your-Project-Catalog-project)) | Не больше 4 гигов у нового продавца. Level 1: 5 заказов, 3 клиента, 400 USD, рейтинг 4,4+, ответы на 80% сообщений. Пороги могли поменяться ([levels](https://help.fiverr.com/hc/en-us/articles/360010560118-Understanding-Fiverr-s-freelancer-levels)) | свой лендинг |
| Увести клиента с площадки | 24 месяца всё идёт через Upwork. Даже просьба оплатить мимо площадки — нарушение и вечный бан. Выкуп клиента: Conversion Fee = 13,5% годового заработка (ставка × 2 080 ч); при 30 USD/ч это 8 424 USD. По памяти проверяющего — «большее из 2 500 USD и 15%», это противоречие ([Conversion Fee](https://support.upwork.com/hc/en-us/articles/360043723533-What-is-the-Upwork-Conversion-Fee)) | Все оплаты только через Fiverr. Нарушения: предупреждение, удаление гига, бан ([off-platform](https://help.fiverr.com/hc/en-us/articles/12792122691601-Stay-protected-Fiverr-s-off-platform-policy)) | — |
| Время до первого заказа | 30–100 откликов, от 1 до 12+ недель (блоги, разброс в 5–10 раз) ([getmany](https://getmany.com/blog/upwork-tips-for-beginners)) | Выход на первую страницу за 2–8 недель (блог, [unil.ink](https://unil.ink/blog/how-to-make-money-on-fiverr-2026)) | данных нет, проверить интервью |

### 3.2 Ловушки в правилах площадок (нарушение лишает канала)

- **Upwork, контакты.** Telegram, WhatsApp или email в профиле и отклике до начала контракта запрещены. Демо показывайте ссылкой или видео внутри Upwork (НП, [справка](https://support.upwork.com/hc/en-us/articles/360052511833-Get-to-know-each-other-before-a-contract)).
- **Upwork, оплата мимо площадки.** Предложить клиенту с Upwork платить напрямую в течение 24 месяцев, в том числе за поддержку, — это бан (НП).
- **Upwork, вход в аккаунт.** VPN, вход из РФ и второй аккаунт «в обход проверки» ведут к блокировке (НП, [sanctions](https://support.upwork.com/hc/en-us/articles/55329263532563-Sanctions-screening-on-Upwork)).
- **Upwork, профиль.** Если в первые 90 дней не отправить ни одного отклика, профиль станет приватным (НП, [справка](https://support.upwork.com/hc/en-us/articles/115003975967-Why-was-my-profile-changed-to-private)). С 28.05.2026 Specialized Profiles отменены, остаётся один профиль (НП, [справка](https://support.upwork.com/hc/en-us/articles/115013750068-Create-a-Specialized-Profile)).
- **Fiverr, контакты и оплата.** Нельзя договариваться об оплате вне Fiverr и делиться контактами. Поддержку клиенту с Fiverr продавайте только через подписку или повторяющийся custom offer (НП).
- **Fiverr, ИИ.** Скрывать, что используете ИИ, нельзя. Результат работы ИИ нужно дорабатывать под клиента. Если клиент просит работу без ИИ, это требование надо выполнить (НП, [AI policy](https://help.fiverr.com/hc/en-us/articles/34998793899665-Using-AI-on-Fiverr-Guidelines-for-freelancers-and-clients)).
- **Fiverr, запрещённые услуги.** Нельзя автоматизировать платформы, которые это запрещают, и обходить пароли или 2FA. Доступы клиента собирайте через Gig requirements (НП).
- **Обе площадки.** Запрещён сбор данных, на который у вас нет права, и любые заказы, нарушающие чужие ToS (НП).

### 3.3 Неделя 1

1. **Upwork**
   - Зарегистрироваться из Грузии без VPN. Нужны документ и подтверждение адреса в Грузии на ваше имя. Заполнить W-8BEN.
   - В Settings → Get paid выписать доступные способы вывода и комиссии.
   - Заголовок профиля: «AI automation / AI workflow automation / Python». Это растущие запросы ([Hiring Insights](https://www.upwork.com/research/upwork-monthly-hiring-insights-august-2026)).
   - Открыть 10 подходящих вакансий и записать по каждой процент комиссии и цену отклика в Connects.
   - Отправить 10–15 откликов в первые 60 минут после публикации вакансии, со ссылкой на демо.
   - Подать 3 проекта в Project Catalog.
2. **Fiverr**
   - Зарегистрироваться и пройти проверку личности.
   - В Earnings → Withdraw посмотреть способы вывода для Грузии.
   - Письменно спросить поддержку, можно ли продавать с вашим гражданством, и сохранить ответ.
   - Открыть оформление заказа как покупатель на ≈ $99 и ≈ $150 и посмотреть сбор за маленький заказ.
   - Публиковать гиги только после этого.
3. **Прямые продажи**
   - Выписать 50 карточек салонов, клиник и автосервисов в Google Maps с каналами записи.
   - Собрать 10–20 местных прайсов.
   - Сделать 5 личных визитов.
   - Поднять лендинг на Cloudflare.
4. **Тестовые платежи** $5–10 по каждому каналу (раздел 2.2).

## 4. Какие 3 пакета продавать

### 4.1 Решение

| Место | Пакет (код в репозитории) | Почему |
|---|---|---|
| 1 | **inbox** — «ИИ-разбор входящих: почта и документы → Google Sheets, CRM или учёт» | (1) «AI automation» — самый частый AI-запрос на Upwork (НП, [Upwork](https://www.upwork.com/research/upwork-monthly-hiring-insights-august-2026)). (2) В каталоге Upwork за извлечение данных из счетов просят $265 / $595 / $875 и $1,300 / $3,200. Это выше, чем за чат-ботов, но выборка — 2 листинга без числа продаж (НП, [листинг](https://www.upwork.com/services/product/development-it-invoice-extraction-accounting-sync-automation-n8n-make-com-1927043207117551724)). (3) Готовое доказательство — ваш агент извлечения выписок со сверкой итогов: «цифры сходятся, расхождения помечены». (4) LLM стоит ≈ $0,68 за 1 500 писем на Haiku 5.5 (П, [цены](https://platform.claude.com/docs/en/about-claude/pricing)). (5) Ограничения Gmail снимаются архитектурой (раздел 5). Локальная ниша: выписки TBC/BoG → таблица (гипотеза). |
| 2 | **booking** — «Бот записи с напоминаниями» | Основной продукт для местного малого бизнеса. Естественный повод для абонентки: хостинг, напоминания, правки. Telegram Bot API бесплатен (П, [FAQ](https://core.telegram.org/bots/faq)), код для Telegram уже есть. Минусы: у WhatsApp появилась переменная себестоимость (И). Конкуренты — Meta Business Agent (П) и SaaS вроде Altegio. |
| 3 | **prices** — «Мониторинг цен конкурентов → Sheets и алерты» | Чистый Python. На публичных страницах юридический риск низкий (НП). Абонентка естественна: сайты меняются, парсеры ломаются. Чек за настройку низкий — средний заказ на сбор данных на Fiverr ≈ $106 (НП, 2025, [Fiverr](https://www.fiverr.com/hire/web-scraping)). |

**Что не делать на старте:**
- Отдельный RAG-бот для сайта. Гиги стоят $50–200, бывают заказы за $30 (НП, [biztoolkit](https://www.biztoolkit.co/post/freelance-chatbot-developer-rates-in-2026-ai-pricing)). Продавать только как опцию FAQ в пакете booking.
- n8n как отдельный продукт.
- Голосовой ресепшн. Себестоимость $0,10–0,31 за минуту (НП, [ainora](https://ainora.lt/blog/ai-voice-agent-cost-per-minute-2026)), для исходящих звонков в США действует TCPA (НП, [Orrick](https://www.orrick.com/en/Insights/2024/02/Did-the-FCC-Really-Ban-AI-Generated-Voice-Calls-No-But-Restrictions-Apply)), распознавание грузинской речи не проверено.
- Лидогенерация скрейпингом: риски GDPR и грузинского закона о персональных данных.

### 4.2 Правила цены

1. **Рыночные ориентиры (все НП):**
   - медиана бюджета AI-вакансий на Upwork ≈ $158 ([UpHunt](https://uphunt.io/blog/do-ai-skills-pay-more-on-upwork-2026));
   - Fiverr: средний проект AI-чат-бота ≈ $216, AI-агента ≈ $295 ([cost guide](https://fiverr.com/resources/guides/costs/chatbot-developer));
   - Upwork: специалисты по AI-чат-ботам $30–61/ч, rule-based бот $300–1,200 за проект ([hire](https://www.upwork.com/hire/ai-chatbot-specialists/));
   - агентства берут за WhatsApp-бота записи от $1,000 плюс $30–80 в месяц ([achiya](https://achiya-automation.com/en/industries/clinics/)).
2. **Одна долларовая витрина** для Upwork, Fiverr и зарубежных прямых клиентов. Цены стартовые, после 5–10 отзывов поднять на 30–50%.
3. **Сколько остаётся на руках:** Fiverr — 80% цены; Upwork — от 82,3% до 100% (комиссия 0–15% × 1,18); прямые клиенты — 90–98,5% в зависимости от рельса. После первых отзывов на Fiverr можно ставить витрина / 0,8, чтобы нетто сравнялось с прямой продажей.
4. Пакеты от $200 избегают сбора Fiverr за маленький заказ (НП).
5. **Формулировки в договоре и счёте:** «разработка, настройка и интеграция ПО», «техническое сопровождение и доработка ПО». Не «консалтинг». Код передаётся как результат услуги, а не как лицензия (НП, раздел 6).
6. **Порядок оплаты настройки:** 50% вперёд, 50% после приёмки. На Upwork — через milestones. На Fiverr тоже milestones: базовый пакет от $100, этап от $50 (НП, [milestones](https://help.fiverr.com/hc/en-us/articles/360010560178-Working-with-Milestones)).

### 4.3 Настройка (setup), USD

| Пакет | Basic | Standard | Premium |
|---|---|---|---|
| **inbox** | **$150.** 1 ящик (Gmail, Workspace, M365 или IMAP) или 1 тип документа. До 10 полей в Google Sheets, запуск по расписанию. 14 дней на исправления. | **$400.** Классификация писем по категориям клиента. Черновики ответов, которые никогда не отправляются сами. Извлечение данных из 2 типов вложений (PDF-счета, чеки). Алерты в Telegram. 14 дней на исправления. | **$900.** Всё из Standard плюс выгрузка в QuickBooks, Xero, HubSpot или учётную таблицу. Проверка сумм и сверка итогов, поиск дублей, отчёт о расхождениях. 30 дней гарантийной поддержки. |
| **booking** | **$200.** Telegram-бот: меню услуг, свободные слоты из Google Calendar, подтверждение и отмена записи, уведомления владельцу. 1 мастер или точка, языки ru/en. | **$450.** Плюс второй канал: WhatsApp Cloud API в аккаунте клиента или Messenger. Напоминания за 24 и за 2 часа. Журнал записей в Sheets. Языки ka/ru/en, грузинский переводит носитель. | **$950.** Плюс понимание свободного текста через LLM — только запись и FAQ этого бизнеса. Несколько мастеров, ссылка на предоплату, отчёт по неявкам. |
| **prices** | **$120.** До 3 сайтов, до 200 SKU, раз в день, выгрузка в Sheets. | **$300.** До 10 сайтов, до 2 000 SKU, история цен, алерты в Telegram или на почту. | **$700.** Сайты на JS (headless-браузер), сопоставление SKU с помощью LLM, дашборд. Обновление до раза в час, но в пределах лимита вежливости (раздел 5.8). |

### 4.4 Ежемесячная поддержка, USD (одинаковая для всех пакетов)

| Уровень | В месяц | Что входит | Еженедельный платёж на Upwork (÷ 4,33) |
|---|---|---|---|
| **Care** | $49 | Хостинг нашего Python-кода; мониторинг доступности с алертами; исправление поломок до 1 ч в месяц; месячный отчёт из журнала событий; до 2 000 LLM-операций. | ≈ $12 |
| **Pro** | $149 | До 4 ч доработок, ответ в течение 24 ч, ремонт парсеров при смене вёрстки, до 10 000 LLM-операций. | ≈ $35 |
| **Business** | $349 | До 10 ч, ответ за 4 рабочих часа, один новый мини-воркфлоу в квартал, до 30 000 LLM-операций. | ≈ $81 |

- **Лимиты LLM-операций.** Это решение брифа. Себестоимость ≈ $0,45 за 1 000 операций на Haiku 5.5 без thinking (П, расчёт по [ценам](https://platform.claude.com/docs/en/about-claude/pricing)).
- **По факту, отдельно от абонентки, оплачиваются:**
  - сообщения WhatsApp — их оплачивает карта клиента в WhatsApp Manager;
  - 360dialog — $59 за номер в месяц, если нужен coexistence (П, [360dialog](https://docs.360dialog.com/docs/get-started/pricing));
  - LLM сверх лимита.
- **Строки «хостинг n8n» в прайсе быть не должно** (И, [n8n FAQ](https://github.com/n8n-io/n8n-docs/blob/main/docs/n8n-community-license/license-faq.md)).
- **Клиентам с Upwork и Fiverr** поддержку выставлять только внутри площадки (раздел 3).
- **Ориентир рынка** для ботов записи: $30–100 в месяц (НП, агентство).

### 4.5 Прайс в лари для местного малого бизнеса

Эквивалент в долларах указан по курсу ≈ 2,62 (НП, [NBG через агрегатор](https://allratestoday.com/central-bank-rates-api/nbg.md)); сверяйте курс на [nbg.gov.ge](https://nbg.gov.ge/en/monetary-policy/currency). Для сравнения: средняя номинальная зарплата в Грузии в I кв. 2026 — 2 363,8 ₾ (НП, [Geostat](https://geostat.ge/media/80106/Average-Monthly-Nominal-Earnings-of-Employees---%28I-Quarter%2C-2026%29.pdf)).

| Пакет | Basic | Standard | Premium |
|---|---|---|---|
| booking | 390 ₾ (≈ $149) | 890 ₾ (≈ $340) | 1 690 ₾ (≈ $645) |
| inbox | 390 ₾ (≈ $149) | 990 ₾ (≈ $378) | 1 990 ₾ (≈ $760) |
| prices | 290 ₾ (≈ $111) | 690 ₾ (≈ $263) | 1 490 ₾ (≈ $569) |
| Поддержка в месяц | «Базовый» 99 ₾ (≈ $38) | «Про» 249 ₾ (≈ $95) | «Бизнес» 590 ₾ (≈ $225) |

- **Состав тарифов** такой же, как в 4.3 и 4.4. Основной канал записи для грузинских владельцев — WhatsApp, Instagram или Messenger (раздел 8).
- **Цены не опираются на местные прайсы конкурентов:** их не нашли. Проверьте цены на 10–20 местных прайсах и в 10–15 интервью.
- **Первым трём клиентам** скидка 50% на настройку в обмен на кейс или отзыв.
- **Аргумент для салона:** одна предотвращённая неявка в неделю окупает 99 ₾ в месяц. Это гипотеза, проверить в интервью.
- **Оплата** банковским переводом в GEL на счёт ИП. Цены без НДС: ИП не плательщик НДС до порога 100 000 ₾ (НП).

## 5. Технические ограничения пакетов

### 5.1 Telegram (booking)

**Факты:**
- **П.** Bot API бесплатен. Лимиты: около 1 сообщения в секунду в один чат, 20 в минуту в группу, около 30 в секунду при рассылке. Webhook работает на портах 443, 80, 88 и 8443 с заголовком `secret_token`. Webhook и getUpdates взаимоисключающие, обновления хранятся не дольше 24 ч ([FAQ](https://core.telegram.org/bots/faq), [API](https://core.telegram.org/bots/api)).
- **П.** Бот не может написать первым. Напоминания уйдут только тем, кто открыл бота, поэтому запись нужно начинать через deep link `t.me/<bot>?start=…` ([bots](https://core.telegram.org/bots)).
- **И.** Bot API 10.0 (08.05.2026): business- и secretary-ботов можно подключать к аккаунтам без Telegram Premium. `can_reply` работает только в личных чатах, где было входящее сообщение за последние 24 ч ([changelog](https://core.telegram.org/bots/api-changelog)). Исправляет прошлый тезис, что владельцу нужен Premium.
- **НП.** Статус Telegram и WhatsApp в России в 2026 году.

**Проектируем:** webhook с `secret_token`; ограничение 1 сообщение в секунду на чат и не больше ≈ 25 в секунду на рассылку напоминаний; deep link; идемпотентная обработка обновлений.

### 5.2 WhatsApp Cloud API (booking, Standard и Premium)

**Цены и лимиты:**
- **И. Тарификация с 01.10.2026.** Платными стали каждое нешаблонное (service) сообщение и utility-шаблоны внутри 24-часового окна. Тариф равен utility-тарифу рынка получателя, скидок за объём нет. Бесплатно только 72-часовое окно free entry point (FEP): пользователь пишет с Android или iOS из рекламы Click-to-WhatsApp или по кнопке CTA страницы Facebook ([non-template](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages)). Снимок документации сделан до 01.09.2026, поэтому перед прайсом сверьтесь с живой страницей. Проверка tech_compliance подтвердила, что ответы в окне бесплатны, но опиралась на более ранний снимок, и он устарел.
- **НП.** По данным Twilio, первые 1 000 сервисных сообщений на номер в месяц бесплатны ([Twilio](https://help.twilio.com/articles/53100480177819)).
- **П.** Грузия (+995) входит в регион «Rest of Central & Eastern Europe» и не вынесена в отдельные рынки. Тариф в USD не получен, нужен CSV rate card ([pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing), [регионы](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/conversation-based-pricing)).
- **П.** Meta меняет цены только 1-го числа квартала и предупреждает минимум за месяц. Поэтому в договор нужен пункт о пересмотре цены ([pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)).
- **П. Лимит исходящих.** Новый business portfolio пишет первым не больше чем 250 уникальным пользователям за скользящие 24 ч. Дальше 2 000, 10 000, 100 000, без ограничений. С 07.10.2025 лимит считается на портфолио ([limits](https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits)).

**Номер, подключение и правила:**
- **П. Номер из приложения WhatsApp Business.** Либо удалить аккаунт в приложении (история пропадёт), либо coexistence через Solution Partner или Tech Provider. Условия coexistence: приложение версии 2.24.17 или новее, пропускная способность 20 сообщений в секунду, рассылки становятся только для чтения ([migrate](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/migrate-existing-whatsapp-number-to-a-business-account), [coexistence](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users), [throughput](https://developers.facebook.com/documentation/business-messaging/whatsapp/throughput)).
- **П. Статус Tech Provider.** Нужны верификация бизнеса и App Review. Через Embedded Signup до верификаций можно подключить 10 новых клиентов за 7 дней, после — 200 ([tech provider](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/get-started-for-tech-providers), [ES overview](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview)).
- **П. 360dialog:** €49 / $59 в месяц за номер, сообщения Meta оплачиваются отдельно. Пополнение картой — комиссия 4%. Неоплаченный счёт блокирует исходящие. Coexistence поддерживается ([pricing](https://docs.360dialog.com/docs/get-started/pricing), [invoices](https://docs.360dialog.com/docs/hub/invoices), [coexistence](https://docs.360dialog.com/docs/resources/phone-numbers/coexistence)).
- **П. ИИ-ассистенты.** С 15.01.2026 запрещены AI-ассистенты общего назначения, бот конкретного бизнеса разрешён. Плата с AI Providers сейчас берётся только в Бразилии ([AI providers](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/ai-providers)).
- **П. Согласие.** Перед любыми шаблонными сообщениями нужен opt-in с названием бизнеса и целью; отписку надо исполнять ([opt-in](https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in)).
- **П. Новое портфолио:** до верификации не больше 2 номеров и 250 шаблонов на WABA ([numbers](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/phone-numbers)).

**Изменения в API:**
- **П.** Пользователя идентифицируйте по BSUID (`from_user_id`), а не по телефону: при username номера в вебхуке может не быть ([BSUID](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids)).
- **П, бета.** WABA делится на WAAC и Messaging Account, поэтому ID отправителя нужно вынести в абстракцию ([account model](https://developers.facebook.com/documentation/business-messaging/whatsapp/account-model-evolution)).

**Конкурент:**
- **П. Meta Business Agent** — собственный ИИ-агент Meta для WhatsApp: $2 за 1 млн токенов, примерно $0,04–0,05 за сообщение ([about](https://developers.facebook.com/documentation/business-messaging/whatsapp/about-the-platform)).

**Проектируем:**
- Клиент владеет своим Business portfolio и WABA, вы — админ или system user, карта клиента привязана к WABA.
- Статус Tech Provider на старте не получаем.
- Шаблоны напоминаний заранее отправляем на одобрение; фиксируем opt-in.
- BSUID, абстракция отправителя, бот строго «запись и FAQ».

### 5.3 Instagram и Messenger

- **П (Context7).** На сообщения в Instagram API отвечать в течение 24 ч, тег `human_agent` даёт до 7 дней. Приложению, которое обслуживает чужие аккаунты, нужны Advanced Access, App Review и Business Verification. Приложению в портфеле самого клиента («только для бизнеса, которым я владею или управляю») хватает Standard Access без App Review ([IG app review](https://developers.facebook.com/documentation/instagram-platform/app-review)).
- **НП.** Требования Messenger API не проверены.

**Проектируем:** отдельное приложение Meta в портфеле каждого клиента. Instagram — опция Premium после Telegram и WhatsApp.

### 5.4 Google Calendar (booking)

**НП** (официальная документация через зеркало, вторично не проверялась): сервисные аккаунты, созданные после 02.03.2020, могут приглашать гостей только через domain-wide delegation, а она есть только в Workspace. Квота — 600 запросов в минуту на пользователя ([Calendar](https://developers.google.com/workspace/calendar/api/releases)).

**Проектируем:**
- Клиент расшаривает календарь на email сервисного аккаунта с правом «Изменять события».
- Бот создаёт события без attendees, подтверждение уходит в мессенджер.
- Для Workspace с приглашениями — domain-wide delegation, её включает администратор клиента.

### 5.5 Почта: Gmail, Workspace, Microsoft 365 (inbox)

**Факты:**
- **П. Скоупы.** Restricted: `mail.google.com`, readonly, compose, insert, modify, metadata, settings.basic, settings.sharing. Sensitive: `gmail.send`. Если данные restricted-скоупов хранятся или передаются на ваших серверах, нужна оценка CASA и её повтор раз в 12 месяцев, проверка занимает недели ([scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), [restricted](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification)).
- **П. Internal-приложение.** Пользователь типа Internal не требует верификации, но проект должен принадлежать организации Workspace клиента.
- **П. Режим Testing.** Не больше 100 тестовых пользователей, refresh-токены живут 7 дней ([overview](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview), [OAuth2](https://developers.google.com/identity/protocols/oauth2)).
- **П. Пароли в Workspace.** С 01.05.2025 вход по логину и паролю отключён, нужен OAuth. IMAP включает администратор ([LSA](https://support.google.com/a/users/answer/11339703), [XOAUTH2](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol)).
- **П. Apps Script** не требует верификации, если владелец и пользователи в одном домене Workspace ([Apps Script](https://developers.google.com/apps-script/guides/client-verification)).
- **П. Сторонние приложения.** Администратор может их запретить; тогда OAuth-клиент нужно отметить как Trusted ([API controls](https://support.google.com/a/answer/7281227)).
- **НП. Личный @gmail.com:** IMAP с паролем приложения при включённой 2FA ([app passwords](https://support.google.com/accounts/answer/185833)).
- **НП. Microsoft Graph:** делегированные Mail.Read и Mail.ReadWrite не требуют согласия администратора ([Graph](https://learn.microsoft.com/en-us/graph/permissions-reference)).
- **НП. Передача текста писем в LLM.** Проверить Google API Services User Data Policy, раздел Limited Use ([policy](https://developers.google.com/terms/api-services-user-data-policy)).

**Проектируем по типу клиента.** Мультиклиентское Gmail-приложение на своём проекте никогда не делаем: оно требует CASA.

| Клиент | Путь |
|---|---|
| Google Workspace | Internal OAuth-клиент в GCP-проекте организации клиента; или Apps Script в домене клиента; или сервисный аккаунт с domain-wide delegation (`gmail.modify` выдаёт администратор). IMAP через XOAUTH2. |
| Личный @gmail.com | IMAP и пароль приложения (НП) или GCP-проект на аккаунте самого клиента в статусе Published. Режим Testing не использовать. |
| Microsoft 365 | Single-tenant приложение Entra в тенанте клиента и Graph. Для доступа без входа пользователя — application permission плюс application access policy на один ящик. |
| Другие IMAP | IMAP и пароль приложения (как сейчас в коде). |

### 5.6 Google Sheets (все пакеты)

**НП** (официальная документация через зеркало): 300 запросов в минуту на проект и 60 в минуту на пользователя. Сервисный аккаунт считается одним пользователем. Batch считается одним запросом. Таймаут 180 с, при превышении ответ 429 ([limits](https://developers.google.com/workspace/sheets/api/limits)).

**Проектируем:** один `values.batchUpdate` за прогон, экспоненциальная пауза при 429. Таблицу клиент расшаривает на сервисный аккаунт с правами Editor.

### 5.7 LLM (все пакеты)

**Цены и лимиты:**
- **П. Цены за 1 млн токенов.** `claude-haiku-5-5`: $0,10 вход / $0,50 выход при промпте до 100K токенов; $0,50 / $2,50 сверх этого, и тогда весь запрос считается по высокой цене; cache hit $0,01; Batch $0,05 / $0,25. `claude-sonnet-5-5`: $2 / $10. `claude-opus-5-5`: $4 / $20 (по справочнику claude-api, кэш 06.10.2026). Обработка только в США (`inference_geo` = us) — ×1,1. Инструменты добавляют 286 скрытых входных токенов ([pricing](https://platform.claude.com/docs/en/about-claude/pricing)).
- **П. Thinking у Haiku 5.5** включён по умолчанию (effort medium) и оплачивается как выход. Для классификации ставьте effort `low` или `thinking: {type: disabled}` — это допустимо при effort high и ниже ([effort](https://platform.claude.com/docs/en/build-with-claude/effort), [thinking cost](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost)).
- **П. Лимит расходов.** На тире Start потолок $500 в месяц; при достижении API отвечает 429 `enforced_spend_limit_reached` до 1-го числа. Новые организации могут начать с тира Evaluation ([rate limits](https://platform.claude.com/docs/en/api/rate-limits)).
- **П. Кредиты** предоплаченные и сгорают через год ([оплата](https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage)).

**Доступность и данные:**
- **П.** Грузия есть в списке поддерживаемых стран Anthropic, страница действует с 12.11.2026 ([countries](https://www.anthropic.com/supported-countries)).
- **НП.** России и Беларуси нет в списках Anthropic и OpenAI. OpenAI предупреждает: за предоставление доступа за пределами списка аккаунт могут заблокировать ([OpenAI](https://developers.openai.com/api/docs/supported-countries)).
- **НП. Anthropic:** не обучает модели на данных клиентов, DPA входит в Commercial Terms, Batch API хранит данные 29 дней ([retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention), [terms](https://www.anthropic.com/legal/commercial-terms)).
- **НП. OpenAI:** не обучает модели без согласия, логи злоупотреблений хранит до 30 дней, `store=false`. gpt-5.4-mini: $0,75 / $4,50 ([your data](https://developers.openai.com/api/docs/guides/your-data), [model](https://developers.openai.com/api/docs/models/gpt-5.4-mini)).

**Проектируем:**
- По умолчанию Haiku 5.5 с effort `low` или без thinking. Качество проверить на 50 реальных примерах против Opus 5.5; выбор модели остаётся за оператором.
- Промпт не длиннее 100K токенов, кэшировать системный промпт.
- Почту не отправлять через Batch API.
- В Console задать лимит расходов. При 429 работать на детерминированных эвристиках.
- Ответ модели проверять по схеме.

### 5.8 Парсинг (prices)

**Факты (все НП):**
- **США.** hiQ v. LinkedIn (2022): сбор публичных страниц, вероятно, не нарушает CFAA ([9th Cir.](https://cdn.ca9.uscourts.gov/datastore/opinions/2022/04/18/17-16783.pdf)). Meta v. Bright Data (2024): сбор публичных данных без входа в аккаунт не нарушил условия Meta ([FBM](https://www.fbm.com/post/102kqw7/)).
- **ЕС.** Ryanair C-30/14: сайт может ограничить парсинг договором ([CJEU](https://curia.europa.eu/juris/liste.jsf?num=C-30/14)).
- **Персональные данные.** Цены и SKU к ним не относятся, а имена продавцов на маркетплейсах могут относиться.

**Проектируем:**
- Только публичные страницы без логина, соблюдаем robots.txt, честный User-Agent.
- Пауза 3–5 с между запросами на домен.
- Не обходим CAPTCHA и антибот-защиту. Резидентные прокси для обхода блокировок не используем: это противоречит правилу выше. Если сайт блокирует, снимаем его из мониторинга.
- **Ёмкость.** При паузе 4–5 с на домен успеваем 720–900 запросов в час. Ежечасный Premium рассчитан на страницы категорий или фиды, а не на 2 000 карточек одного домена.
- Получили претензию — прекращаем сбор с этого сайта в тот же день.

### 5.9 n8n и персональные данные

- **И. n8n.**
  - Можно: вести воркфлоу клиентов на своём инстансе без их доступа к нему, без ограничения числа клиентов; клиенты могут подключать свои учётные данные. Можно ставить n8n на сервер клиента, если вы не продаёте ему хостинг.
  - Нельзя: давать клиенту строить или править логику через UI, API, MCP или агента; white-label; продукт, чья ценность почти целиком из n8n; строка «хостинг n8n» ([FAQ](https://github.com/n8n-io/n8n-docs/blob/main/docs/n8n-community-license/license-faq.md)).
  - Файлы `.ee` требуют Enterprise-лицензии ([LICENSE.md](https://raw.githubusercontent.com/n8n-io/n8n/master/LICENSE.md)).
  - Вывод: основной стек — Python, n8n по желанию.
- **НП. Закон Грузии о персональных данных.** Принят 14.06.2023, действует с 01.03.2024. Данные о здоровье — особая категория. Уведомление об утечке в течение 72 ч. Штрафы 1 000–10 000 GEL (по вторичному источнику) ([matsne](https://matsne.gov.ge/en/document/view/5827307)).
  - Проектируем: минимум данных (имя, телефон, услуга, время), без диагнозов, DPA с клиентом, срок хранения, порядок действий при утечке.

### 5.10 Сводка: что проектируем и что даёт клиент

| Пакет | Проектируем | Клиент предоставляет |
|---|---|---|
| booking | Telegram webhook и deep link; ограничение скорости; общий календарь сервисного аккаунта; WhatsApp в WABA клиента, BSUID, opt-in, шаблоны; минимум персональных данных | Список услуг, мастеров и график; доступ к календарю; для WhatsApp — Business portfolio, номер, карту в WhatsApp Manager и решение по coexistence; текст согласия; грузинский перевод или его согласование |
| inbox | Архитектура по типу почты (5.5); черновики никогда не отправляются сами; Haiku 5.5 без thinking; сверка сумм | Доступ администратора или пароль приложения; категории; 20–50 примеров писем и документов; согласие на обработку у LLM-провайдера (DPA) |
| prices | Ограничение скорости на домен; batchUpdate; без обхода защиты; стоп по претензии | Список URL конкурентов; таблица соответствия SKU; гарантия законной внутренней цели; доступ к таблице |

### 5.11 Что поменять в репозитории `ai-automation-studio`

1. `src/autostudio/settings.py`: сейчас для Anthropic по умолчанию `claude-opus-5-5` ($4 / $20). Для классификации и извлечения поставить `claude-haiku-5-5` с effort `low` или без thinking и проверить качество на выборке. У Opus 5.5 thinking не отключается.
2. **inbox:** добавить IMAP XOAUTH2 для Workspace и Microsoft Graph для M365. Сейчас поддерживается только IMAP с паролем.
3. **booking:** адаптер WhatsApp — BSUID, абстракция отправителя, opt-in, статусы одобрения шаблонов.
4. **prices:** ограничитель запросов на домен и проверка ёмкости частоты при сохранении конфига.
5. **Все пакеты:** обработка 429 `enforced_spend_limit_reached` с переходом на эвристики.

## 6. Налоги и юридическое оформление в Грузии

**Всё в этом разделе — НП.** matsne.gov.ge, rs.ge, napr.gov.ge и psh.gov.ge в проверке не открылись. Без ответа бухгалтера счета не выставлять.

### 6.1 Шаги по порядку

1. **Банк.** Откроют ли счёт ИП (USD и GEL) с вашим гражданством и статусом пребывания ([TBC](https://www.tbcbank.ge/), [BoG](https://bankofgeorgia.ge/)).
2. **Миграция.** Нужно ли иностранцу с ИП разрешение на работу по поправкам к закону о трудовой миграции, которые действуют с 01.03.2026 ([matsne](https://matsne.gov.ge/)).
3. **Регистрация ИП** в Доме юстиции (NAPR):
   - паспорт с нотариальным переводом на грузинский;
   - заявление;
   - юридический адрес в Грузии с согласием собственника;
   - пошлина ≈ 50 GEL за следующий рабочий день или ≈ 100 GEL в день обращения;
   - вид деятельности — разработка ПО (NACE 62.01), а не консалтинг.

   Регистрация ИП не даёт права на пребывание ([NAPR](https://napr.gov.ge/), [PSH](https://psh.gov.ge/)).
4. **Статус малого бизнеса.** В тот же день подать заявку на rs.ge, бесплатно. Доход до получения статуса облагается по общему режиму, 20% ([rs.ge](https://www.rs.ge/)).
5. **Первые деньги от клиентов** принимать только после того, как статус появится в кабинете.

### 6.2 Факты (все НП)

| Тема | Что известно | Источник |
|---|---|---|
| Ставка | 1% с валового дохода без вычета расходов при доходе до 500 000 GEL за календарный год. Сверх порога — 3% (по памяти, только на превышение). Статус снимают, если порог превышен два года подряд. | [Налоговый кодекс](https://matsne.gov.ge/en/document/view/1043717), [Andersen](https://ge.andersen.com/1-tax-regime/) |
| Исключения | Консалтинг (приказ Минфина № 996), лицензируемые виды деятельности, подакцизные товары, обмен валют. Роялти в режим не входят. Облагается ли смешанный доход целиком по 20% или только его часть, неизвестно. | [matsne](https://matsne.gov.ge/), [Andersen](https://ge.andersen.com/1-tax-regime/) |
| Декларация | Ежемесячно, нарастающим итогом, уплата до 15-го числа следующего месяца. Подавать и нулевые (бесплатно, снимает риск). | [rs.ge](https://www.rs.ge/) |
| Валюта | Пересчёт по официальному курсу НБГ на дату получения. Консервативно: база — сумма до комиссии площадки, дата — зачисление на баланс площадки или кошелька. | [NBG](https://nbg.gov.ge/en/monetary-policy/currency) |
| НДС | Обязательная регистрация при обороте больше 100 000 GEL за любые 12 месяцев подряд, ставка 18%, срок — 2 рабочих дня. Услуги иностранным бизнесам, скорее всего, в порог не входят; иностранным частным лицам — неясно. | [Налоговый кодекс](https://matsne.gov.ge/en/document/view/1043717) |
| Обратный НДС | 18% на услуги нерезидентов: комиссии площадок, API, хостинг. Касается и ИП, который не плательщик НДС. Это 1,8–3,6% оборота только с комиссии площадки. Если поставщик сам выставляет грузинский НДС, обратное начисление не нужно. | там же |
| НДС от Upwork | 18% на комиссии, Connects и подписку, если нет грузинского VAT ID. | [Upwork VAT](https://support.upwork.com/hc/en-us/articles/18521482302995-Georgia-VAT-for-freelancers) |
| Местные клиенты | Компания, которая платит физлицу без ИП, удерживает 20%. Передавайте клиенту выписку об ИП и подтверждение статуса малого бизнеса. | [rs.ge](https://www.rs.ge/) |
| Резидентство | Налоговый резидент Грузии — 183 дня в любом 12-месячном периоде. Если провели в России 183 дня и больше, вы налоговый резидент РФ (13–15%). | [matsne](https://matsne.gov.ge/en/document/view/1043717), [nalog.gov.ru](https://www.nalog.gov.ru/) |
| Пенсия | 4% для самозанятых. Обязательна для граждан и постоянных резидентов; иностранцы без ПМЖ, по памяти, не участвуют. | [pensions.ge](https://www.pensions.ge/) |
| Касса | Нужна для наличных; при оплате по счёту — нет. Наличные не принимать. | [rs.ge](https://www.rs.ge/) |
| Другие режимы | Виртуальная зона и International Company — только для ООО. | [matsne](https://matsne.gov.ge/) |
| Upwork W-8BEN | Без формы вывести деньги нельзя; при правильно заполненной форме налог США не удерживается. | [IRS](https://www.irs.gov/forms-pubs/about-form-w-8-ben) |

**Учёт.** Таблица поступлений: дата, валюта, сумма, курс НБГ на дату. Скользящая 12-месячная сумма по грузинским клиентам для контроля порога НДС. Напоминание к 15-му числу. Хранить выгрузки Upwork, Fiverr, PayPal и Payoneer. **Резерв в ценах:** 1% налога плюс 2–3% на возможный обратный НДС, пока бухгалтер не ответил.

### 6.3 Вопросы бухгалтеру

1. Делаю для иностранных и грузинских компаний Telegram- и WhatsApp-ботов, парсеры цен с выгрузкой в Google Sheets и ИИ-сортировку почты, затем беру ежемесячную плату за сопровождение. Не будет ли это консалтингом по приказу Минфина № 996 / ст. 88 НК? Какой код NACE указать и как формулировать предмет договора и счёта?
2. С какой суммы считать 1% при работе через Upwork или Fiverr: с полной суммы контракта или после комиссии площадки? Что считается датой получения дохода — зачисление на баланс площадки или вывод на банк? По какому курсу НБГ пересчитывать?
3. Должен ли я как ИП, не плательщик НДС, платить обратный НДС 18% с комиссий Upwork, Fiverr и PayPal, с API OpenAI и Anthropic и с хостинга? Если да, как и до какого числа подаётся декларация? Что делать, если поставщик уже выставил грузинский НДС?
4. Входят ли в порог НДС 100 000 GEL услуги иностранным компаниям и иностранным физлицам (B2C, покупатели на Fiverr)? Сколько дней даётся на регистрацию плательщиком НДС после превышения?
5. Нужна ли нулевая декларация в месяцы без дохода? Какие штрафы за просрочку и может ли из-за неё слететь статус?
6. С какой даты действует статус малого бизнеса после заявки и как облагается доход, полученный до этой даты?
7. Облагается ли курсовая разница при продаже полученных USD?
8. Ежемесячная плата за поддержку и размещение бота у меня — это услуга или роялти?
9. Как облагается смешанный доход, если часть работ признают консалтингом?
10. Обязательны ли для меня пенсионные взносы 4% (иностранец без ПМЖ) и с какой базы?
11. Какие документы давать грузинским клиентам, чтобы они не удерживали 20%?

### 6.4 Вопросы юристу и банку

- **Юристу по миграции.** Нужно ли мне, иностранцу (укажите гражданство и статус пребывания) с ИП и зарубежными клиентами, разрешение на работу или регистрация права на работу по поправкам с 01.03.2026?
- **Юристу по персональным данным.** Какой сейчас регулятор: Служба защиты персональных данных или State Audit Office после поправок 2026 года? Какая статья регулирует прямой маркетинг? Нужен ли DPA между мной и клиентом, если бот хранит имена и телефоны его клиентов?
- **Банку (TBC, BoG).**
  - Откроете ли счёт ИП в USD и GEL с моим гражданством и статусом пребывания? Какие документы нужны?
  - Можно ли зачислять выводы из PayPal и Payoneer?
  - Сколько стоит входящий SWIFT в USD?
  - Какие условия у BOG Payments для ИП?

## 7. Инфраструктура и расходы

### 7.1 Расходы оператора в месяц

| Статья | Вариант | $/мес | Статус |
|---|---|---|---|
| Лендинг | Cloudflare Workers static assets или Pages Free: запросы к статике бесплатны; 500 сборок в месяц, 20 000 файлов, до 25 MiB на файл ([limits](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/pages/platform/limits.mdx)) | 0 | П |
| Ограничения хостинга | GitHub Pages запрещён для бизнеса ([GitHub](https://github.com/github/docs/blob/main/content/pages/getting-started-with-github-pages/github-pages-limits.md)) (П). Vercel Hobby — только некоммерческое использование ([Vercel](https://vercel.com/docs/limits/fair-use-guidelines#commercial-usage)) (НП). Запрет на коммерцию в Cloudflare Free в документации не найден, ToS не перечитаны ([plans](https://www.cloudflare.com/plans/)) (НП). | — | П / НП |
| Домен .com | Cloudflare Registrar без наценки; оплата PayPal или картой; только DNS Cloudflare; автопродление; основной способ оплаты должен работать ([registrar FAQ](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/registrar/faq.mdx), [billing](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/billing/understand/faq.mdx)). Цена ≈ $10,46/год ([registrar](https://www.cloudflare.com/products/registrar/)) | ≈ 0,87 | П — условия; НП — цена |
| Входящая почта | Cloudflare Email Routing | 0 | П |
| Исходящая почта с DKIM своего домена | Cloudflare Email Sending (бета), нужен Workers Paid от $5: 3 000 писем включено, дальше $0,35 за 1 000; SMTP на порту 465 ([pricing](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/email-service/platform/pricing.mdx), [limits](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/email-service/platform/limits.mdx)). Не для холодных рассылок. | 5 | П |
| Бесплатная альтернатива | Zoho Mail Free ([Zoho](https://www.zoho.com/mail/zohomail-pricing.html)) или Gmail «Send as» — без DKIM вашего домена ([Gmail](https://support.google.com/mail/answer/22370)) | 0 | НП |
| Мониторинг | Healthchecks.io Free, 20 проверок ([код](https://github.com/healthchecks/healthchecks/blob/master/hc/accounts/models.py)), плюс Uptime Kuma (MIT) на отдельном хосте. UptimeRobot Free для клиентов не использовать, пока не проверены условия ([UptimeRobot](https://uptimerobot.com/pricing/)). | 0 | НП |
| VPS (демо и общий) | Hetzner CX23 ≈ €2,99 + €0,50 за IPv4; в 2026 цены, возможно, выросли — закладывать €4–6 ([Hetzner](https://www.hetzner.com/cloud)). Запасные: DigitalOcean $6 или Vultr $5, оба принимают PayPal ([DO](https://www.digitalocean.com/pricing/droplets)). | ≈ 4–6 | НП |
| LLM на демо | Haiku 5.5 | < 1 | П (цены) |
| Payoneer | 29,95 USD в год при обороте ниже порога | ≈ 2,5 | НП |
| Upwork Freelancer Plus | $19,99 + 18% НДС ≈ $23,59. Брать при обороте Direct Contracts больше ≈ $470 в месяц или при покупке больше ≈ 130 Connects в месяц ([Upwork](https://www.upwork.com/resources/is-upwork-free)) | 0 или 23,59 | НП |
| Fiverr Seller Plus Kickstart | $15/мес; на старте не брать ([Fiverr](https://help.fiverr.com/hc/en-us/articles/360017140717-Seller-Plus-Standard-and-Premium-Advanced-tools-for-business-growth)) | 0 | НП |
| Демо-видео и созвоны | OBS и YouTube unlisted ([Loom](https://www.loom.com/pricing) Free: 25 роликов по 5 мин); Cal.com Free без встроенной оплаты, так как она идёт через Stripe ([Cal.com](https://cal.com/pricing)) | 0 | НП |
| **Итого** | Минимум ≈ $1, рекомендуемо ≈ $10–12 (домен + Workers Paid + VPS + Payoneer) | | |

### 7.2 Расходы на одного клиента в месяц

| Статья | Сумма | Статус |
|---|---|---|
| Доля VPS при 5–10 лёгких клиентах на одном CX23 | €0,35–0,70; бэкапы +20% | НП |
| Отдельный VPS (headless-парсеры или изоляция) | €4–6 | НП |
| inbox: 1 500 писем × (2 000 вх. + 500 вых. токенов) | Haiku 5.5 ≈ $0,68; Sonnet 5.5 ≈ $13,5; Opus 5.5 ≈ $27, плюс thinking, который нельзя отключить | П (цены; объёмы — допущение; thinking не включён) |
| booking: 300 диалогов × 8 реплик × (3 000 + 300) | Haiku ≈ $1,08; Sonnet ≈ $21,6; Opus ≈ $43,2 | П (то же) |
| prices: 2 000 товаров × (1 000 + 100), один прогон | Haiku ≈ $0,30 (ежедневно ≈ $9); Sonnet ≈ $6; Opus ≈ $12 | П (то же) |
| Сообщения WhatsApp | Тариф для +995 неизвестен; оплачивает карта клиента в WABA | И / НП |
| 360dialog (если coexistence) | $59 за номер | П |
| Резидентные прокси | Не используем для обхода защиты (раздел 5.8) | — |
| **Итого** | ≈ $1–3 на общем VPS с Haiku 5.5; ≈ $5–7 на отдельном VPS. При абонентке $49–349 маржа больше 90% | расчёт |

Все цены LLM взяты по [странице цен Claude](https://platform.claude.com/docs/en/about-claude/pricing). Токенизатор новых моделей может давать примерно на 30% больше токенов (НП).

### 7.3 Разовые расходы и привлечение клиентов

| Статья | Сумма | Статус |
|---|---|---|
| Регистрация ИП | ≈ 50 GEL (следующий рабочий день) или ≈ 100 GEL (в тот же день) ([PSH](https://psh.gov.ge/)) | НП |
| Нотариальный перевод паспорта; перевод текстов на грузинский носителем | стоимость не найдена | — |
| Connects на 50–100 откликов (8–16 Connects × $0,15) | $60–240 + 18% НДС ≈ $71–283, без Boost ([Connects](https://support.upwork.com/hc/en-us/articles/211062898-Understanding-and-using-Connects)) | НП |
| Запас денег на жизнь | 1–3 месяца до первого заказа (блоги) | НП |

## 8. Локальный рынок

Почти всё в этом разделе — НП: первичные источники не открылись, опросов малого бизнеса Грузии нет.

- **Сегменты (гипотезы):**
  - салоны красоты и барбершопы;
  - стоматологии и клиники — осторожно с данными о здоровье;
  - автосервисы;
  - туроператоры и гостевые дома Батуми;
  - бизнесы русскоязычных релокантов в Тбилиси и Батуми;
  - для пакета inbox — бухгалтерские фирмы и малый бизнес со счетами и выписками TBC/BoG.
- **Каналы связи клиентов с бизнесом.**
  - В Грузии 3 037 500 пользователей Facebook Messenger, около 78% населения (март 2024, НП, [NapoleonCat](https://napoleoncat.com/stats/messenger-users-in-georgia/2024/03)).
  - Telegram у грузин нишевый, но основной у релокантов (НП, [StatCounter](https://gs.statcounter.com/social-media-stats/all/georgia)).
  - Записываются, вероятно, по телефону, через Instagram Direct и Messenger (наблюдение, [Google Maps](https://www.google.com/maps/search/beauty+salon+tbilisi)).
  - Вывод: для грузинских владельцев одного Telegram мало. Проверить на 50 карточках Google Maps и в 10–15 интервью до разработки мультиканальности.
- **Конкуренты:**
  - **Meta Business Agent** (П): $2 за 1 млн токенов, примерно $0,04–0,05 за сообщение, есть коннекторы для записи. Это прямой конкурент, и его же можно использовать как движок ([Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/about-the-platform)).
  - **Altegio** (частично П): маркетплейс приложений партнёров с категорией Chat Bots; уведомления в WhatsApp идут через приложения маркетплейса. Это ещё и возможный канал продаж бота. Цены и присутствие в Грузии — НП ([Altegio API](https://developer.alteg.io/_bundle/en/developers/openapi.json), [pricing](https://alteg.io/en/pricing/)).
  - **Fresha** — НП ([pricing](https://www.fresha.com/pricing)).
  - **DIKIDI и YCLIENTS** у русскоязычных салонов — НП ([DIKIDI](https://dikidi.net)).
  - **Booksy**, вероятно, в Грузии не работает (НП).
  - **Местные SaaS для записи** не найдены.
- **Позиционирование:** «без нового приложения и без комиссий за клиентов — бот в вашем мессенджере, записи в вашей таблице». Если у клиента уже есть CRM, продавать интеграцию поверх неё, а не замену.
- **Где искать клиентов (НП):**
  - личные визиты (Ваке, Сабуртало, центр Батуми);
  - рекомендации;
  - чаты релокантов, где правила разрешают рекламу;
  - коворкинги (Fabrika, Impact Hub);
  - объединения бизнеса ([GITA](https://gita.gov.ge), GCCI, AmCham, BAG);
  - Google Maps и [yell.ge](https://www.yell.ge) как база контактов.
- **Холодные рассылки — нельзя.** WhatsApp требует opt-in (П, [opt-in](https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in), [policy](https://business.whatsapp.com/policy)). Грузинский закон о персональных данных требует согласия на прямой маркетинг (НП, [personaldata.ge](https://personaldata.ge/en)). Если пишете первым — одно персональное сообщение с фразой «если неинтересно — напишите, больше не побеспокою» и журнал отказов.
- **Язык (НП, [CRRC](https://caucasusbarometer.org/en/)):**
  - грузинским владельцам — по-грузински, запасной английский; текст делает носитель;
  - релокантам и туристическому Батуми — по-русски или по-английски;
  - обращаться по-русски к грузинам может быть политически чувствительно.
- **Не целиться в бизнес в России:** Telegram там почти недоступен, а LLM-провайдеры Россию не поддерживают (раздел 1, строка 13).
- **Оплата местными клиентами:** перевод в GEL на счёт ИП. Предоплата записи — через BOG Link Payment (условия НП). Требование рассчитываться между резидентами только в лари не проверено.

## 9. Риски и стоп-правила

| # | Риск | Что делаем | Стоп-правило (решение брифа) |
|---|---|---|---|
| 1 | Не проходит верификация на Upwork или Fiverr из-за паспорта; санкционный скрининг (НП) | Документы и подтверждение адреса заранее, без VPN, одно и то же имя везде | Верификация провалилась — второй аккаунт не создаём, снимаем этот канал |
| 2 | Бан за увод клиента с площадки (НП) | Поддержка внутри площадки | Никогда не переводить клиента площадки на прямую оплату |
| 3 | Не работает платёжный канал: нет Wise Business, PayPal не выводит на грузинский банк (НП) | Тестовые платежи $5–10 | Тест не дошёл до грузинского счёта — убираем канал из счетов |
| 4 | Споры и холды PayPal по услугам, споры до 180 дней (НП) | 50% предоплаты, письменная приёмка, только goods & services | Крупные суммы — только Wise или банк |
| 5 | Доход переквалифицируют в консалтинг; обратный НДС; разрешение на работу (НП) | Формулировка «разработка ПО», ответы бухгалтера и юриста | Ни одного счёта до регистрации ИП, статуса в кабинете rs.ge и письменных ответов на вопросы 1 и 3 (раздел 6.3) и миграционный вопрос (6.4) |
| 6 | WhatsApp: расходы на сообщения и бан номера (И / П) | WABA и карта клиента, opt-in, Telegram по умолчанию | Клиент не готов завести свой Business portfolio и карту — продаём только Telegram |
| 7 | Политика Meta об ИИ-ассистентах (П) | Бот только для записи и FAQ | Просьба сделать «ChatGPT в WhatsApp» — отказ |
| 8 | Gmail CASA (П) | Приложение в аккаунте каждого клиента | Никогда не держать мультиклиентское Gmail-приложение на своём проекте |
| 9 | Претензии из-за парсинга (НП) | Только публичные страницы, без обхода защиты | Логин, CAPTCHA, антибот или претензия — прекращаем сбор с этого сайта в тот же день |
| 10 | Нет заказов, 1–3 месяца до первого (НП) | Запас денег, три канала параллельно | 40 откликов на Upwork без интервью — переписать профиль, оффер и демо. 80 откликов — перенести время в прямые продажи. 8 недель без оплаченного заказа — сузиться до пакета inbox и одного сегмента, провести 10 интервью |
| 11 | Fiverr теряет покупателей, −21,9% (НП) | Fiverr — вспомогательный канал | — |
| 12 | Себестоимость клиента растёт (LLM, сообщения, VPS) | Haiku 5.5, лимиты LLM-операций, расходы по факту | Себестоимость больше 20% абонентки два месяца подряд — переводим на тариф выше или выставляем расходы по факту |
| 13 | Часы поддержки сверх тарифа | Учёт часов в журнале | Лимит превышен два месяца подряд — переводим на тариф выше |
| 14 | Лимит расходов на LLM API, ответ 429 (П) | Лимит в Console, переход на эвристики | — |
| 15 | Клиент или его аудитория в РФ или Беларуси | — | Отказ для пакетов с LLM |
| 16 | Данные о здоровье в боте клиники (НП) | Минимум данных, DPA | Клиника хочет хранить диагнозы — отказываемся от этой функции |
| 17 | Сервер клиента упал незаметно | Uptime Kuma на отдельном хосте, Healthchecks | — |
| 18 | Бесплатный тариф запрещает коммерцию (GitHub Pages — П; Vercel и UptimeRobot — НП) | Cloudflare для лендинга | — |

## 10. Источники

**Платежи**
- PayPal: [ROW-тарифы 28.09.2026](https://www.paypalobjects.com/marketing/ua/OA/merchant-fees/en-ROW-092826.pdf) · [тарифы 21.04.2025](https://www.paypalobjects.com/marketing/web/shared/clac/en/merchant-fees/paypal-merchant-fees-en-21-Apr-2025.pdf) · [GE fees](https://www.paypal.com/ge/webapps/mpp/paypal-fees) · [GE merchant fees](https://www.paypal.com/ge/webapps/mpp/merchant-fees) · [GE вывод](https://www.paypal.com/ge/cshelp/article/how-do-i-withdraw-money-to-my-bank-account-help394) · [GE инвойсы](https://www.paypal.com/ge/webapps/mpp/email-invoice) · [GE user agreement](https://www.paypal.com/ge/legalhub/paypal/useragreement-full) · [Payouts countries](https://developer.paypal.com/docs/payouts/standard/reference/country-feature/) · [country codes](https://developer.paypal.com/api/rest/reference/country-codes/)
- Payoneer: [annual fee](https://payoneer.custhelp.com/app/answers/detail/a_id/43738/~/annual-account-fee) · [pricing](https://www.payoneer.com/pricing/) · [fees](https://www.payoneer.com/fees/) · [уведомление 2021](https://payoneer.custhelp.com/app/answers/detail/a_id/36365/~/upcoming-changes-to-limits-and-fees-in-your-payoneer-account) · [recurring](https://payoneer.custhelp.com/app/answers/detail/a_id/31220/~/recurring-payments)
- Wise: [где можно жить](https://wise.com/help/articles/2813542/where-do-i-need-to-live-to-hold-money-with-wise) · [приём](https://wise.com/help/articles/2978013/what-are-the-fees-and-limits-for-receiving-money) · [GEL](https://wise.com/help/articles/2932350/guide-to-gel-transfers)
- Прочее: [Stripe global](https://stripe.com/global) · [cbw.ge, комиссия 1,5%](https://www.cbw.ge/banking/georgian-banks-to-charge-15-fee-on-lari-payments-abroad-starting-april-15) · [BOG Link Payment](https://api.bog.ge/docs/en/link-payment/introduction) · [Deel](https://help.letsdeel.com/) · [expat.com](https://www.expat.com/en/forum/asia/georgia/882227-paypal-business-account-fees-in-georgia.html)

**Upwork**
- [eligibility](https://support.upwork.com/hc/en-us/articles/211067778-Who-s-eligible-to-join-and-use-Upwork) · [sanctions](https://support.upwork.com/hc/en-us/articles/55329263532563-Sanctions-screening-on-Upwork) · [ID](https://support.upwork.com/hc/en-us/articles/211067788-How-to-verify-your-identity-with-a-government-ID-and-proof-of-address) · [private profile](https://support.upwork.com/hc/en-us/articles/115003975967-Why-was-my-profile-changed-to-private) · [Specialized Profiles](https://support.upwork.com/hc/en-us/articles/115013750068-Create-a-Specialized-Profile)
- Комиссии и вывод: [fee](https://support.upwork.com/hc/en-us/articles/211062538-Learn-about-the-Freelancer-Service-Fee) · [is Upwork free](https://www.upwork.com/resources/is-upwork-free) · [Georgia VAT](https://support.upwork.com/hc/en-us/articles/18521482302995-Georgia-VAT-for-freelancers) · [Connects](https://support.upwork.com/hc/en-us/articles/211062898-Understanding-and-using-Connects) · [Direct to Local Bank](https://support.upwork.com/hc/en-us/articles/211060578-What-are-the-fees-limits-and-timing-of-Direct-to-Local-Bank-payments) · [Direct to U.S. Bank](https://support.upwork.com/hc/en-us/articles/227022468-Direct-to-U-S-Bank-timing-and-fees-explained) · [digital bank](https://support.upwork.com/hc/en-us/articles/12928623064723-Use-a-digital-bank-to-get-paid-on-Upwork) · [wire](https://support.upwork.com/hc/en-us/articles/211063898-How-do-wire-transfers-work-on-Upwork) · [PayPal](https://support.upwork.com/hc/en-us/articles/211063978-What-are-the-PayPal-fees-and-timing-for-withdrawals) · [withdraw timing](https://support.upwork.com/hc/en-us/articles/40368585192723-When-can-I-withdraw-my-earnings)
- Контракты и правила: [Project Catalog](https://support.upwork.com/hc/en-us/articles/4408644453395-How-we-review-your-Project-Catalog-project) · [Conversion Fee](https://support.upwork.com/hc/en-us/articles/360043723533-What-is-the-Upwork-Conversion-Fee) · [до контракта](https://support.upwork.com/hc/en-us/articles/360052511833-Get-to-know-each-other-before-a-contract) · [recurring weekly](https://support.upwork.com/hc/en-us/articles/45819532067219-How-to-set-up-a-recurring-weekly-payment-for-your-freelancer) · [Direct Contracts](https://support.upwork.com/hc/en-us/articles/360025040794-Direct-Contracts-bring-a-client-to-Upwork) · [Contract Initiation Fee](https://support.upwork.com/hc/en-us/articles/26106318334611-What-is-the-Contract-Initiation-Fee-on-Upwork) · [prohibited jobs](https://support.upwork.com/hc/en-us/articles/17995622011667--What-kind-of-jobs-aren-t-allowed-on-Upwork) · [legal](https://www.upwork.com/legal)
- Спрос и цены: [In-Demand Skills 2026 (04.02.2026)](https://investors.upwork.com/news-releases/news-release-details/upworks-demand-skills-2026-demand-top-ai-skills-more-doubles-ai) · [Hiring Insights Aug 2026 (15.09.2026)](https://www.upwork.com/research/upwork-monthly-hiring-insights-august-2026) · [hire AI chatbot](https://www.upwork.com/hire/ai-chatbot-specialists/) · [листинг invoice extraction](https://www.upwork.com/services/product/development-it-invoice-extraction-accounting-sync-automation-n8n-make-com-1927043207117551724) · [UpHunt (08.2026)](https://uphunt.io/blog/do-ai-skills-pay-more-on-upwork-2026) · [Upwatcher](https://www.upwatcher.io/market/ai-automation/) · [getmany](https://getmany.com/blog/upwork-tips-for-beginners) · [gigradar](https://gigradar.io/blog/upwork-proposal-analytics-track-win-rate-response-rate-roi)

**Fiverr**
- Деньги: [earnings](https://help.fiverr.com/hc/en-us/articles/9234443621137-Your-earnings-page) · [buyer fee](https://help.fiverr.com/hc/en-us/articles/360050216133-Paying-for-orders-extras-or-custom-offers) · [for clients](https://help.fiverr.com/hc/en-us/articles/360010558038-How-Fiverr-works-for-clients) · [clearance](https://help.fiverr.com/hc/en-us/articles/34069565843985-How-Fiverr-works-for-freelancers) · [payouts](https://help.fiverr.com/hc/en-us/articles/360010530058-Withdrawing-your-earnings-managing-payout-methods) · [payment methods](https://help.fiverr.com/hc/en-us/articles/37332701180945-Payment-methods)
- Аккаунт и продвижение: [verification](https://help.fiverr.com/hc/it/articles/22582440968849-Verifica-delle-informazioni-personali-e-aziendali) · [Seller Plus](https://help.fiverr.com/hc/en-us/articles/360017140717-Seller-Plus-Standard-and-Premium-Advanced-tools-for-business-growth) · [Ads](https://help.fiverr.com/hc/en-us/articles/360017729338-Promoting-your-Gigs-with-Fiverr-Ads) · [levels](https://help.fiverr.com/hc/en-us/articles/360010560118-Understanding-Fiverr-s-freelancer-levels) · [search](https://help.fiverr.com/hc/en-us/articles/23429542870161-Fiverr-s-search-and-recommendation-system)
- Заказы и правила: [subscriptions](https://help.fiverr.com/hc/en-us/articles/33272731177105-Subscription-orders-for-freelancers) · [hourly](https://help.fiverr.com/hc/en-us/articles/27516246556945-Hourly-work-for-freelancers) · [milestones](https://help.fiverr.com/hc/en-us/articles/360010560178-Working-with-Milestones) · [off-platform](https://help.fiverr.com/hc/en-us/articles/12792122691601-Stay-protected-Fiverr-s-off-platform-policy) · [AI](https://help.fiverr.com/hc/en-us/articles/34998793899665-Using-AI-on-Fiverr-Guidelines-for-freelancers-and-clients) · [prohibited](https://help.fiverr.com/hc/en-us/articles/49174165608593-Prohibited-services-on-Fiverr)
- Рынок: [6-K Q2 2026 (29.07.2026)](https://www.sec.gov/Archives/edgar/data/1762301/000117891326003624/exhibit_99-1.htm) · [BTI Fall 2025 (10.12.2025)](https://www.fiverr.com/news/2025-fall-business-trends-index) · [BTI 09.06.2026](https://aimagazine.com/globenewswire/3308866) · [cost guide](https://fiverr.com/resources/guides/costs/chatbot-developer) · [web scraping](https://www.fiverr.com/hire/web-scraping) · [n8n gigs](https://www.fiverr.com/gigs/n8n) · [unil.ink](https://unil.ink/blog/how-to-make-money-on-fiverr-2026)

**Техника**
- Telegram: [FAQ](https://core.telegram.org/bots/faq) · [bots](https://core.telegram.org/bots) · [API](https://core.telegram.org/bots/api) · [changelog](https://core.telegram.org/bots/api-changelog)
- WhatsApp, цены и лимиты: [pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing) · [non-template](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages) · [AI providers](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/ai-providers) · [регионы](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/conversation-based-pricing) · [limits](https://developers.facebook.com/documentation/business-messaging/whatsapp/messaging-limits) · [throughput](https://developers.facebook.com/documentation/business-messaging/whatsapp/throughput) · [Twilio](https://help.twilio.com/articles/53100480177819) · [TechCrunch](https://techcrunch.com/2025/10/18/whatssapp-changes-its-terms-to-bar-general-purpose-chatbots-from-its-platform/)
- WhatsApp, подключение и аккаунты: [migrate](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/migrate-existing-whatsapp-number-to-a-business-account) · [coexistence](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users) · [ES overview](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview) · [tech provider](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/get-started-for-tech-providers) · [opt-in](https://developers.facebook.com/documentation/business-messaging/whatsapp/getting-opt-in) · [numbers](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/phone-numbers) · [account model](https://developers.facebook.com/documentation/business-messaging/whatsapp/account-model-evolution) · [BSUID](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids) · [Business Agent](https://developers.facebook.com/documentation/business-messaging/whatsapp/about-the-platform)
- 360dialog и Instagram: [360dialog pricing](https://docs.360dialog.com/docs/get-started/pricing) · [invoices](https://docs.360dialog.com/docs/hub/invoices) · [coexistence](https://docs.360dialog.com/docs/resources/phone-numbers/coexistence) · [IG app review](https://developers.facebook.com/documentation/instagram-platform/app-review)
- Google и Microsoft: [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes) · [restricted](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification) · [OAuth overview](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview) · [OAuth2](https://developers.google.com/identity/protocols/oauth2) · [policies](https://developers.google.com/identity/protocols/oauth2/policies) · [LSA](https://support.google.com/a/users/answer/11339703) · [XOAUTH2](https://developers.google.com/workspace/gmail/imap/xoauth2-protocol) · [Apps Script](https://developers.google.com/apps-script/guides/client-verification) · [API controls](https://support.google.com/a/answer/7281227) · [app passwords](https://support.google.com/accounts/answer/185833) · [User Data Policy](https://developers.google.com/terms/api-services-user-data-policy) · [Sheets limits](https://developers.google.com/workspace/sheets/api/limits) · [Calendar](https://developers.google.com/workspace/calendar/api/releases) · [Graph](https://learn.microsoft.com/en-us/graph/permissions-reference)
- LLM: [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing) · [effort](https://platform.claude.com/docs/en/build-with-claude/effort) · [thinking cost](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost) · [rate limits](https://platform.claude.com/docs/en/api/rate-limits) · [retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) · [commercial terms](https://www.anthropic.com/legal/commercial-terms) · [оплата API](https://support.claude.com/en/articles/8977456-how-do-i-pay-for-my-claude-api-usage) · [Anthropic countries](https://www.anthropic.com/supported-countries) · [OpenAI countries](https://developers.openai.com/api/docs/supported-countries) · [OpenAI data](https://developers.openai.com/api/docs/guides/your-data) · [gpt-5.4-mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
- n8n и право: [n8n FAQ](https://github.com/n8n-io/n8n-docs/blob/main/docs/n8n-community-license/license-faq.md) · [n8n LICENSE](https://raw.githubusercontent.com/n8n-io/n8n/master/LICENSE.md) · [hiQ](https://cdn.ca9.uscourts.gov/datastore/opinions/2022/04/18/17-16783.pdf) · [Bright Data](https://www.fbm.com/post/102kqw7/) · [Ryanair](https://curia.europa.eu/juris/liste.jsf?num=C-30/14) · [Orrick TCPA](https://www.orrick.com/en/Insights/2024/02/Did-the-FCC-Really-Ban-AI-Generated-Voice-Calls-No-But-Restrictions-Apply)
- Рынок ботов и голоса: [ainora](https://ainora.lt/blog/ai-voice-agent-cost-per-minute-2026) · [biztoolkit](https://www.biztoolkit.co/post/freelance-chatbot-developer-rates-in-2026-ai-pricing) · [achiya](https://achiya-automation.com/en/industries/clinics/)

**Налоги и юр. оформление**
- [Налоговый кодекс](https://matsne.gov.ge/en/document/view/1043717) · [matsne](https://matsne.gov.ge/) · [закон о персональных данных](https://matsne.gov.ge/en/document/view/5827307) · [personaldata.ge](https://personaldata.ge/en) · [Andersen](https://ge.andersen.com/1-tax-regime/)
- [NAPR](https://napr.gov.ge/) · [PSH](https://psh.gov.ge/) · [rs.ge](https://www.rs.ge/) · [NBG](https://nbg.gov.ge/en/monetary-policy/currency) · [pensions.ge](https://www.pensions.ge/)
- [TBC](https://www.tbcbank.ge/) · [BoG](https://bankofgeorgia.ge/) · [nalog.gov.ru](https://www.nalog.gov.ru/) · [W-8BEN](https://www.irs.gov/forms-pubs/about-form-w-8-ben)

**Инфраструктура**
- Cloudflare: [Pages limits](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/pages/platform/limits.mdx) · [Workers limits](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/workers/platform/limits.mdx) · [registrar FAQ](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/registrar/faq.mdx) · [billing FAQ](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/billing/understand/faq.mdx) · [Email pricing](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/email-service/platform/pricing.mdx) · [Email limits](https://github.com/cloudflare/cloudflare-docs/blob/production/src/content/docs/email-service/platform/limits.mdx) · [plans](https://www.cloudflare.com/plans/) · [registrar](https://www.cloudflare.com/products/registrar/)
- Хостинг: [GitHub Pages](https://github.com/github/docs/blob/main/content/pages/getting-started-with-github-pages/github-pages-limits.md) · [Vercel](https://vercel.com/docs/limits/fair-use-guidelines#commercial-usage) · [Hetzner](https://www.hetzner.com/cloud) · [Hetzner verification](https://docs.hetzner.com/accounts-panel/accounts/account-verification/) · [DigitalOcean](https://www.digitalocean.com/pricing/droplets) · [Oracle Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- Сервисы: [Zoho](https://www.zoho.com/mail/zohomail-pricing.html) · [Gmail send as](https://support.google.com/mail/answer/22370) · [UptimeRobot](https://uptimerobot.com/pricing/) · [Healthchecks](https://github.com/healthchecks/healthchecks/blob/master/hc/accounts/models.py) · [Loom](https://www.loom.com/pricing) · [Cal.com](https://cal.com/pricing)

**Локальный рынок**
- [NapoleonCat](https://napoleoncat.com/stats/messenger-users-in-georgia/2024/03) · [StatCounter](https://gs.statcounter.com/social-media-stats/all/georgia) · [Geostat I кв. 2026](https://geostat.ge/media/80106/Average-Monthly-Nominal-Earnings-of-Employees---%28I-Quarter%2C-2026%29.pdf) · [NBG-агрегатор](https://allratestoday.com/central-bank-rates-api/nbg.md) · [CRRC](https://caucasusbarometer.org/en/) · [OSW](https://www.osw.waw.pl/en)
- [Altegio API](https://developer.alteg.io/_bundle/en/developers/openapi.json) · [Altegio pricing](https://alteg.io/en/pricing/) · [Fresha](https://www.fresha.com/pricing) · [DIKIDI](https://dikidi.net)
- [WhatsApp policy](https://business.whatsapp.com/policy) · [yell.ge](https://www.yell.ge) · [GITA](https://gita.gov.ge) · [ss.ge](https://ss.ge)
