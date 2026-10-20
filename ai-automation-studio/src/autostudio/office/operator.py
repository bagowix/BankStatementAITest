"""Профиль оператора: реквизиты ИП для счетов и договоров из `operator.toml`.

Файл с настоящими реквизитами лежит в корне репозитория и в .gitignore: репозиторий
зеркалируется в публичный, а IBAN и адрес ИП туда попадать не должны. В git — только
`operator.example.toml` с вымышленными данными. Значения-образцы из него («Example»,
«Пример», example.com, ID из одних нулей, SWIFT `XXXX…`) профиль не принимает: иначе
скопированный образец, где заменили только часть полей, печатал бы чужое имя в счетах.
"""

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import msgspec

from autostudio.errors import ConfigError
from autostudio.office.money import InvoiceCurrency, Language
from autostudio.office.paths import OPERATOR_FILE, project_root

OPERATOR_ENV: Final = 'AUTOSTUDIO_OPERATOR'

# Длины IBAN по реестру SWIFT для стран, откуда реально придут деньги; остальные
# проверяются общим правилом ISO 13616 (15–34 символа) и контрольной суммой.
_IBAN_LENGTHS: Final[Mapping[str, int]] = {
    'GE': 22,
    'DE': 22,
    'GB': 22,
    'BE': 16,
    'NL': 18,
    'FR': 27,
    'LT': 20,
    'EE': 20,
    'LV': 21,
    'PL': 28,
    'AT': 20,
    'CH': 21,
    'TR': 26,
    'AE': 23,
    'CY': 28,
}
_IBAN_SHAPE: Final = re.compile(r'^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$')
# Грузинский IBAN: GE, 2 контрольные цифры, 2 буквы кода банка, 16 цифр счёта.
_IBAN_GE: Final = re.compile(r'^GE\d{2}[A-Z]{2}\d{16}$')
_MOD97_OK: Final = 1
_IBAN_MOD: Final = 97
# BIC (ISO 9362): 4 буквы банка, 2 буквы страны, 2 символа места, необязательно 3 символа филиала.
_SWIFT: Final = re.compile(r'^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$')
_ID_NUMBER: Final = re.compile(r'^\d{9,11}$')
_EMAIL: Final = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_PREFIX: Final = re.compile(r'^[A-Z0-9][A-Z0-9-]{0,11}$')
_TELEGRAM: Final = re.compile(r'^[A-Za-z0-9_]{5,32}$')
_FIELD_KEY: Final = re.compile(r'^[A-Z][A-Z0-9_]*$')
_IE_PREFIXES: Final = ('Individual Entrepreneur ', 'Индивидуальный предприниматель ', 'ИП ')
# Признаки образца из operator.example.toml: слово Example/Пример (и домены example.com/.org),
# ID из одной повторяющейся цифры, SWIFT на XXXX, семь нулей подряд в телефоне.
_PLACEHOLDER_WORD: Final = re.compile(r'\b(example|пример)\b', re.IGNORECASE)
_REPEATED_DIGIT: Final = re.compile(r'^(\d)\1+$')
_PLACEHOLDER_SWIFT: Final = 'XXXX'
_ZERO_RUN: Final = re.compile(r'0{7,}')


def compact_iban(raw: str) -> str:
    """IBAN без пробелов и в верхнем регистре — форма для проверки и хранения."""
    return re.sub(r'\s+', '', raw).upper()


def group_iban(raw: str) -> str:
    """IBAN группами по четыре символа — так его легче сверить глазами и продиктовать."""
    compact = compact_iban(raw)
    return ' '.join(compact[i : i + 4] for i in range(0, len(compact), 4))


