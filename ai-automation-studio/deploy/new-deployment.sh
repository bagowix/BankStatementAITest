#!/usr/bin/env bash
# Готовит каталог развёртывания клиента на сервере: compose-файл и проверка, что
# конфиги и .env на месте. Сам запуск — отдельной командой, чтобы оператор видел,
# что именно поднимается.
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <client-id> <package> [<package>...]" >&2
  exit 64
fi

client_id="$1"; shift
dir="clients/${client_id}"
[[ -d "$dir" ]] || { echo "нет каталога $dir — сначала: autostudio new-client ${client_id} --package <pkg>" >&2; exit 1; }
[[ -f "$dir/.env" ]] || { echo "нет $dir/.env" >&2; exit 1; }

for pkg in "$@"; do
  [[ -f "$dir/${pkg}.toml" ]] || { echo "нет $dir/${pkg}.toml" >&2; exit 1; }
done

sed "s/\${CLIENT_ID:?CLIENT_ID не задан}/${client_id}/" deploy/compose.client.yaml > "$dir/compose.yaml"
profiles=$(printf -- '--profile %s ' "$@")
echo "готово: $dir/compose.yaml"
echo "проверка: docker compose -f $dir/compose.yaml run --rm ${1} ${1} check /config/${1}.toml"
echo "запуск:   docker compose -f $dir/compose.yaml ${profiles}up -d"
