#!/usr/bin/env bash
# Rehearse: backup → restore to isolated DB → apply migrations → release gate.
# Never touches the live business database name "zhixing".
#
# Usage:
#   scripts/upgrade_rehearsal.sh [backups/zhixing-….dump[.enc]]
# If no file is given, runs backup_db.sh first.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
DBNAME="${REHEARSAL_DB:-zhixing_upgrade_rehearsal}"

FILE="${1:-}"
if [[ -z "$FILE" ]]; then
  echo "== 无备份参数：先做一次升级前备份 =="
  ./scripts/backup_db.sh
  FILE="$(ls -1t backups/zhixing-*.dump backups/zhixing-*.dump.enc 2>/dev/null | head -1 || true)"
fi
if [[ -z "$FILE" || ! -f "$FILE" ]]; then
  echo "用法: $0 backups/zhixing-....dump[.enc]" >&2
  exit 2
fi

echo "== 1/5 校验备份 =="
./scripts/check_backup.sh "$FILE"

echo "== 2/5 恢复到隔离库 ${DBNAME}（不覆盖 zhixing） =="
./scripts/restore_rehearsal.sh "$FILE" "$DBNAME"

echo "== 3/5 在隔离库执行 migrate_schema =="
# Pass target DB name via env; keep the heredoc quoted so host shell does not expand.
docker compose exec -T -e "REHEARSAL_DBNAME=$DBNAME" backend python - <<'PY'
import os
from sqlalchemy import create_engine
from app.migrations import (
    migrate_schema,
    applied_schema_version,
    expected_schema_version,
    assert_schema_current,
)
base = os.environ['DATABASE_URL']
dbname = os.environ['REHEARSAL_DBNAME']
assert dbname and dbname != 'zhixing', 'refusing to migrate live db name'
url = base.rsplit('/', 1)[0] + '/' + dbname
engine = create_engine(url, pool_pre_ping=True)
migrate_schema(engine)
info = assert_schema_current(engine)
print('rehearsal schema OK', info)
print(f'applied={applied_schema_version(engine)} expected={expected_schema_version()}')
PY

echo "== 4/5 离线发布门禁（核心+全量+前端；不依赖演练库） =="
./scripts/release_gate.sh

echo "== 5/5 演练结束 =="
echo "隔离库 ${DBNAME} 仍保留，便于人工抽查。清理："
echo "  docker compose exec -T db psql -U zhixing -d postgres -c \"DROP DATABASE IF EXISTS ${DBNAME};\""
echo
echo "若演练失败：停止发布；业务库未改动。"
echo "生产升级失败时：用同备份恢复业务库（见 docs/自动化测试与升级流程.md 回滚节）。"
