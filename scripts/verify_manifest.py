"""为交付包创建并核对逐文件 SHA-256；不扫描本地运行密钥。"""

import argparse
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'SOURCE_MANIFEST.sha256'
SKIPPED = {'data', 'logs', 'results', '.git', '__pycache__', '.venv', '.pytest_cache'}


def files():
    for item in sorted(ROOT.rglob('*')):
        if not item.is_file() or item == MANIFEST:
            continue
        rel = item.relative_to(ROOT)
        if any(part in SKIPPED for part in rel.parts) or rel.name in {'.coverage', '.DS_Store'}:
            continue
        if rel.suffix in {'.pyc', '.test'}:
            continue
        yield rel


def digest(path: Path) -> str:
    content = path.read_bytes()
    # Git 在 Windows 检出时可能改变文本换行，哈希统一采用 LF。
    # 二进制文件始终严格按照原始字节校验。
    if path.suffix.lower() != '.exe':
        content = content.replace(b'\r\n', b'\n')
    return sha256(content).hexdigest()

def build():
    return {str(name).replace('\\', '/'): digest(ROOT / name) for name in files()}


def main():
    parser = argparse.ArgumentParser(description='创建或检查源码交付清单')
    parser.add_argument('--write', action='store_true', help='生成新的交付清单')
    args = parser.parse_args()
    actual = build()
    if args.write:
        content = '\n'.join(f'{checksum}  {name}' for name, checksum in actual.items()) + '\n'
        MANIFEST.write_text(content, encoding='utf-8')
        print(f'已生成 {len(actual)} 个文件的 SHA-256 清单')
        return 0
    if not MANIFEST.exists():
        raise SystemExit('缺少 SOURCE_MANIFEST.sha256')
    expected = {}
    for entry in MANIFEST.read_text(encoding='utf-8').splitlines():
        checksum, sep, name = entry.partition('  ')
        if not sep or not name or name in expected:
            raise SystemExit('清单存在格式错误或重复文件')
        expected[name] = checksum
    missing = sorted(expected.keys() - actual.keys())
    unexpected = sorted(actual.keys() - expected.keys())
    mismatched = sorted(k for k in expected.keys() & actual.keys()
                        if expected[k] != actual[k])
    if missing or unexpected or mismatched:
        print(f'清单不一致：缺失 {missing[:5]}，多出 {unexpected[:5]}，修改 {mismatched[:5]}')
        return 1
    print(f'SHA-256 核对通过：{len(actual)} 个文件')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