def iban_problem(raw: str) -> str | None:
    """Что не так с IBAN, или None, если он правильный.

    Проверка — ISO 13616: форма, длина для известных стран и контрольная сумма
    mod 97 (переставить первые четыре символа в конец, буквы заменить числами
    A=10…Z=35, остаток от деления на 97 должен быть 1). Опечатка в одной цифре или
    перестановка соседних цифр контрольную сумму не проходят.

    Args:
        raw: IBAN как его вписали (пробелы допустимы).

    Returns:
        Текст проблемы для оператора или None.
    """
    compact = compact_iban(raw)
    if not _IBAN_SHAPE.match(compact):
        return f'IBAN {raw!r}: ожидается код страны, 2 контрольные цифры и номер счёта (15–34 символа)'
    expected = _IBAN_LENGTHS.get(compact[:2])
    if expected is not None and len(compact) != expected:
        return f'IBAN {raw!r}: для {compact[:2]} нужно {expected} символов, а не {len(compact)}'
    if compact.startswith('GE') and not _IBAN_GE.match(compact):
        return f'IBAN {raw!r}: грузинский IBAN — GE, 2 цифры, 2 буквы банка и 16 цифр'
    rearranged = compact[4:] + compact[:4]
    digits = ''.join(str(int(char, 36)) for char in rearranged)
    if int(digits) % _IBAN_MOD != _MOD97_OK:
        return f'IBAN {raw!r}: контрольная сумма не сходится (ISO 13616, mod 97) — опечатка или это образец'
    return None


def is_valid_iban(raw: str) -> bool:
    """Правильный ли IBAN (форма, длина, контрольная сумма)."""
    return iban_problem(raw) is None


def swift_problem(raw: str) -> str | None:
    """Что не так с SWIFT/BIC, или None. Проверяется форма: 8 или 11 символов ISO 9362."""
    if not _SWIFT.match(raw.strip().upper()):
        return f'SWIFT/BIC {raw!r}: ожидается 8 или 11 символов: 4 буквы банка, 2 буквы страны, 2 символа места'
    return None


