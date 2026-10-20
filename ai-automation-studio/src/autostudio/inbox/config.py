"""Конфиг клиента пакета inbox: ящик, вход, категории, счета, профиль компании для черновиков.

Секретов здесь нет: пароль приложения или OAuth-ключи лежат в `.env`, конфиг называет
только имена переменных. Категории и факты о компании — данные клиента, код по ним
не ветвится: всё, что отличает одного клиента от другого, живёт в этом файле.
"""

import re
from typing import Final, Literal

import msgspec
import phonenumbers

from autostudio.clientconf import ClientInfo, NotifyConfig, SinkConfig
from autostudio.errors import ConfigError

type Urgency = Literal['low', 'normal', 'high']
# Как входить в ящик: пароль приложения (личный Gmail, Яндекс, Zoho), OAuth Google Workspace
# (Internal-клиент в GCP-проекте организации клиента) или OAuth Microsoft 365 (приложение в
# тенанте клиента, вход без пользователя). Подробности — docs/packages/inbox.md.
type ImapAuth = Literal['password', 'google_oauth', 'microsoft_oauth']

URGENCY_RANK: Final[dict[str, int]] = {'low': 0, 'normal': 1, 'high': 2}

_CATEGORY_ID: Final = re.compile(r'^[a-z][a-z0-9_]{1,39}$')
_LANGUAGE: Final = re.compile(r'^[a-z]{2}$')
_ADDRESS: Final = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
_ENV_NAME: Final = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,99}$')
# Счёт в письме — обычно 50–500 КБ. Верхняя граница держит PDF целиком внутри частичной
# выборки письма (`imap.MAX_FETCH_BYTES`, base64 раздувает вложение на треть).
_PDF_KB_MAX: Final = 3500
# Категория `other` обязательна: туда код отправляет всё, что модель назвала
# несуществующей категорией, и автоответы — без неё некуда деть «непонятное».
FALLBACK_CATEGORY: Final = 'other'


