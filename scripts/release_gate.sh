#!/usr/bin/env bash
# Release gate: must pass before promoting a build.
# Failure ⇒ stop the release; do not rebuild / switch traffic.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== 发布门禁：核心自动化测试 =="
./scripts/run_tests.sh core

echo "== 发布门禁：后端全量 =="
./scripts/run_tests.sh backend

echo "== 发布门禁：前端 =="
./scripts/run_tests.sh frontend

if curl -fsS "http://127.0.0.1:${WEB_PORT:-18080}/api/health" -o /tmp/zhixing-gate-health.json 2>/dev/null; then
  echo "== 运行中服务 schema 核对 =="
  python3 - <<'PY'
import json,sys
h=json.load(open('/tmp/zhixing-gate-health.json'))
sv=h.get('schema_version') or {}
print(h.get('status'), sv)
if sv and not sv.get('current', True):
    sys.exit('运行中数据库 schema 落后于代码，停止发布。')
if h.get('checks',{}).get('database')=='fail':
    sys.exit('数据库检查失败，停止发布。')
PY
else
  echo "（跳过在线健康检查：本机 ${WEB_PORT:-18080} 未监听，仅依赖离线测试门禁）"
fi

echo "发布门禁通过。"
