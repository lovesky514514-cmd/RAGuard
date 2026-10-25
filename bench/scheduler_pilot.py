"""训练/测试隔离的证据查询影子试验，不连接 Gateway 或写入接口。"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from pathlib import Path
import random

from raguard.evidence_scheduler import (MAX_TICK, ObservationTrace, Schedule,
                                        fit_scheduler)

SEEDS = (11, 23, 47, 71, 101)
PROVIDERS = ('fast', 'slow')  # 只有公开类型元数据，绝不将隐藏提交情况交给策略
BASELINES = {
    'burst': Schedule((0, 1, 2)),
    'uniform': Schedule((0, 6, 12)),
    'backoff': Schedule((0, 4, 12)),
}


@dataclass(frozen=True)
class Episode:
    seed: int
    shift: int
    provider: str
    method: str
    confirmed: int
    reads: int
    first_confirmation_tick: int
    false_confirmation: int
    write_dispatches: int


def sample(seed: int, count: int, *, shifted: bool = False) -> list[ObservationTrace]:
    """隐藏真值只在生成夹具和评分时使用，所有方法共享完全相同轨迹。"""
    if count < 1:
        raise ValueError('数据集不能空')
    rng = random.Random(seed)
    out = []
    distributions = {
        'fast': ((0, 2, 4, 6, 8), (50, 25, 10, 10, 5)),
        'slow': ((0, 4, 6, 8, 10, 12), (5, 10, 15, 30, 25, 15)),
    }
    for provider in PROVIDERS:
        kind = ('slow' if provider == 'fast' else 'fast') if shifted else provider
        values, weights = distributions[kind]
        for _ in range(count):
            # 8% 的外部操作从未提交，8% 的已提交操作始终没有可采信回执。
            available = rng.random()
            first = rng.choices(values, weights=weights)[0] if available >= 0.16 else None
            out.append(ObservationTrace(provider, first))
    rng.shuffle(out)
    return out


def evaluate(trace: ObservationTrace, schedule: Schedule, *, method: str,
             seed: int, shifted: bool) -> Episode:
    # 只观察是否存在可采信正向证据；缺失并不证明没有提交。
    selected = next((i for i, tick in enumerate(schedule.ticks)
                     if trace.first_valid_tick is not None and tick >= trace.first_valid_tick), None)
    confirmed = selected is not None
    return Episode(seed, int(shifted), trace.provider, method, int(confirmed),
                   len(schedule.ticks) if selected is None else selected + 1,
                   schedule.ticks[selected] if confirmed else -1,
                   0, 0)


def run(*, test_count: int = 200, train_count: int = 100) -> list[Episode]:
    if min(test_count, train_count) < 15:
        raise ValueError('训练与测试的每类样本至少 15 条')
    rows = []
    for seed in SEEDS:
        train = sample(seed + 10000, train_count)
        fitted = fit_scheduler(train)
        # 全局训练最优是强基线；类型感知算法不独享拟合能力。
        candidates = {**BASELINES, 'pooled_tuned': fitted.pooled}
        for shifted in (False, True):
            for trace in sample(seed + 20000, test_count, shifted=shifted):
                for label, sched in candidates.items():
                    rows.append(evaluate(trace, sched, method=label,
                                         seed=seed, shifted=shifted))
                rows.append(evaluate(trace, fitted.choose(trace.provider),
                                     method='provider_tuned', seed=seed, shifted=shifted))
    return rows


def summarize(rows: list[Episode]) -> list[dict]:
    output = []
    for shifted in (0, 1):
        for method in (*BASELINES, 'pooled_tuned', 'provider_tuned'):
            items = [r for r in rows if r.method == method and r.shift == shifted]
            output.append({'shift': shifted, 'method': method, 'episodes': len(items),
                           'confirmed': sum(x.confirmed for x in items),
                           'reads': sum(x.reads for x in items),
                           'mean_confirmation_tick': round(
                               sum(x.first_confirmation_tick for x in items if x.confirmed)
                               / max(1, sum(x.confirmed for x in items)), 3),
                           'false_confirmation': sum(x.false_confirmation for x in items),
                           'write_dispatches': sum(x.write_dispatches for x in items)})
    return output


def write_csv(path: Path, data: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)


def main() -> int:
    parser = argparse.ArgumentParser(description='EAP 影子调度：训练/测试分离及分布变化负测')
    parser.add_argument('--output', type=Path, default=Path('results/scheduler_pilot'))
    args = parser.parse_args()
    rows = run()
    summary = summarize(rows)
    write_csv(args.output / 'episodes.csv', [asdict(r) for r in rows])
    write_csv(args.output / 'summary.csv', summary)
    for item in summary:
        print(item)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
