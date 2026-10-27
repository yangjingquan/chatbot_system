#!/usr/bin/env python3
"""Re-encrypt lead contacts after CONTACT_SECRET / CONTACT_SECRET_PREVIOUS are set.

Usage (from repo root, against running Compose backend env):
  docker compose exec -T backend python -m app  # no
  docker compose exec -T backend python /app/../  # scripts not in image

Preferred:
  docker compose run --rm -e CONTACT_SECRET -e CONTACT_SECRET_PREVIOUS -e SESSION_SECRET \
    backend python - <<'PY'
  ... or copy this file into the container.

This script is also runnable on the host if DATABASE_URL points at the DB:
  cd backend && CONTACT_SECRET=... CONTACT_SECRET_PREVIOUS=... DATABASE_URL=... python ../scripts/rotate_contact_secret.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'backend'))

def main():
    from app.db import engine
    from app.encryption import rotate_contacts, contact_secrets
    primary, previous = contact_secrets()
    if not primary:
        raise SystemExit('缺少 CONTACT_SECRET 或 SESSION_SECRET')
    if not previous:
        print('警告: 未设置 CONTACT_SECRET_PREVIOUS。若新密钥与旧密钥不同，历史密文将无法解密。', file=sys.stderr)
    with engine.begin() as conn:
        count = rotate_contacts(conn)
    print(f'已重加密联系方式行数: {count}')
    print('完成后请清空 CONTACT_SECRET_PREVIOUS，并 docker compose up -d --force-recreate backend worker')

if __name__ == '__main__':
    main()
