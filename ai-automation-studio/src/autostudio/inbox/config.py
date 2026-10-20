"""Конфиг клиента пакета inbox: ящик, категории, профиль компании для черновиков.

Секретов здесь нет: логин и пароль приложения IMAP лежат в `.env`, конфиг называет
только имена переменных. Категории и факты о компании — данные клиента, код по ним
не ветвится: всё, что отличает одного клиента от другого, живёт в этом файле.
"""

import re
from typing import Final, Literal

import msgspec

from autostudio.clientconf import ClientInfo, NotifyConfig, SinkConfig
from autostudio.errors import ConfigError

type Urgency = Literal['low', 'normal', 'high']

URGENCY_RANK: Final[dict[str, int]] = {'low': 0, 'normal': 1, 'high': 2}

_CATEGORY_ID: Final = re.compile(r'^[a-z][a-z0-9_]{1,39}$')
_LANGUAGE: Final = re.compile(r'^[a-z]{2}$')
_ADDRESS: Final = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
# Категория `other` обязательна: туда код отправляет всё, что модель назвала
# несуществующей категорией, и автоответы — без неё некуда деть «непонятное».
FALLBACK_CATEGORY: Final = 'other'


class Category(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Категория письма и что с ним делать.

    Attributes:
        id: Код категории (`lead`, `booking_request`): его видит модель и таблица.
        description: Описание для модели — по нему она выбирает категорию.
        notify: Это «возможность» (лид, заявка): считать в отчёте и сообщать владельцу,
            если срочность не ниже `notify_on_urgency`.
        draft_reply: Готовить черновик ответа, если письмо ждёт ответа.
        move_to: Папка (ярлык Gmail), куда скопировать письмо, или None. Письмо из
            исходной папки не удаляется никогда.
    """

    id: str
    description: str
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


def default_categories() -> list[Category]:
    """Набор категорий по умолчанию — подходит большинству сервисных бизнесов."""
    return [
        Category(
            id='lead',
            description='A potential new customer asks about prices, availability, a quote or wants to buy or book.',
            notify=True,
            draft_reply=True,
        ),
        Category(
            id='support',
            description='An existing customer has a question, problem, complaint, change or refund request.',
            draft_reply=True,
        ),
        Category(
            id='invoice',
            description='Invoices, bills, payment requests, receipts or payment confirmations '
            'from suppliers or partners.',
        ),
        Category(
            id='marketing',
            description='Newsletters, promotions, cold sales pitches, SEO or advertising offers and other bulk mail.',
        ),
        Category(
            id=FALLBACK_CATEGORY,
            description='Anything else: personal, internal, service notifications, unclear messages.',
        ),
    ]


class CompanyProfile(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Что черновик может говорить от имени компании.

    Attributes:
        name: Название компании в подписи и в поле From.
        signature: Подпись под черновиком; пусто — только название.
        tone: Тон ответа своими словами (`warm, concise, professional`).
        facts: ЕДИНСТВЕННЫЕ факты, которые черновик вправе утверждать: цены, как
            забронировать, часы работы. Чего здесь нет, того черновик не обещает.
        reply_language: `auto` — язык отправителя, иначе код языка (`en`, `ru`).
    """

    name: str
    signature: str = ''
    tone: str = 'warm, concise and professional'
    facts: list[str] = msgspec.field(default_factory=list)
    reply_language: str = 'auto'

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ConfigError('inbox.company.name: нужно название компании')
        if any(not fact.strip() for fact in self.facts):
            raise ConfigError('inbox.company.facts: пустой факт — уберите его')
        if self.reply_language != 'auto' and not _LANGUAGE.match(self.reply_language):
            raise ConfigError(f'inbox.company.reply_language={self.reply_language!r}: `auto` или код языка (en, ru)')

    @property
    def sign_off(self) -> str:
        """Подпись, которой код заканчивает каждый черновик."""
        return self.signature.strip() or self.name.strip()


class InboxSection(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Секция `[inbox]`: ящик, лимиты, категории и правила действий.

    Attributes:
        imap_host: IMAP-сервер (`imap.gmail.com`, `imap.yandex.ru`, `imap.zoho.eu`).
        imap_port: Порт IMAP по SSL.
        username_env: Переменная окружения с логином ящика.
        password_env: Переменная с паролем приложения (не основным паролем!).
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
    """

    imap_host: str
    imap_port: int = 993
    username_env: str = 'INBOX_IMAP_USER'
    password_env: str = 'INBOX_IMAP_PASSWORD'  # noqa: S105 - имя переменной, а не секрет
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

    def __post_init__(self) -> None:
        if not self.imap_host.strip():
            raise ConfigError('inbox.imap_host: нужен адрес IMAP-сервера')
        if not 0 < self.imap_port < 65536:
            raise ConfigError(f'inbox.imap_port={self.imap_port}: порт 1–65535')
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


def _check_folder(name: str, where: str) -> None:
    # Имя папки уходит в команду IMAP: перевод строки в нём — это внедрение команды.
    if not name.strip() or any(ch in name for ch in '\r\n\0'):
        raise ConfigError(f'{where}={name!r}: пустое имя папки или управляющие символы')
