"""执行同种子、多策略的真实 HTTP 故障试验并生成原始数据。"""

import argparse
from dataclasses import asdict
from itertools import product
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

from bench.analyze import write_csv, aggregate, write_report
from scripts.sandbox_binary import bundled_windows_sandbox
from bench.runner import (FAULTS, METHODS, RATES, TOOL_IDS, Trial,
                          build_sandbox, trial_result)


def parse_args():
    cli = argparse.ArgumentParser(description='RAGuard 独立 Go 沙箱故障矩阵')
    cli.add_argument('--mode', choices=['smoke', 'full'], default='smoke')
    cli.add_argument('--repeats', type=int, default=None,
                     help='每一条件重复次数；正式实验建议先作功效分析')
    cli.add_argument('--seed', type=int, default=20261023)
    cli.add_argument('--output', type=Path, default=Path('results/run'))
    cli.add_argument('--max-episodes', type=int, default=0, help='仅供调试的前 N 个试验')
    cli.add_argument('--no-readback', action='store_true', help='关闭服务端状态查询')
    return cli.parse_args()


def plan(mode: str, repeats: int, seed: int, no_readback: bool = False) -> list[Trial]:
    if repeats < 1:
        raise ValueError('重复次数至少为 1')
    if mode == 'smoke':
        faults = ('before_disconnect', 'after_disconnect')
        rates = (0.0, 1.0)
        tools = TOOL_IDS
    elif mode == 'full':
        faults, rates, tools = FAULTS, RATES, TOOL_IDS
    else:
        raise ValueError('未知实验模式')
    # 故障选择不依赖方法；每个 seed/replicate 在六种方案里严格配对。
    return [Trial(method, tool, fault, rate, repeat, seed, no_readback)
            for tool, fault, rate, repeat, method in
            product(tools, faults, rates, range(repeats), METHODS)]


def main() -> int:
    args = parse_args()
    repeats = args.repeats if args.repeats is not None else (1 if args.mode == 'smoke' else 30)
    episodes = plan(args.mode, repeats, args.seed, args.no_readback)
    if args.max_episodes:
        episodes = episodes[:args.max_episodes]
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as build:
        binary = build_sandbox(Path(build))
        records = []
        for index, trial in enumerate(episodes, 1):
            records.append(asdict(trial_result(trial, binary)))
            if index % 50 == 0:
                print(f'试验进度 {index}/{len(episodes)}', flush=True)
    write_csv(args.output/'episodes.csv', records)
    write_csv(args.output/'summary.csv', aggregate(records))
    write_report(args.output/'REPORT.md', records, mode=args.mode)
    try:
        version = subprocess.run(['go', 'version'], text=True, capture_output=True,
                                 check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        version = 'bundled-windows-binary' if bundled_windows_sandbox() else 'unknown'
    manifest = dict(mode=args.mode, repeats=repeats, seed=args.seed,
                    no_readback=args.no_readback, episode_count=len(records),
                    planned_episode_count=len(plan(args.mode, repeats, args.seed, args.no_readback)),
                    python=sys.version.split()[0], go=version,
                    platform=platform.platform(),
                    research_ready=False,
                    notes='沙箱实验原始数据；尚未包含真实 Agent 跨域基准。')
    (args.output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n',
                                             encoding='utf-8')
    print(f'完成 {len(records)} 组；原始数据位于 {args.output / "episodes.csv"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
