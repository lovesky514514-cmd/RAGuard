"""检查仓库文本编码和明显的乱码替换字符。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {'.py', '.go', '.mod', '.md', '.toml', '.sh', '.bat', '.cmd', '.json', '.yml', '.yaml'}
SKIP_DIRS = {'__pycache__', '.venv', 'results', 'data', '.git'}


def main():
    errors = []
    checked = 0
    for path in ROOT.rglob('*'):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        checked += 1
        raw = path.read_bytes()
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            errors.append(f'{path.relative_to(ROOT)}：不是 UTF-8')
            continue
        if content.startswith('\ufeff'):
            errors.append(f'{path.relative_to(ROOT)}：含 UTF-8 BOM，请移除')
        if '\ufffd' in content or ('锟' + '斤拷') in content:
            errors.append(f'{path.relative_to(ROOT)}：发现替换字符或疑似乱码')
    for error in errors:
        print(error)
    print(f'已检查 {checked} 个文本文件；编码异常 {len(errors)} 个')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