class TaxNote(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Строка о налогах внизу счёта, на двух языках.

    Attributes:
        en: Для счёта на английском.
        ru: Для счёта на русском.
    """

    en: str = 'Small Business status holder — VAT is not charged.'
    ru: str = 'Статус малого бизнеса — НДС не начисляется.'

    def text(self, language: Language) -> str:
        return self.ru if language == 'ru' else self.en


class PaymentNote(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Дополнительные способы оплаты в блоке реквизитов (например, Wise ACH для клиентов из США).

    Attributes:
        en: Для счёта на английском; пусто — блок не печатается.
        ru: Для счёта на русском; пусто — берётся `en`.
    """

    en: str = ''
    ru: str = ''

    def text(self, language: Language) -> str:
        return (self.ru or self.en) if language == 'ru' else self.en


class Issuer(msgspec.Struct, frozen=True, kw_only=True):
    """Снимок реквизитов оператора на момент выпуска счёта.

    Хранится в реестре вместе со счётом: после смены банка, адреса или регистрации НДС
    повторная печать старого счёта показывает то, с чем он был выпущен, а не текущий профиль.

    Attributes:
        legal_name: Наименование ИП.
        legal_name_ru: Наименование по-русски или None.
        id_number: Идентификационный номер ИП.
        address: Юридический адрес.
        email: E-mail.
        phone: Телефон или None.
        website: Сайт или None.
        bank_name: Банк.
        bank_address: Адрес банка или None.
        iban: IBAN без пробелов.
        swift: SWIFT/BIC в верхнем регистре.
        intermediary_bank: Банк-посредник для USD или None.
        intermediary_swift: SWIFT банка-посредника или None.
        tax_note: Строка о налогах.
        payment_note: Дополнительные способы оплаты.
    """

    legal_name: str
    legal_name_ru: str | None = None
    id_number: str
    address: str
    email: str
    phone: str | None = None
    website: str | None = None
    bank_name: str
    bank_address: str | None = None
    iban: str
    swift: str
    intermediary_bank: str | None = None
    intermediary_swift: str | None = None
    tax_note: TaxNote = msgspec.field(default_factory=TaxNote)
    payment_note: PaymentNote = msgspec.field(default_factory=PaymentNote)

    @property
    def iban_grouped(self) -> str:
        return group_iban(self.iban)

    def display_name(self, language: Language) -> str:
        """Наименование ИП для счёта на нужном языке."""
        if language == 'ru' and self.legal_name_ru:
            return self.legal_name_ru
        return self.legal_name

    def printed_texts(self) -> list[str]:
        """Свободный текст оператора, который печатается в счёте: его проверяют на «консалтинг»."""
        return [self.tax_note.en, self.tax_note.ru, self.payment_note.en, self.payment_note.ru]


class OperatorProfile(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """Реквизиты ИП-оператора.

    Attributes:
        legal_name: Наименование для счетов: `Individual Entrepreneur <Имя>`.
        legal_name_ru: То же по-русски для счетов на русском (`ИП <Имя>`) или None.
        owner_name: Имя человека для договоров (`{{NAME}}`); None — из `legal_name` без «Individual Entrepreneur».
        id_number: Идентификационный номер ИП (9–11 цифр).
        address: Юридический адрес ИП.
        bank_name: Банк счёта ИП.
        iban: IBAN счёта ИП; проверяется контрольная сумма.
        swift: SWIFT/BIC банка.
        email: Рабочий e-mail.
        phone: Телефон или None.
        website: Сайт или None.
        telegram: Telegram без @ для договоров (`{{TELEGRAM}}`) или None.
        bank_address: Адрес банка — его часто спрашивает банк плательщика за границей.
        intermediary_bank: Банк-посредник (корреспондент) для переводов в USD или None.
        intermediary_swift: SWIFT/BIC банка-посредника или None.
        payment_note: Другие способы оплаты (en/ru), например реквизиты Wise ACH.
        tax_note: Строка о налогах внизу счёта (en/ru).
        invoice_prefix: Префикс номера счёта: `AS-` даёт `AS-2026-001`.
        default_currency: Валюта счёта по умолчанию.
        contract_fields: Прочие поля `{{…}}` шаблонов договоров: `SERVER_LOCATION`, `HOSTING_PROVIDER`, ….
    """

    legal_name: str
    legal_name_ru: str | None = None
    owner_name: str | None = None
    id_number: str
    address: str
    bank_name: str
    iban: str
    swift: str
    email: str
    phone: str | None = None
    website: str | None = None
    telegram: str | None = None
    bank_address: str | None = None
    intermediary_bank: str | None = None
    intermediary_swift: str | None = None
    payment_note: PaymentNote = msgspec.field(default_factory=PaymentNote)
    tax_note: TaxNote = msgspec.field(default_factory=TaxNote)
    invoice_prefix: str = 'AS-'
    default_currency: InvoiceCurrency = 'USD'
    contract_fields: dict[str, str] = msgspec.field(default_factory=dict)

    def __post_init__(self) -> None:
        # Все проблемы сразу: оператор правит файл один раз, а не по ошибке за запуск.
        problems = [problem for problem in self._problems() if problem]
        if problems:
            raise ConfigError('; '.join(problems))

    def _problems(self) -> list[str | None]:
        bad_keys = [key for key in self.contract_fields if not _FIELD_KEY.match(key)]
        required = {
            'legal_name': self.legal_name,
            'address': self.address,
            'bank_name': self.bank_name,
        }
        return [
            *(f'{name}: пусто' for name, value in required.items() if not value.strip()),
            None if _ID_NUMBER.match(self.id_number.strip()) else f'id_number {self.id_number!r}: ожидается 9–11 цифр',
            iban_problem(self.iban),
            swift_problem(self.swift),
            None if _EMAIL.match(self.email.strip()) else f'email {self.email!r}: не похоже на адрес',
            None
            if _PREFIX.match(self.invoice_prefix)
            else f'invoice_prefix {self.invoice_prefix!r}: заглавные латинские буквы, цифры и дефис, до 12 символов',
            None
            if self.telegram is None or _TELEGRAM.match(self.telegram)
            else f'telegram {self.telegram!r}: имя пользователя без @, 5–32 символа',
            None if not bad_keys else f'contract_fields: ключи как в шаблоне, ЗАГЛАВНЫМИ ({", ".join(bad_keys)})',
            None if self.intermediary_swift is None else swift_problem(self.intermediary_swift),
            None
            if self.intermediary_swift is None or self.intermediary_bank
            else 'intermediary_swift без intermediary_bank: впишите и название банка-посредника',
            *self._placeholder_problems(),
        ]

    def _placeholder_problems(self) -> list[str]:
        texts = {
            'legal_name': self.legal_name,
            'legal_name_ru': self.legal_name_ru,
            'owner_name': self.owner_name,
            'address': self.address,
            'bank_name': self.bank_name,
            'bank_address': self.bank_address,
            'intermediary_bank': self.intermediary_bank,
            'email': self.email,
            'website': self.website,
            'payment_note.en': self.payment_note.en,
            'payment_note.ru': self.payment_note.ru,
            **{f'contract_fields.{key}': value for key, value in self.contract_fields.items()},
        }
        found = [name for name, value in texts.items() if value and _PLACEHOLDER_WORD.search(value)]
        if _REPEATED_DIGIT.match(self.id_number.strip()):
            found.append('id_number')
        if self.swift_code.startswith(_PLACEHOLDER_SWIFT):
            found.append('swift')
        if self.intermediary_swift is not None and self.intermediary_swift.strip().upper().startswith(
            _PLACEHOLDER_SWIFT
        ):
            found.append('intermediary_swift')
        if self.telegram is not None and 'example' in self.telegram.lower():
            found.append('telegram')
        if self.phone is not None and _ZERO_RUN.search(re.sub(r'\D', '', self.phone)):
            found.append('phone')
        hint = 'значение-образец из operator.example.toml — впишите свои данные или удалите строку'
        return [f'{name}: {hint}' for name in found]

    @property
    def iban_compact(self) -> str:
        return compact_iban(self.iban)

    @property
    def iban_grouped(self) -> str:
        return group_iban(self.iban)

    @property
    def swift_code(self) -> str:
        return self.swift.strip().upper()

    def display_name(self, language: Language) -> str:
        """Наименование ИП для счёта на нужном языке."""
        if language == 'ru' and self.legal_name_ru:
            return self.legal_name_ru
        return self.legal_name

    def issuer(self) -> Issuer:
        """Снимок реквизитов для счёта: он сохраняется в реестре вместе со счётом."""
        return Issuer(
            legal_name=self.legal_name,
            legal_name_ru=self.legal_name_ru,
            id_number=self.id_number.strip(),
            address=self.address,
            email=self.email.strip(),
            phone=self.phone,
            website=self.website,
            bank_name=self.bank_name,
            bank_address=self.bank_address,
            iban=self.iban_compact,
            swift=self.swift_code,
            intermediary_bank=self.intermediary_bank,
            intermediary_swift=None if self.intermediary_swift is None else self.intermediary_swift.strip().upper(),
            tax_note=self.tax_note,
            payment_note=self.payment_note,
        )

    def person_name(self, language: Language) -> str:
        """Имя человека для договора: `owner_name` или наименование без «Individual Entrepreneur»."""
        if self.owner_name:
            return self.owner_name
        name = self.display_name(language)
        for prefix in _IE_PREFIXES:
            if name.startswith(prefix):
                return name.removeprefix(prefix).strip()
        return name


def operator_path(explicit: Path | None, environ: Mapping[str, str]) -> Path:
    """Где искать `operator.toml`: флаг `--operator`, затем `AUTOSTUDIO_OPERATOR`, затем корень репозитория.

    Корень ищется от исходников, а не от текущего каталога (`paths.project_root`).

    Args:
        explicit: Путь из флага или None.
        environ: Окружение процесса.

    Returns:
        Путь к файлу профиля.
    """
    if explicit is not None:
        return explicit
    from_env = environ.get(OPERATOR_ENV, '').strip()
    return Path(from_env) if from_env else project_root() / OPERATOR_FILE


def load_operator(path: Path) -> OperatorProfile:
    """Читает и проверяет профиль оператора.

    Args:
        path: Путь к `operator.toml`.

    Returns:
        Проверенный профиль.

    Raises:
        ConfigError: Файла нет, TOML битый или реквизиты неверны (все проблемы одним сообщением).
    """
    try:
        data = tomllib.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ConfigError(
            f'{path} не найден: скопируйте operator.example.toml в operator.toml и впишите реквизиты ИП '
            '(файл в .gitignore) или укажите --operator / AUTOSTUDIO_OPERATOR'
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f'{path}: битый TOML: {exc}') from None
    try:
        return msgspec.convert(data, type=OperatorProfile)
    except msgspec.ValidationError as exc:
        raise ConfigError(f'{path}: {exc}') from None
    except ConfigError as exc:
        raise ConfigError(f'{path}: {exc}') from None
