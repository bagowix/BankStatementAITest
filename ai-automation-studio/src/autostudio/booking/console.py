"""Консольный адаптер: тот же движок, что в Telegram, — в терминале.

Оператор проверяет конфиг клиента до запуска (услуги, часы, FAQ, ответы модели)
и может записать демо без Telegram. Кнопки нумеруются: ввод `2` нажимает вторую,
`/share +995555123456` — «Отправить мой номер», `/quit` — выход.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Final

import msgspec

from autostudio.booking.engine import Engine
from autostudio.booking.messages import ADMINS, Button, Incoming, Outgoing

HELP: Final = 'Введите текст, номер кнопки, /share <телефон> или /quit.'

type Reader = Callable[[str], Awaitable[str | None]]
type Writer = Callable[[str], None]


async def read_line(prompt: str) -> str | None:
    """Строка из stdin в отдельном потоке (цикл событий не блокируется); None — конец ввода."""
    try:
        return await asyncio.to_thread(input, prompt)
    except EOFError:
        return None


def render(message: Outgoing, user: str, first_index: int) -> tuple[str, list[Button]]:
    """Сообщение движка как текст терминала.

    Args:
        message: Ответ движка.
        user: Ключ консольного пользователя.
        first_index: Номер первой кнопки.

    Returns:
        Текст и кнопки (по порядку номеров), которые можно нажать.
    """
    if message.to == ADMINS:
        return f'[→ администраторам] {message.text}', []
    if message.to is not None and message.to != user:
        return f'[→ {message.to}] {message.text}', []
    lines = [f'🤖 {message.text}']
    buttons = [button for row in message.buttons for button in row]
    index = first_index
    for row in message.buttons:
        labels = []
        for button in row:
            labels.append(f'[{index}] {button.label}')
            index += 1
        lines.append('   ' + '   '.join(labels))
    if message.request_contact is not None:
        lines.append(f'   ⌨ {message.request_contact}: /share <телефон>   ' + '   '.join(message.keyboard))
    return '\n'.join(lines), buttons


async def repl(
    engine: Engine,
    *,
    user: str = 'console:operator',
    name: str = 'Operator',
    language: str | None = None,
    admin: bool = False,
    read: Reader = read_line,
    write: Writer = print,
) -> int:
    """Диалог с движком в терминале.

    Args:
        engine: Движок.
        user: Ключ «покупателя».
        name: Отображаемое имя.
        language: Код языка клиента или None.
        admin: Говорить от имени администратора (служебные команды).
        read: Ввод строки (в тестах — заглушка).
        write: Вывод строки.

    Returns:
        Сколько сообщений обработано.
    """
    write(HELP)
    pressable: Sequence[Button] = []
    incoming: Incoming | None = Incoming(user=user, name=name, language=language, is_admin=admin, text='/start')
    handled = 0
    while incoming is not None:
        replies = await engine.handle(incoming)
        handled += 1
        shown: list[Button] = []
        for reply in replies:
            text, buttons = render(reply, user, len(shown) + 1)
            shown += buttons
            write(text)
        pressable = shown or pressable
        incoming = None
        while incoming is None:
            line = await read('> ')
            if line is None or line.strip() in ('/quit', '/exit'):
                return handled
            line = line.strip()
            base = Incoming(user=user, name=name, language=language, is_admin=admin)
            if line.isdigit() and 1 <= int(line) <= len(pressable):
                incoming = msgspec.structs.replace(base, data=pressable[int(line) - 1].data)
            elif line.startswith('/share '):
                incoming = msgspec.structs.replace(base, phone=line.removeprefix('/share ').strip())
            elif line:
                incoming = msgspec.structs.replace(base, text=line)
    return handled
