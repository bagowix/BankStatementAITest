import datetime as dt
from decimal import Decimal

from autostudio.office.invoice import ClientParty, InvoiceDraft, LineItem

# Вымышленные реквизиты: IBAN с правильной контрольной суммой, но кода банка XX в Грузии нет.
VALID_IBAN = 'GE07XX0000000000000000'
OPERATOR_TOML = f"""
legal_name = "Individual Entrepreneur Jane Example"
legal_name_ru = "ИП Джейн Пример"
id_number = "000000000"
address = "Georgia, Batumi, Example Street 1"
bank_name = "JSC Example Bank"
iban = "{VALID_IBAN}"
swift = "XXXXGE22"
email = "jane@example.com"
phone = "+995 500 00 00 00"
website = "https://example.com"
telegram = "jane_example"

[contract_fields]
SERVER_LOCATION = "Example Country"
"""


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
    )
