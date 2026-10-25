"""Experimental cross-observation evidence invariants; audit-only, never dispatches writes.

The invariants are **provider-declared**: neither uniqueness of effect IDs nor
monotonic versions may be inferred from response field names alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .evidence import (EvidenceContract, EvidenceDecision,
                       EvidenceObservation, assess_evidence)


@dataclass(frozen=True)
class SemanticPolicy:
    """Enable checks only after validating the provider's identity/version semantics."""

    unique_effect_within_tool: bool = False
    monotonic_snapshot_version: bool = False
    stable_effect_per_business: bool = False


@dataclass(frozen=True)
class Alert:
    index: int
    reason: str
    business_ref: str


@dataclass(frozen=True)
class Audit:
    admitted: int
    unadmitted: int
    alerts: tuple[Alert, ...]
    legacy_conflicts: int


def audit_stream(
    stream: Sequence[EvidenceObservation],
    contracts: Mapping[str, EvidenceContract],
    *,
    now: float,
    policy: SemanticPolicy,
) -> Audit:
    """Check chronological *readback arrivals* against explicit provider guarantees.

    The existing whole-history assess_evidence remains the strong reference
    baseline. An alert means manual review/safe stop, not proof of misconduct.
    Missing/unverified evidence is excluded from temporal invariant state.
    """
    seen: dict[str, list[EvidenceObservation]] = {}
    states: dict[str, tuple[int, str]] = {}
    effect_owners: dict[tuple[str, str, int, str], str] = {}
    alerts: list[Alert] = []
    legacy_conflicts = 0
    admitted = 0
    unadmitted = 0

    for index, event in enumerate(stream):
        contract = contracts.get(event.business_ref)
        if contract is None:
            unadmitted += 1
            continue
        seen.setdefault(event.business_ref, []).append(event)
        historical = assess_evidence(contract, tuple(seen[event.business_ref]), now)
        if historical.decision == EvidenceDecision.ESCALATE:
            legacy_conflicts += 1
        current = assess_evidence(contract, (event,), now)
        if (current.decision != EvidenceDecision.CONFIRMED
                or event.outcome != 'committed'):
            unadmitted += 1
            continue

        admitted += 1
        previous = states.get(event.business_ref)
        if previous is not None:
            version, effect = previous
            if policy.monotonic_snapshot_version and event.version < version:
                alerts.append(Alert(index, 'version_rollback', event.business_ref))
            if policy.stable_effect_per_business and event.effect_id != effect:
                alerts.append(Alert(index, 'business_effect_rebound', event.business_ref))

        if policy.unique_effect_within_tool:
            key = (event.authority, event.tool, event.authority_epoch, event.effect_id)
            owner = effect_owners.get(key)
            if owner is not None and owner != event.business_ref:
                alerts.append(Alert(index, 'effect_id_reused_across_businesses',
                                    event.business_ref))
            effect_owners.setdefault(key, event.business_ref)

        # Do not let a suspect rollback overwrite the greatest known version.
        if previous is None or event.version > previous[0]:
            states[event.business_ref] = (event.version, event.effect_id)
    return Audit(admitted, unadmitted, tuple(alerts), legacy_conflicts)