class Category(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Категория письма и что с ним делать.

    Attributes:
        id: Код категории (`lead`, `booking_request`): его видит модель и таблица.
        description: Описание для модели — по нему она выбирает категорию.
        label: Название для владельца по языкам (`{en = "Booking request", ru = "Заявка на бронь"}`);
            без него показывается id с заглавной буквы.
        lead: Это «возможность» (лид, заявка): считается в отчёте как найденный лид. Если не
            задано — совпадает с `notify` (так вели себя конфиги до появления поля).
        notify: Сообщать владельцу о таком письме, если срочность не ниже `notify_on_urgency`.
        draft_reply: Готовить черновик ответа, если письмо ждёт ответа.
        move_to: Папка (ярлык Gmail), куда скопировать письмо, или None. Письмо из
            исходной папки не удаляется никогда.
    """

    id: str
    description: str
    label: dict[str, str] = msgspec.field(default_factory=dict)
    lead: bool | None = None
    notify: bool = False
    draft_reply: bool = False
    move_to: str | None = None

    def __post_init__(self) -> None:
        if not _CATEGORY_ID.match(self.id):
            raise ConfigError(f'категория {self.id!r}: только a-z, 0-9 и _, начинается с буквы, 2–40 символов')
        if not self.description.strip():
            raise ConfigError(f'категория {self.id!r}: нужно описание — по нему модель выбирает категорию')
        if self.move_to is not None:
            _check_folder(self.move_to, f'categories[{self.id}].move_to')
        _check_languages(self.label, f'categories[{self.id}].label')

    @property
    def is_lead(self) -> bool:
        """Считать ли письмо этой категории лидом (в отчёте и в итогах запуска)."""
        return self.notify if self.lead is None else self.lead

    def title(self, language: str) -> str:
        """Название категории для владельца: подпись на его языке, иначе английская, иначе id.

        Args:
            language: Язык владельца.

        Returns:
            Название без служебных подчёркиваний.
        """
        label = self.label.get(language) or self.label.get('en')
        return label.strip() if label else self.id.replace('_', ' ').capitalize()


def default_categories() -> list[Category]:
    """Набор категорий по умолчанию — подходит большинству сервисных бизнесов."""
    return [
        Category(
            id='lead',
            description='A potential new customer asks about prices, availability, a quote or wants to buy or book.',
            label={'en': 'New inquiry', 'ru': 'Новый запрос'},
            lead=True,
            notify=True,
            draft_reply=True,
        ),
        Category(
            id='support',
            description='An existing customer has a question, problem, complaint, change or refund request.',
            label={'en': 'Customer support', 'ru': 'Вопрос клиента'},
            draft_reply=True,
        ),
        Category(
            id='invoice',
            description='Invoices, bills, payment requests, receipts or payment confirmations '
            'from suppliers or partners.',
            label={'en': 'Invoice', 'ru': 'Счёт'},
        ),
        Category(
            id='marketing',
            description='Newsletters, promotions, cold sales pitches, SEO or advertising offers and other bulk mail.',
            label={'en': 'Newsletter or promotion', 'ru': 'Рассылка или реклама'},
        ),
        Category(
            id=FALLBACK_CATEGORY,
            description='Anything else: personal, internal, service notifications, unclear messages.',
            label={'en': 'Other', 'ru': 'Прочее'},
        ),
    ]


class CompanyProfile(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Что черновик может говорить от имени компании.

    Attributes:
        name: Название компании в подписи и в поле From.
        signature: Подпись под черновиком; пусто — только название.
        signatures: Подписи на других языках (`{ru = "С уважением, …"}`): черновик на русском
            не должен заканчиваться английским «Best regards».
        tone: Тон ответа своими словами (`warm, concise, professional`).
        facts: ЕДИНСТВЕННЫЕ факты, которые черновик вправе утверждать: цены, как
            забронировать, часы работы. Чего здесь нет, того черновик не обещает.
        facts_by_language: Те же факты на других языках (`{ru = [...]}`); для черновика на
            этом языке берутся они — модели не приходится переводить цены и условия.
        reply_language: `auto` — язык отправителя, иначе код языка (`en`, `ru`).
    """

    name: str
    signature: str = ''
    signatures: dict[str, str] = msgspec.field(default_factory=dict)
    tone: str = 'warm, concise and professional'
    facts: list[str] = msgspec.field(default_factory=list)
    facts_by_language: dict[str, list[str]] = msgspec.field(default_factory=dict)
    reply_language: str = 'auto'

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigError('inbox.company.name: нужно название компании')
        if any(not fact.strip() for fact in self.facts):
            raise ConfigError('inbox.company.facts: пустой факт — уберите его')
        if self.reply_language != 'auto' and not _LANGUAGE.match(self.reply_language):
            raise ConfigError(f'inbox.company.reply_language={self.reply_language!r}: `auto` или код языка (en, ru)')
        _check_languages(self.signatures, 'inbox.company.signatures')
        for language, facts in self.facts_by_language.items():
            if not _LANGUAGE.match(language):
                raise ConfigError(f'inbox.company.facts_by_language: {language!r} — нужен код языка (en, ru)')
            if not facts or any(not fact.strip() for fact in facts):
                raise ConfigError(f'inbox.company.facts_by_language.{language}: пустой список или пустой факт')

    @property
    def sign_off(self) -> str:
        """Подпись по умолчанию, которой код заканчивает черновик."""
        return self.signature.strip() or self.name.strip()

    def sign_off_for(self, language: str) -> str:
        """Подпись черновика на языке ответа (если задана), иначе подпись по умолчанию."""
        return self.signatures.get(language, '').strip() or self.sign_off

    def facts_for(self, language: str) -> list[str]:
        """Факты для черновика на языке ответа: переведённые, если есть, иначе основные."""
        return self.facts_by_language.get(language) or self.facts

    @property
    def all_facts(self) -> list[str]:
        """Все факты на всех языках — по ним проверяются числа в черновике."""
        return [*self.facts, *(fact for facts in self.facts_by_language.values() for fact in facts)]


class InvoiceSection(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Секция `[inbox.invoices]`: извлечение счетов из PDF-вложений.

    Модель читает текстовый слой PDF и предлагает поля счёта, код их сверяет: итог должен быть
    в тексте PDF, строки — сходиться с итогом. Сканы без текстового слоя (OCR в v1 нет) и всё,
    что не сошлось, попадают в таблицу с пометкой «не проверено», а не как проверенные числа.

    Attributes:
        enabled: Извлекать счета. По умолчанию выключено, как черновики и сигналы: это функция
            тарифов Standard и Premium, и обновление не должно молча добавить клиенту Basic
            вызовы модели и новую вкладку.
        categories: Категории писем со счетами; None — категория `invoice`, если она есть.
        max_pdf_kb: PDF больше этого размера не разбирается (пометка «разобрать вручную»).
        max_pages: PDF длиннее не разбирается: счёт на 30 страниц — это уже не счёт, а отчёт.
        max_text_chars: Сколько символов текста PDF видит модель.
        sheet_tab: Вкладка таблицы со счетами.
    """

    enabled: bool = False
    categories: list[str] | None = None
    max_pdf_kb: int = 2048
    max_pages: int = 10
    max_text_chars: int = 20_000
    sheet_tab: str = 'Invoices'

    def __post_init__(self) -> None:
        if not 50 <= self.max_pdf_kb <= _PDF_KB_MAX:
            raise ConfigError(f'inbox.invoices.max_pdf_kb: от 50 до {_PDF_KB_MAX} КБ')
        if not 1 <= self.max_pages <= 50:
            raise ConfigError('inbox.invoices.max_pages: от 1 до 50 страниц')
        if not 2000 <= self.max_text_chars <= 100_000:
            raise ConfigError('inbox.invoices.max_text_chars: от 2000 до 100000 символов')
        if not self.sheet_tab.strip():
            raise ConfigError('inbox.invoices.sheet_tab: нужно имя вкладки')


class InboxSection(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Секция `[inbox]`: ящик, лимиты, категории и правила действий.

    Attributes:
        imap_host: IMAP-сервер (`imap.gmail.com`, `outlook.office365.com`, `imap.yandex.ru`).
        imap_port: Порт IMAP по SSL.
        auth: Способ входа: `password`, `google_oauth` или `microsoft_oauth`.
        username_env: Переменная окружения с логином ящика (при OAuth — адрес ящика).
        password_env: Переменная с паролем приложения (не основным паролем!) для `password`.
        oauth_client_id_env: Переменная с client ID OAuth-клиента (Google или Microsoft).
        oauth_client_secret_env: Переменная с client secret.
        oauth_refresh_token_env: Переменная с refresh token Google (`autostudio inbox oauth-google`).
        oauth_tenant_id_env: Переменная с ID тенанта Microsoft 365 (GUID или домен).
        folder: Папка, которую разбираем.
        drafts_folder: Папка черновиков. В Gmail — `[Gmail]/Drafts`, в локализованном
            интерфейсе имя может быть другим; `autostudio inbox check --connect` подскажет.
        from_address: Адрес в поле From черновиков; по умолчанию — логин IMAP.
        first_run_since_days: При первом запуске берём письма только за эти дни.
        max_per_run: Сколько писем разбирать за запуск — потолок расходов на модель.
        max_body_chars: Сколько символов текста письма видит модель.
        timeout_seconds: Таймаут сетевых операций IMAP.
        categories: Категории; `other` обязательна.
        bulk_category: Куда без модели кладётся рассылка (List-Unsubscribe, Precedence: bulk).
        auto_reply_category: Куда без модели кладутся автоответы и уведомления о недоставке.
        company: Профиль компании для черновиков.
        notify_on_urgency: Минимальная срочность лида для сообщения владельцу.
        mask_pii_in_sheet: Маскировать адрес, имя и телефон отправителя в таблице.
        alert_operator_on_injection: Сообщать оператору о письмах с подозрением на prompt-инъекцию.
        sheet_tab: Вкладка таблицы.
        phone_region: Регион для номеров без кода страны (`599 12 34 56` → +995…): ISO 3166-1.
        phone_fallback_regions: Регионы для номеров, которых нет в `phone_region`; E.164 из них — только
            если запись сама называет страну (`8 (916) …`, `+7 …`).
        invoices: Извлечение счетов из PDF.
    """

    imap_host: str
    imap_port: int = 993
    auth: ImapAuth = 'password'
    username_env: str = 'INBOX_IMAP_USER'
    password_env: str = 'INBOX_IMAP_PASSWORD'  # noqa: S105 - имя переменной, а не секрет
    oauth_client_id_env: str = 'INBOX_OAUTH_CLIENT_ID'
    oauth_client_secret_env: str = 'INBOX_OAUTH_CLIENT_SECRET'  # noqa: S105 - имя переменной
    oauth_refresh_token_env: str = 'INBOX_OAUTH_REFRESH_TOKEN'  # noqa: S105 - имя переменной
    oauth_tenant_id_env: str = 'INBOX_OAUTH_TENANT_ID'
    folder: str = 'INBOX'
    drafts_folder: str = 'Drafts'
    from_address: str | None = None
    first_run_since_days: int = 3
    max_per_run: int = 50
    max_body_chars: int = 8000
    timeout_seconds: float = 30.0
    categories: list[Category] = msgspec.field(default_factory=default_categories)
    bulk_category: str = 'marketing'
    auto_reply_category: str = FALLBACK_CATEGORY
    company: CompanyProfile
    notify_on_urgency: Urgency = 'high'
    mask_pii_in_sheet: bool = False
    alert_operator_on_injection: bool = True
    sheet_tab: str = 'Inbox'
    phone_region: str = 'GE'
    # Русскоязычные гости пишут грузинскому бизнесу номер по-домашнему: «8 (916) 123-45-67».
    # В регионе GE такого номера нет, и без запасного региона телефон честного лида терялся бы.
    # Запасной регион берётся только для номеров, которых нет в `phone_region`, и подтверждает лишь
    # то, что номер есть в письме. E.164 из него — только когда запись сама называет страну («8 …»,
    # «+7 …»): «(917) 555-0134» туриста из США — тоже валидный российский номер.
    phone_fallback_regions: list[str] = msgspec.field(default_factory=lambda: ['RU'])
    invoices: InvoiceSection = InvoiceSection()

    def __post_init__(self) -> None:
        if not self.imap_host.strip():
            raise ConfigError('inbox.imap_host: нужен адрес IMAP-сервера')
        if not 0 < self.imap_port < 65536:
            raise ConfigError(f'inbox.imap_port={self.imap_port}: порт 1–65535')
        for name, value in self._env_names():
            if not _ENV_NAME.match(value):
                raise ConfigError(f'inbox.{name}={value!r}: нужно имя переменной окружения')
        for region in (self.phone_region, *self.phone_fallback_regions):
            if region not in phonenumbers.SUPPORTED_REGIONS:
                raise ConfigError(f'inbox.phone_region: {region!r} — нужен код страны ISO 3166-1 (GE, RU, DE)')
        _check_folder(self.folder, 'inbox.folder')
        _check_folder(self.drafts_folder, 'inbox.drafts_folder')
        if self.from_address is not None and not _ADDRESS.match(self.from_address):
            raise ConfigError(f'inbox.from_address={self.from_address!r}: нужен адрес вида name@example.com')
        if self.drafts_folder == self.folder:
            raise ConfigError('inbox.drafts_folder совпадает с inbox.folder: черновики разбирались бы как входящие')
        if not 0 <= self.first_run_since_days <= 60:
            raise ConfigError('inbox.first_run_since_days: от 0 до 60 дней')
        if not 1 <= self.max_per_run <= 500:
            raise ConfigError('inbox.max_per_run: от 1 до 500 писем за запуск')
        if not 500 <= self.max_body_chars <= 100_000:
            raise ConfigError('inbox.max_body_chars: от 500 до 100000 символов')
        if not 1 <= self.timeout_seconds <= 300:
            raise ConfigError('inbox.timeout_seconds: от 1 до 300 секунд')
        if not self.sheet_tab.strip():
            raise ConfigError('inbox.sheet_tab: нужно имя вкладки')
        ids = [category.id for category in self.categories]
        duplicates = sorted({cid for cid in ids if ids.count(cid) > 1})
        if duplicates:
            raise ConfigError(f'inbox.categories: повторяются id {", ".join(duplicates)}')
        if FALLBACK_CATEGORY not in ids:
            raise ConfigError(f'inbox.categories: нужна категория {FALLBACK_CATEGORY!r} — туда идёт всё непонятное')
        for name, value in (('bulk_category', self.bulk_category), ('auto_reply_category', self.auto_reply_category)):
            if value not in ids:
                raise ConfigError(f'inbox.{name}={value!r}: такой категории нет в inbox.categories')
        for category in self.categories:
            if category.move_to is not None and category.move_to in (self.folder, self.drafts_folder):
                raise ConfigError(f'categories[{category.id}].move_to: нельзя копировать в разбираемую папку')
        for category_id in self.invoices.categories or ():
            if category_id not in ids:
                raise ConfigError(f'inbox.invoices.categories: категории {category_id!r} нет в inbox.categories')
        if self.invoices.enabled and self.invoices.sheet_tab == self.sheet_tab:
            raise ConfigError('inbox.invoices.sheet_tab совпадает с inbox.sheet_tab: у счетов свои колонки')

    def _env_names(self) -> list[tuple[str, str]]:
        return [
            ('username_env', self.username_env),
            ('password_env', self.password_env),
            ('oauth_client_id_env', self.oauth_client_id_env),
            ('oauth_client_secret_env', self.oauth_client_secret_env),
            ('oauth_refresh_token_env', self.oauth_refresh_token_env),
            ('oauth_tenant_id_env', self.oauth_tenant_id_env),
        ]

    @property
    def auth_env(self) -> list[str]:
        """Переменные окружения, без которых вход в ящик выбранным способом невозможен."""
        match self.auth:
            case 'password':
                return [self.username_env, self.password_env]
            case 'google_oauth':
                return [
                    self.username_env,
                    self.oauth_client_id_env,
                    self.oauth_client_secret_env,
                    self.oauth_refresh_token_env,
                ]
            case 'microsoft_oauth':
                return [
                    self.username_env,
                    self.oauth_tenant_id_env,
                    self.oauth_client_id_env,
                    self.oauth_client_secret_env,
                ]

    @property
    def invoice_category_ids(self) -> list[str]:
        """Категории, письма которых проверяются на PDF-счета (пусто — извлечение выключено)."""
        if not self.invoices.enabled:
            return []
        if self.invoices.categories is not None:
            return list(self.invoices.categories)
        return [category_id for category_id in ('invoice',) if category_id in self.category_ids]

    @property
    def phone_regions(self) -> list[str]:
        """Регионы разбора телефонов: основной, затем запасные (без повторов)."""
        return list(dict.fromkeys([self.phone_region, *self.phone_fallback_regions]))

    def category(self, category_id: str) -> Category:
        """Категория по id; неизвестный id — категория `other`.

        Args:
            category_id: Код категории.

        Returns:
            Найденная категория.
        """
        by_id = {category.id: category for category in self.categories}
        return by_id.get(category_id) or by_id[FALLBACK_CATEGORY]

    @property
    def category_ids(self) -> list[str]:
        return [category.id for category in self.categories]


class InboxConfig(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Полный конфиг клиента пакета inbox (TOML-файл)."""

    client: ClientInfo
    notify: NotifyConfig = NotifyConfig()
    sink: SinkConfig = SinkConfig()
    inbox: InboxSection


def _check_languages(values: dict[str, str], where: str) -> None:
    for language, value in values.items():
        if not _LANGUAGE.match(language):
            raise ConfigError(f'{where}: {language!r} — нужен код языка (en, ru)')
        if not value.strip():
            raise ConfigError(f'{where}.{language}: пустое значение')


def _check_folder(name: str, where: str) -> None:
    # Имя папки уходит в команду IMAP: перевод строки в нём — это внедрение команды.
    if not name.strip() or any(ch in name for ch in '\r\n\0'):
        raise ConfigError(f'{where}={name!r}: пустое имя папки или управляющие символы')
