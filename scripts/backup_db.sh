#!/usr/bin/env bash
# PostgreSQL full backup. Files are staged, then published only after success.
# Offsite copying and restore drills remain separate operator responsibilities.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ -n "${PHYSICAL_BACKUP_HOST_DIR:-}" ]]; then
  TASK_BACKUP_DIR="$PHYSICAL_BACKUP_HOST_DIR"
elif [[ -n "${BACKUP_DIR:-}" ]]; then
  TASK_BACKUP_DIR="$BACKUP_DIR" # compatibility with existing host scripts
elif [[ "$ROOT" == /opt/chatbot/app ]]; then
  TASK_BACKUP_DIR=/opt/chatbot/backups
else
  TASK_BACKUP_DIR="$ROOT/backups"
fi
mkdir -p "$TASK_BACKUP_DIR"
chmod 700 "$TASK_BACKUP_DIR" 2>/dev/null || true
STAMP="$(date +%Y%m%dT%H%M%S%z)-$$"
WORK_DIR="$(mktemp -d "$TASK_BACKUP_DIR/.backup-$STAMP-XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
RAW="$WORK_DIR/zhixing-$STAMP.dump"
echo "创建 PostgreSQL 完整备份..."
docker compose exec -T db pg_dump -U zhixing -Fc --no-owner --no-acl zhixing > "$RAW"
[[ -s "$RAW" ]] || { echo "备份文件为空，未发布" >&2; exit 1; }
chmod 600 "$RAW"
STAGED="$RAW"
FINAL="$TASK_BACKUP_DIR/zhixing-$STAMP.dump"
if [[ -n "${BACKUP_PASSPHRASE:-}" ]]; then
  STAGED="$RAW.enc"
  FINAL="$FINAL.enc"
  openssl enc -aes-256-cbc -pbkdf2 -salt -in "$RAW" -out "$STAGED" -pass env:BACKUP_PASSPHRASE
  [[ -s "$STAGED" ]] || { echo "加密备份文件为空，未发布" >&2; exit 1; }
  chmod 600 "$STAGED"
else
  echo "未设置 BACKUP_PASSPHRASE，备份未额外加密（库内联系方式仍是 Fernet 密文）。"
fi
DIGEST="$(shasum -a 256 "$STAGED" | awk '{print $1}')"
printf '%s  %s\n' "$DIGEST" "$(basename "$FINAL")" > "$WORK_DIR/checksum"
chmod 600 "$WORK_DIR/checksum"
mv "$WORK_DIR/checksum" "$FINAL.sha256"
mv "$STAGED" "$FINAL"
echo "备份文件: $FINAL"
echo "校验和: $FINAL.sha256"
echo "请将备份与密钥安全副本分开放到异地介质，不要提交到 Git。"
