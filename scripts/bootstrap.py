"""Generate local secrets once without printing them."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
target = root / '.env'
if target.exists():
    print('保留已有 .env，不覆盖配置。')
else:
    content = (root / '.env.example').read_text()
    content = content.replace('replace-with-a-long-random-password', secrets.token_urlsafe(18))
    content = content.replace('replace-with-at-least-32-random-characters', secrets.token_urlsafe(48))
    content = content.replace('replace-with-a-random-database-password', secrets.token_hex(24))
    target.write_text(content)
    target.chmod(0o600)
    print('已生成本地 .env（权限 600）；管理员密码见 ADMIN_PASSWORD。')
