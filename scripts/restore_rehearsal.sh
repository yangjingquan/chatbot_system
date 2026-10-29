#!/usr/bin/env bash
# Restore into a NEW, explicitly named rehearsal database; never replace an existing database.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FILE="${1:-}"
DBNAME="${2:-zhixing_rehearsal}"
if [[ -z "$FILE" || ! -f "$FILE" ]]; then
  echo "用法: scripts/restore_rehearsal.sh backups/zhixing-....dump[.enc] [zhixing_rehearsal[_标识]]" >&2
  exit 2
fi
if [[ ! "$DBNAME" =~ ^zhixing_rehearsal(_[a-z0-9_]+)?$ ]] || (( ${#DBNAME} > 63 )); then
  echo "演练库名称须为 zhixing_rehearsal 或 zhixing_rehearsal_ 加小写字母、数字、下划线" >&2
  exit 2
fi
./scripts/check_backup.sh "$FILE"
WORK="$FILE"
CLEANUP=""
trap 'if [[ -n "$CLEANUP" ]]; then rm -f "$CLEANUP"; fi' EXIT
if [[ "$FILE" == *.enc ]]; then
  [[ -n "${BACKUP_PASSPHRASE:-}" ]] || { echo "需要 BACKUP_PASSPHRASE" >&2; exit 1; }
  WORK="$(mktemp)"
  CLEANUP="$WORK"
  openssl enc -d -aes-256-cbc -pbkdf2 -in "$FILE" -out "$WORK" -pass env:BACKUP_PASSPHRASE
fi
EXISTS="$(docker compose exec -T db psql -U zhixing -d postgres -At -v ON_ERROR_STOP=1 -c "SELECT 1 FROM pg_database WHERE datname = '$DBNAME'")"
if [[ -n "$EXISTS" ]]; then
  echo "演练库 $DBNAME 已存在，为避免覆盖，请选择新的演练库名称。" >&2
  exit 1
fi
echo "创建新演练库: $DBNAME"
docker compose exec -T db psql -U zhixing -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"$DBNAME\" OWNER zhixing"
echo "恢复中…"
docker compose exec -T db pg_restore -U zhixing -d "$DBNAME" --no-owner --no-acl --exit-on-error < "$WORK"
docker compose exec -T db psql -U zhixing -d "$DBNAME" -v ON_ERROR_STOP=1 -c \
  "SELECT (SELECT count(*) FROM conversations) AS conversations,
          (SELECT count(*) FROM leads) AS leads,
          (SELECT count(*) FROM documents) AS documents,
          (SELECT count(*) FROM messages) AS messages;"
echo "演练库 $DBNAME 已就绪，请核对数据及恢复耗时。清理前确认库名和内容。"
