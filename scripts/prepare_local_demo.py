"""为离线本地演示创建独立签名密钥；不要把该文件用于生产。"""

import os
from pathlib import Path
import secrets


SECRET_FILE = Path(__file__).resolve().parents[1] / 'data' / 'local_demo_identity.key'


def main() -> None:
    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SECRET_FILE.exists():
        # 使用排他创建，防止并发启动时静默覆盖另一进程使用的密钥。
        fd = os.open(SECRET_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            stream.write(secrets.token_hex(32))
    if len(SECRET_FILE.read_text(encoding='ascii').strip()) < 32:
        raise ValueError('本地演示签名密钥长度不够')
    print('已准备本地演示签名密钥（不显示密钥内容）')


if __name__ == '__main__':
    main()
