# 知行客服 · 常用门禁入口（详见 docs/自动化测试与升级流程.md）
.PHONY: test test-core test-backend test-frontend release-gate pre-upgrade upgrade-rehearsal

test:
	./scripts/run_tests.sh all

test-core:
	./scripts/run_tests.sh core

test-backend:
	./scripts/run_tests.sh backend

test-frontend:
	./scripts/run_tests.sh frontend

release-gate:
	./scripts/release_gate.sh

pre-upgrade:
	./scripts/pre_upgrade.sh

upgrade-rehearsal:
	./scripts/upgrade_rehearsal.sh
