import datetime as dt
from decimal import Decimal

import msgspec

from autostudio.office.invoice import ClientParty, InvoiceDraft, LineItem
from autostudio.office.operator import Issuer, OperatorProfile

# Вымышленный оператор для тестов. Не «Example»: такие значения профиль отвергает как
# остатки operator.example.toml. Домен .test зарезервирован (RFC 2606), IBAN — с правильной
# контрольной суммой, но кода банка XX в Грузии нет.
VALID_IBAN = 'GE07XX0000000000000000'
TEST_NAME = 'Test Operator'
TEST_NAME_RU = 'Тест Оператор'
TEST_ID = '123456789'
TEST_SWIFT = 'TESTGE22'
TEST_BANK = 'JSC Test Bank'
TEST_EMAIL = 'office@operator.test'
TEST_TELEGRAM = 'test_operator'
OPERATOR_TOML = f"""
legal_name = "Individual Entrepreneur {TEST_NAME}"
legal_name_ru = "ИП {TEST_NAME_RU}"
id_number = "{TEST_ID}"
address = "Georgia, Batumi, Test Street 1"
bank_name = "{TEST_BANK}"
iban = "{VALID_IBAN}"
swift = "{TEST_SWIFT}"
email = "{TEST_EMAIL}"
website = "https://operator.test"
telegram = "{TEST_TELEGRAM}"

[contract_fields]
SERVER_LOCATION = "Test Country"
"""


_PROFILE = OperatorProfile(
    legal_name=f'Individual Entrepreneur {TEST_NAME}',
    legal_name_ru=f'ИП {TEST_NAME_RU}',
    id_number=TEST_ID,
    address='Georgia, Batumi, Test Street 1',
    bank_name=TEST_BANK,
    iban=VALID_IBAN,
    swift=TEST_SWIFT,
    email=TEST_EMAIL,
    telegram=TEST_TELEGRAM,
    contract_fields={'SERVER_LOCATION': 'Test Country'},
)


def make_profile(**changes: object) -> OperatorProfile:
    # replace снова вызывает __post_init__: изменённый профиль проходит ту же проверку.
    return msgspec.structs.replace(_PROFILE, **changes)


def make_issuer() -> Issuer:
    return make_profile().issuer()


def make_draft(
    *, issue_date: dt.date = dt.date(2026, 10, 20), name: str = 'Example Corp', price: str = '600.00'
) -> InvoiceDraft:
    return InvoiceDraft(
        client=ClientParty(name=name, country='US'),
        items=[
            LineItem(
                description='Software development, configuration and integration services', unit_price=Decimal(price)
            )
        ],
        currency='USD',
        issue_date=issue_date,
        issuer=make_issuer(),
    )
