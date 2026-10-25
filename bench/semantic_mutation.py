"""Frozen, mutation-labelled synthetic traces for evidence-contract drift.

No Provider credentials, network calls, or physical writes are used. Reports
must not be described as a deployed anomaly detector or novel algorithm.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from pathlib import Path

from raguard.evidence import EvidenceContract, EvidenceObservation
from raguard.semantic_guard import SemanticPolicy, audit_stream

POLICY = SemanticPolicy(unique_effect_within_tool=True,
                        monotonic_snapshot_version=True,
                        stable_effect_per_business=True)
CLASSES = ('clean', 'cross_tool_reuse', 'cross_business_reuse',
           'version_rollback', 'business_rebound', 'commit_then_absent')


@dataclass(frozen=True)
class Trial:
    scenario: str
    mutation: str
    expected_alert: int
    baseline_detected: int
    semantic_detected: int
    false_positive: int
    admitted: int


def contract(ref: str, tool: str = 'tickets.create') -> EvidenceContract:
    return EvidenceContract(tool, ref, 'fixed-payload', 'sandbox', 1, 100.0, 1)


def observation(ref: str, effect: str, *, tool='tickets.create',
                version=2, outcome='committed') -> EvidenceObservation:
    return EvidenceObservation(tool, ref, 'fixed-payload', 'sandbox', 1, 90.0,
                               version, outcome, effect,
                               verified=True, final=True)


def fixture(kind: str, n: int):
    """Each mutation changes the *temporal relationship*, not the JSON schema."""
    if kind not in CLASSES or n < 0:
        raise ValueError('Unregistered mutation class')
    a, b = f'operation-{n}-a', f'operation-{n}-b'
    one, two = f'effect-{n}-a', f'effect-{n}-b'
    lookup = {a: contract(a), b: contract(b)}
    first = observation(a, one)
    if kind == 'clean':
        events = (first, observation(b, two), observation(a, one, version=3))
    elif kind == 'cross_tool_reuse':
        lookup[b] = contract(b, 'orders.create')
        events = (first, observation(b, one, tool='orders.create'))
    elif kind == 'cross_business_reuse':
        events = (first, observation(b, one))
    elif kind == 'version_rollback':
        events = (first, observation(a, one, version=1))
    elif kind == 'business_rebound':
        events = (first, observation(a, two, version=3))
    else:
        events = (first, observation(a, '', version=3, outcome='absent'))
    return lookup, events


def run(repeats: int = 8) -> list[Trial]:
    if type(repeats) is not int or not 1 <= repeats <= 100:
        raise ValueError('repeats must be between 1 and 100')
    trials = []
    for kind in CLASSES:
        for n in range(repeats):
            contracts, stream = fixture(kind, n)
            result = audit_stream(stream, contracts, now=100.0, policy=POLICY)
            # Legacy reference uses all available observations for each business,
            # not only the final response. It can detect some temporal conflicts.
            baseline = int(result.legacy_conflicts > 0)
            # Final/absence conflict is *already* caught by the existing method.
            new = int(bool(result.alerts) or baseline)
            target = int(kind not in ('clean', 'cross_tool_reuse'))
            trials.append(Trial(f'{kind}-{n:02}', kind, target, baseline, new,
                                int(new and not target), result.admitted))
    return trials


def main() -> int:
    parser = argparse.ArgumentParser(description='Synthetic semantic-contract mutations')
    parser.add_argument('--output', type=Path, default=Path('results/semantic_mutation'))
    args = parser.parse_args()
    rows = run()
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'episodes.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0])))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    lines = ['# Evidence semantics: shadow-only mutation checks', '',
             '48 synthetic scenarios (8 in each of 6 predeclared classes).',
             'Both detectors see the same evidence history. No real API or writes.',
             'Existing accumulated assess_evidence is the baseline; not strawman schema-only.', '',
             '| Mutation | Cases | Existing conflicts | With temporal invariants | False flags |',
             '| --- | ---: | ---: | ---: | ---: |']
    for kind in CLASSES:
        group = [r for r in rows if r.mutation == kind]
        lines.append(f'| {kind} | {len(group)} | '
                     f'{sum(r.baseline_detected for r in group)} | '
                     f'{sum(r.semantic_detected for r in group)} | '
                     f'{sum(r.false_positive for r in group)} |')
    lines.extend(['', 'A synthetic mutation label is NOT a real-world ground truth.',
                  'Provider-declared effect-ID scope and version monotonicity must be verified;',
                  'otherwise the guard stays disabled (fail-open for detection, not write authorization).',
                  'These are known temporal-consistency invariants, not a new algorithm.'])
    (args.output / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('Completed', len(rows), 'synthetic scenarios')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
