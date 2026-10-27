#!/usr/bin/env bash
# Encrypted/custom-format PostgreSQL backup for the local Compose stack.
# Does NOT upload anywhere. Copy the resulting file to offsite media yourself.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true
STAMP="$(date +%Y%m%dT%H%M%S%z)"
RAW="$BACKUP_DIR/zhixing-$STAMP.dump"
echo "写入自定义格式备份: $RAW"
docker compose exec -T db pg_dump -U zhixing -Fc --no-owner --no-acl zhixing > "$RAW"
chmod 600 "$RAW"
FINAL="$RAW"
if [[ -n "${BACKUP_PASSPHRASE:-}" ]]; then
  FINAL="$RAW.enc"
  openssl enc -aes-256-cbc -pbkdf2 -salt -in "$RAW" -out "$FINAL" -pass env:BACKUP_PASSPHRASE
  rm -f "$RAW"
  chmod 600 "$FINAL"
  echo "已用 BACKUP_PASSPHRASE 加密为: $FINAL"
else
  echo "未设置 BACKUP_PASSPHRASE，备份未额外加密（库内联系方式仍是 Fernet 密文）。"
fi
SUM="$FINAL.sha256"
shasum -a 256 "$FINAL" | awk '{print $1"  '"$(basename "$FINAL")"'"}' > "$SUM"
chmod 600 "$SUM"
echo "校验和: $SUM"
echo "请将备份与 .env 中的 CONTACT_SECRET/SESSION_SECRET 安全副本分开放到异地介质（U 盘/另一台机器）。不要提交到 Git。"
