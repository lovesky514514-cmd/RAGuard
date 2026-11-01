"""检查源码头部说明、长装饰分隔线与中文注释覆盖率。"""

from __future__ import annotations

import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DIRS = ("raguard", "bench", "mock", "tests", "scripts", "audit")
SUFFIXES = {".py", ".go", ".sh", ".bat", ".cmd"}
CHINESE = re.compile(r"[\u3400-\u9fff]")
SEPARATOR = re.compile(r"^\s*(?:(?:#|//|/\*|\*|::|REM)\s*)?(?:[=]{8,}|[-]{12,}|[_]{12,}|[—─]{8,})\s*$")
DOC_OPEN = re.compile(r"^(?:[ruRU]{0,2})?\"\"\"|^(?:[ruRU]{0,2})?'''")
SKIP_PARTS = {"__pycache__", ".venv", "build", "dist"}


def scan(path: Path) -> tuple[list[str], bool]:
    """只检查源码文本，不修改文件或重排研究结果。"""
    rel = path.relative_to(ROOT).as_posix()
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    problems = []
    for index, line in enumerate(lines, 1):
        if SEPARATOR.fullmatch(line):
            problems.append(f"{rel}:{index} 存在长装饰分隔线")
    first = next((i for i, value in enumerate(lines) if value.strip()), None)
    if first is not None and path.suffix == ".py" and DOC_OPEN.match(lines[first].strip()):
        tail = next((i for i in range(first + 1, len(lines))
                     if '"""' in lines[i] or "'''" in lines[i]), None)
        if tail is not None and tail - first + 1 > 12:
            problems.append(f"{rel}:1 文件头说明超过 12 行")
        if len(lines[first]) > 200:
            problems.append(f"{rel}:1 文件头说明行过长")
    meaningful = [line.strip() for line in lines
                  if line.strip().startswith(("#", "//"))]
    has_chinese = bool(CHINESE.search("\n".join(meaningful[:200]) +
                                    "\n".join(lines[:8])))
    return problems, has_chinese


def check() -> int:
    """输出可核查统计；规范违规使 CI 失败，注释覆盖不足只提醒。"""
    errors = []
    source_count = 0
    chinese_count = 0
    uncovered = []
    for folder in DIRS:
        for path in sorted((ROOT / folder).rglob("*")):
            if (not path.is_file() or path.suffix not in SUFFIXES
                    or any(p in SKIP_PARTS for p in path.parts)):
                continue
            source_count += 1
            problems, is_chinese = scan(path)
            errors.extend(problems)
            if is_chinese:
                chinese_count += 1
            elif path.suffix in {".py", ".go"}:
                uncovered.append(path.relative_to(ROOT).as_posix())
    print(f"检查 {source_count} 个源码文件，其中 {chinese_count} 个含中文注释或头部说明")
    if uncovered:
        print("尚待增加中文说明的文件：", ", ".join(uncovered[:15]))
    for error in errors:
        print("代码格式问题：", error)
    return int(bool(errors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAGuard 源码可读性检查")
    parser.parse_args()
    raise SystemExit(check())
