# clients/

Каталоги клиентов: `clients/<client-id>/<package>.toml` и `.env` с секретами.
Всё, кроме этого файла, в `.gitignore`: конфиги клиентов содержат их адреса,
чаты и таблицы, а `.env` — токены. Храните их в приватном месте (зашифрованный
бэкап, менеджер паролей), а не в git.

Новый клиент: `uv run autostudio new-client acme-barbers --package booking`.
