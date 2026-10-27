#!/usr/bin/env bash
# Repeatable automated test entry for core + full suites.
# Runs inside the backend image with the host backend/ bind-mounted so
# uncommitted local edits are exercised without a rebuild.
# Usage:
#   scripts/run_tests.sh           # full backend + frontend
#   scripts/run_tests.sh core      # API / permissions / chat / trial / knowledge / schema
#   scripts/run_tests.sh backend
#   scripts/run_tests.sh frontend
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MODE="${1:-all}"

CORE_TESTS=(
  tests/test_api.py
  tests/test_permissions.py
  tests/test_access_control.py
  tests/test_stream_concurrency.py
  tests/test_privacy.py
  tests/test_document_lifecycle.py
  tests/test_knowledge_maintenance.py
  tests/test_reception.py
  tests/test_reception_workbench.py
  tests/test_reception_ws.py
  tests/test_trial_lead_chain.py
  tests/test_schema_migrations.py
)

backend_pytest() {
  # --no-deps: SQLite tests do not need db/redis. Bind-mount current sources.
  docker compose run --rm --no-deps \
    -v "$ROOT/backend:/app" \
    -v "$ROOT/knowledge_base:/knowledge_base:ro" \
    -e EMBEDDING_ENABLED=false \
    -e DEEPSEEK_API_KEY= \
    backend \
    pytest "$@"
}

run_backend_core() {
  echo "== 后端核心门禁 =="
  backend_pytest "${CORE_TESTS[@]}" -q
}

run_backend_all() {
  echo "== 后端全量 =="
  backend_pytest -q
}

run_frontend() {
  echo "== 前端 =="
  # Prefer Node >= 20 (package scripts need node --test / strip-types).
  if [[ -z "${NODE_BINARY:-}" ]]; then
    for candidate in       "$HOME/.nvm/versions/node/v22.23.2/bin"       "$HOME/.nvm/versions/node/v20.20.2/bin"       /opt/homebrew/opt/node@20/bin       /usr/local/opt/node@20/bin; do
      if [[ -x "$candidate/node" ]]; then
        export PATH="$candidate:$PATH"
        break
      fi
    done
  fi
  node_major="$(node -p "process.versions.node.split('.')[0]" 2>/dev/null || echo 0)"
  if [[ "$node_major" -lt 20 ]]; then
    echo "前端测试需要 Node >= 20，当前: $(node -v 2>/dev/null || echo missing)" >&2
    echo "请启用 nvm 的 v20/v22，或设置 PATH 后重试。" >&2
    exit 1
  fi
  if [[ ! -d frontend/node_modules ]]; then
    (cd frontend && npm ci)
  fi
  (cd frontend && npm test)
}

case "$MODE" in
  core) run_backend_core ;;
  backend) run_backend_all ;;
  frontend) run_frontend ;;
  all|"")
    run_backend_all
    run_frontend
    ;;
  *)
    echo "用法: $0 [all|core|backend|frontend]" >&2
    exit 2
    ;;
esac
echo "测试通过 ($MODE)"
