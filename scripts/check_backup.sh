#!/usr/bin/env bash
# Verify checksum and list tables inside a pg_dump -Fc (or .enc) backup.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
FILE="${1:-}"
if [[ -z "$FILE" ]]; then
  echo "用法: scripts/check_backup.sh backups/zhixing-....dump[.enc]" >&2
  exit 2
fi
if [[ ! -f "$FILE" ]]; then
  echo "文件不存在: $FILE" >&2
  exit 1
fi
SUM_FILE="$FILE.sha256"
if [[ -f "$SUM_FILE" ]]; then
  EXPECTED="$(awk '{print $1}' "$SUM_FILE")"
  ACTUAL="$(shasum -a 256 "$FILE" | awk '{print $1}')"
  if [[ "$EXPECTED" != "$ACTUAL" ]]; then
    echo "校验和失败" >&2
    exit 1
  fi
  echo "校验和通过"
else
  echo "未找到 ${SUM_FILE}，无法验证备份完整性" >&2
  exit 1
fi
WORK="$FILE"
CLEANUP=""
trap 'if [[ -n "$CLEANUP" ]]; then rm -f "$CLEANUP"; fi' EXIT
if [[ "$FILE" == *.enc ]]; then
  if [[ -z "${BACKUP_PASSPHRASE:-}" ]]; then
    echo "加密备份需要 BACKUP_PASSPHRASE" >&2
    exit 1
  fi
  WORK="$(mktemp)"
  CLEANUP="$WORK"
  openssl enc -d -aes-256-cbc -pbkdf2 -in "$FILE" -out "$WORK" -pass env:BACKUP_PASSPHRASE
fi
echo "备份内容列表（前 40 行）："
docker compose exec -T db pg_restore -l < "$WORK" | sed -n '1,40p'
echo "检查完成"
