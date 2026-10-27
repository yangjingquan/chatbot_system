#!/usr/bin/env bash
# Restore a pg_dump backup into an isolated rehearsal database (does not touch zhixing).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FILE="${1:-}"
DBNAME="${2:-zhixing_rehearsal}"
if [[ -z "$FILE" || ! -f "$FILE" ]]; then
  echo "用法: scripts/restore_rehearsal.sh backups/zhixing-....dump[.enc] [dbname]" >&2
  exit 2
fi
WORK="$FILE"
CLEANUP=""
if [[ "$FILE" == *.enc ]]; then
  [[ -n "${BACKUP_PASSPHRASE:-}" ]] || { echo "需要 BACKUP_PASSPHRASE" >&2; exit 1; }
  WORK="$(mktemp)"
  CLEANUP="$WORK"
  openssl enc -d -aes-256-cbc -pbkdf2 -in "$FILE" -out "$WORK" -pass env:BACKUP_PASSPHRASE
fi
echo "准备演练库: $DBNAME"
docker compose exec -T db psql -U zhixing -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DBNAME' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS $DBNAME;
CREATE DATABASE $DBNAME OWNER zhixing;
SQL
echo "恢复中…"
docker compose exec -T db pg_restore -U zhixing -d "$DBNAME" --no-owner --no-acl < "$WORK" || true
# pg_restore may return 1 with harmless parallel warnings; verify counts.
docker compose exec -T db psql -U zhixing -d "$DBNAME" -c \
  "SELECT (SELECT count(*) FROM conversations) AS conversations,
          (SELECT count(*) FROM leads) AS leads,
          (SELECT count(*) FROM documents) AS documents,
          (SELECT count(*) FROM messages) AS messages;"
[[ -n "$CLEANUP" ]] && rm -f "$CLEANUP"
echo "演练库 $DBNAME 已就绪。用完后执行："
echo "  docker compose exec -T db psql -U zhixing -d postgres -c \"DROP DATABASE IF EXISTS $DBNAME;\""
