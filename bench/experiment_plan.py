"""生成配对实验任务清单，不模拟运行、不制造论文结果。

此文件保留早期三业务域研究设计，仅生成计划，未实现其中全部场景。
当前真实运行协议见 bench/faultmatrix.py 和 docs/EXPERIMENTS.md。
"""

from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import product
from pathlib import Path
import argparse
import csv
import json

DOMAINS = ('orders', 'tickets', 'files')
FAULTS = ('timeout', 'disconnect_before_commit', 'drop_after_commit',
          'http_429', 'http_503', 'stale_readback')
EFFECTS = ('read', 'idempotent_write', 'reversible_write', 'opaque_write')
RATES = (0, 1, 5, 10, 20)


@dataclass(frozen=True)
class Trial:
    """一次预先指定的实验条件与固定随机种子。"""

    scenario_id: str
    domain: str
    fault: str
    fault_rate_percent: int
    effect: str
    repeat: int
    seed: int


def trials(repeats: int = 30, master_seed: int = 20261023):
    """惰性生成试验，不允许不同策略各自重新抽样。"""
    if repeats < 1:
        raise ValueError('重复次数必须至少为 1')
    for domain, fault, rate, effect, run in product(
            DOMAINS, FAULTS, RATES, EFFECTS, range(repeats)):
        parts = (master_seed, domain, fault, rate, effect, run)
        # 不使用 Python hash()，避免不同进程的哈希随机化导致无法复现。
        identity = json.dumps(parts, separators=(',', ':'), ensure_ascii=False)
        digest = sha256(identity.encode('utf-8')).hexdigest()
        yield Trial(digest[:16], domain, fault, rate, effect, run, int(digest[16:32], 16))


def write_csv(path: Path, repeats: int = 30, master_seed: int = 20261023) -> int:
    """只导出计划行；真实执行器稍后填充业务结果。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(Trial.__dataclass_fields__))
        writer.writeheader()
        count = 0
        for trial in trials(repeats, master_seed):
            writer.writerow(asdict(trial))
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description='生成固定种子的故障实验计划')
    parser.add_argument('--repeats', type=int, default=30)
    parser.add_argument('--seed', type=int, default=20261023)
    parser.add_argument('--output', type=Path, default=Path('results/trial_plan.csv'))
    args = parser.parse_args()
    count = write_csv(args.output, args.repeats, args.seed)
    print(f'已生成 {count} 条实验计划：{args.output}（不含实验结果）')


if __name__ == '__main__':
    main()
