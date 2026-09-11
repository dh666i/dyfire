"""Prepare a NEW deployment directory. Never imports or overwrites a live site."""
import os
from pathlib import Path
import secrets
import shutil


def main():
    root = Path(__file__).resolve().parents[1]
    if (root / 'data' / 'spark.db').exists():
        raise SystemExit('已有数据库，停止初始化；迁移需保留原密钥和数据。')
    for directory in ('data', 'secrets', 'updates', 'updates/requests', 'backups'):
        path = root / directory
        path.mkdir(exist_ok=True, parents=True)
    for name in ('cookie.key', 'session.key'):
        path = root / 'secrets' / name
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(secrets.token_bytes(32))
            os.chmod(path, 0o600)
            os.chown(path, 10001, 10001)
    for name in ('data', 'updates/requests'):
        os.chown(root / name, 10001, 10001)
        os.chmod(root / name, 0o700)
    os.chmod(root / 'updates', 0o755)
    os.chmod(root / 'backups', 0o700)
    if not (root / '.env.console').exists():
        shutil.copyfile(root / '.env.console.example', root / '.env.console')
        os.chmod(root / '.env.console', 0o600)
    print('目录已初始化；尚未创建用户或启动服务。')


if __name__ == '__main__':
    main()
