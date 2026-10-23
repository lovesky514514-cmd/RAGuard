"""对原始 Episode 数据进行指标汇总与配对区间估计。"""

from collections import defaultdict
from math import ceil, isfinite
from pathlib import Path
import csv
import random


FIELDS = ('acknowledged_success', 'physical_goal', 'unsafe', 'escalated',
          'extra_calls', 'latency_ms', 'fault_fired')


def rows_from_csv(path: Path) -> list[dict]:
    with path.open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


def quantile(values: list[float], fraction: float) -> float:
    """采用 nearest-rank 百分位；小样本结果必须附样本数。"""
    if not values or not 0 < fraction <= 1 or any(not isfinite(x) for x in values):
        raise ValueError('样本、分位点或数值不合法')
    return sorted(values)[max(0, ceil(fraction * len(values)) - 1)]


def aggregate(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[(row['method'], row['tool'], row['fault'], row['rate'], row['no_readback'])].append(row)
    summary = []
    for (method, tool, fault, rate, no_readback), subset in sorted(groups.items()):
        n = len(subset)
        totals = {field: sum(float(row[field]) for row in subset) for field in FIELDS}
        latencies = [float(row['latency_ms']) for row in subset]
        summary.append(dict(method=method, tool=tool, fault=fault, rate=rate,
                            no_readback=no_readback, n=n,
                            success_n=int(totals['acknowledged_success']),
                            unsafe_n=int(totals['unsafe']),
                            escalation_n=int(totals['escalated']),
                            injected_n=int(totals['fault_fired']),
                            success_rate=round(totals['acknowledged_success']/n, 6),
                            physical_goal_rate=round(totals['physical_goal']/n, 6),
                            unsafe_rate=round(totals['unsafe']/n, 6),
                            escalation_rate=round(totals['escalated']/n, 6),
                            avg_extra_calls=round(totals['extra_calls']/n, 6),
                            p50_ms=round(quantile(latencies, .5), 3),
                            p95_ms=round(quantile(latencies, .95), 3)))
    return summary


def paired_difference(rows: list[dict], left='raguard', right='b3',
                      metric='acknowledged_success', samples=1000,
                      seed=20261023) -> dict:
    """按工具、故障、强度和重复编号配对；不跨配置乱配。"""
    def pair_key(row: dict):
        return (row['tool'], row['fault'], row['rate'],
                row['repeat'], row['seed'], row['no_readback'])

    if samples < 100:
        raise ValueError('bootstrap 重抽样次数至少 100')

    def indexed(method: str) -> dict:
        index = {}
        for row in rows:
            if row['method'] != method:
                continue
            key = pair_key(row)
            if key in index:
                raise ValueError(f'{method} 存在重复试验键：{key}')
            index[key] = float(row[metric])
        return index

    lhs, rhs = indexed(left), indexed(right)
    if not lhs or not rhs:
        raise ValueError('缺少完整的基线配对')
    if lhs.keys() != rhs.keys():
        missing_left = len(rhs.keys() - lhs.keys())
        missing_right = len(lhs.keys() - rhs.keys())
        raise ValueError(f'试验配对不完整：左侧缺 {missing_left}，右侧缺 {missing_right}')
    keys = sorted(lhs)
    deltas = [lhs[key] - rhs[key] for key in keys]
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(deltas, k=len(deltas)))/len(deltas)
                   for _ in range(samples))
    return dict(metric=metric, left=left, right=right, pairs=len(keys),
                delta_mean=round(sum(deltas)/len(deltas), 6),
                ci95_lower=round(quantile(means, .025), 6),
                ci95_upper=round(quantile(means, .975), 6),
                note='配置混合的描述性区间；最终论文需按故障与业务域分层。')


def paired_by_stratum(rows: list[dict], metric: str = 'acknowledged_success') -> list[dict]:
    """逐工具和故障环境报告效应，不用混合均值替代异质性。"""
    groups = defaultdict(list)
    for row in rows:
        groups[(row['tool'], row['fault'], row['rate'], row['no_readback'])].append(row)
    output = []
    for (tool, fault, rate, no_readback), subset in sorted(groups.items()):
        if not {'b3', 'raguard'} <= {r['method'] for r in subset}:
            continue
        result = paired_difference(subset, metric=metric)
        output.append({'tool': tool, 'fault': fault, 'rate': rate,
                       'no_readback': no_readback, **result})
    return output


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        raise ValueError('结果不能是空列表')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, rows: list[dict], *, mode: str):
    """报告只根据真实 CSV 统计生成，不填入预设的成功百分比。"""
    summary = aggregate(rows)
    lines = [f'# RAGuard {mode} 实验结果', '',
             f'共执行 {len(rows)} 个隔离 Episode，结果来自真实 Go HTTP 沙箱。', '',
             '注意：该文件不是科研结论。未运行的完整矩阵、跨 Agent 验证、统计功效均不能宣称完成。', '',
             '| 方法 | 样本 | 已确认成功 | 发生不安全副作用 | 升级停止 |',
             '|---|---:|---:|---:|---:|']
    for method in sorted({r['method'] for r in rows}):
        subset = [r for r in rows if r['method'] == method]
        n = len(subset)
        success = sum(int(r['acknowledged_success']) for r in subset)
        unsafe = sum(int(r['unsafe']) for r in subset)
        escalated = sum(int(r['escalated']) for r in subset)
        lines.append(f'| {method} | {n} | {success}/{n} | {unsafe}/{n} | {escalated}/{n} |')
    lines.extend(['', '成功率同时要求最终业务状态正确和响应已确认；不安全率基于物理写入，不能靠补偿抹掉重复历史。',
                  '不同方法采用同一故障选择和同一真实工具沙箱，每个 Episode 的世界状态单独初始化。',
                  '单步骤矩阵不包含 B4；跨步骤 Saga 基线使用独立的工作流协议，不能混入单步骤指标。',
                  '低故障率和很少的重复次数不具备检验功效。',
                  '混合区间仅作探索展示；严格配对检查会拒绝缺行和重复键。'])
    if {'b3', 'raguard'} <= {r['method'] for r in rows}:
        for metric in ('acknowledged_success', 'unsafe'):
            v = paired_difference(rows, metric=metric)
            lines += ['', f"配对差值 {metric}（RAGuard - B3）：{v['delta_mean']:.4f}，"
                          f"95% bootstrap 区间 [{v['ci95_lower']:.4f}, {v['ci95_upper']:.4f}]，"
                          f"配对 {v['pairs']} 组。"]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
