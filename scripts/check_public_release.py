"""发布前检查：许可证、敏感文件、Windows 沙箱及中文编码。"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.venv', '__pycache__', '.pytest_cache', 'data', 'logs', 'results'}
TEXT_EXT = {'.py', '.go', '.md', '.toml', '.bat', '.cmd', '.sh', '.json', '.yml', '.yaml'}
UNSAFE_NAMES = {'.env', 'id_rsa', 'id_ed25519', 'credentials.json', 'secrets.json'}
UNSAFE_SUFFIX = {'.pem', '.key', '.p12', '.pfx', '.sqlite3', '.db', '.log'}
SECRET_PATTERNS = (
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}'),
    re.compile(r'\bsk-[A-Za-z0-9_-]{30,}'),
)


def check() -> list[str]:
    """检查工程分发风险，不把静态扫描误称为安全认证。"""
    errors: list[str] = []
    license_file = ROOT / 'LICENSE'
    if not license_file.is_file():
        errors.append('缺少 LICENSE')
    else:
        license_text = license_file.read_text(encoding='utf-8')
        if ('Apache License' not in license_text or 'Version 2.0, January 2004' not in license_text
                or 'END OF TERMS AND CONDITIONS' not in license_text):
            errors.append('LICENSE 不是完整的 Apache License 2.0')
    if not (ROOT / 'NOTICE').is_file():
        errors.append('缺少 NOTICE')

    for file in ROOT.rglob('*'):
        if not file.is_file() or any(part in SKIP for part in file.relative_to(ROOT).parts):
            continue
        relative = file.relative_to(ROOT).as_posix()
        if (file.name in UNSAFE_NAMES or file.name.startswith('.env.')
                or file.suffix.lower() in UNSAFE_SUFFIX):
            errors.append(f'疑似本机凭据或运行状态文件：{relative}')
            continue
        if file.suffix.lower() not in TEXT_EXT:
            continue
        try:
            content = file.read_text(encoding='utf-8-sig')
        except UnicodeError:
            errors.append(f'文本不是 UTF-8：{relative}')
            continue
        if '\ufffd' in content:
            errors.append(f'出现 Unicode 乱码替换字符：{relative}')
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            errors.append(f'疑似私钥或密钥硬编码：{relative}')

    binary = ROOT / 'bin' / 'raguard-mock.exe'
    digest_file = ROOT / 'bin' / 'raguard-mock.exe.sha256'
    if not binary.is_file() or not digest_file.is_file():
        errors.append('Windows 沙箱或校验文件缺失')
    else:
        expected = digest_file.read_text(encoding='ascii').strip()
        actual = hashlib.sha256(binary.read_bytes()).hexdigest()
        if expected.lower() != actual:
            errors.append('Windows 沙箱 SHA-256 不匹配')
    return errors


def main() -> int:
    errors = check()
    if errors:
        for error in errors:
            print('公开前检查失败：' + error)
        return 1
    print('公开前静态检查通过（许可证、敏感文件、中文编码与沙箱哈希）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
