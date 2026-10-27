#!/usr/bin/env bash
# Pre-upgrade gate: health → backup → checksum → record schema version.
# Stops with non-zero status if any step fails; do NOT rebuild on failure.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== 1/4 健康检查 =="
if ! curl -fsS "http://127.0.0.1:${WEB_PORT:-18080}/api/health" -o /tmp/zhixing-health.json; then
  echo "健康检查失败：服务未就绪，已停止升级。" >&2
  exit 1
fi
python3 - <<'PY'
import json,sys
h=json.load(open('/tmp/zhixing-health.json'))
print('health:', h.get('status'), 'schema:', h.get('schema_version'))
if h.get('status')=='fail' or h.get('checks',{}).get('database')=='fail':
    sys.exit('数据库或整体状态失败，已停止升级。')
PY

echo "== 2/4 升级前备份 =="
./scripts/backup_db.sh
LATEST="$(ls -1t backups/zhixing-*.dump backups/zhixing-*.dump.enc 2>/dev/null | head -1 || true)"
if [[ -z "${LATEST}" ]]; then
  echo "未找到备份文件，已停止升级。" >&2
  exit 1
fi
echo "备份文件: $LATEST"

echo "== 3/4 备份校验 =="
./scripts/check_backup.sh "$LATEST"

echo "== 4/4 记录当前 schema =="
docker compose exec -T backend python - <<'PY'
from app.db import engine
from app.migrations import applied_schema_version, expected_schema_version, migrate_schema
migrate_schema(engine)
print(f'applied={applied_schema_version(engine)} expected={expected_schema_version()}')
PY

echo
echo "升级前检查通过。"
echo "下一步（成功后再做）：docker compose up -d --build"
echo "若构建或启动失败：不要继续；用 backups/ 中刚生成的文件按 docs/自动化测试与升级流程.md 回滚。"
echo "BACKUP_FILE=$LATEST"
