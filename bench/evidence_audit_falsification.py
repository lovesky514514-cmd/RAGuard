"""穷举授权条件并独立检测错误确认、重发与冲突降级。"""

from dataclasses import replace
from itertools import product
import json

from bench.evidence_audit import Label, Reading, Source, inspect
from bench.evidence_frontier import Context, Decision, Evidence, Kind


def verify():
    context = Context("intent", "scope", "hash", "issuer", 1, 4, False, False)
    base = Evidence(Kind.COMMITTED, "intent", "scope", "hash", "issuer", 1, 20,
                    authenticated=True, final=True)
    checked = confirmations = 0
    for label, kind, scope_ok, auth, final, can_confirm, lease, valid, now, prior in product(
        (Label.COMMIT, Label.HARD_CONFLICT, Label.ABSENT),
        (Kind.COMMITTED, Kind.FINAL_NON_EXECUTION), (True, False),
        (True, False), (True, False), (True, False),
        (5, 30), (3, 20), (1, 4, 6, 21), (True, False)
    ):
        proof = replace(base, kind=kind, scope="scope" if scope_ok else "other",
                        authenticated=auth, final=final, valid_until=valid)
        claims = (proof,) if label == Label.COMMIT else (
            (proof, replace(proof, kind=Kind.FINAL_NON_EXECUTION))
            if label == Label.HARD_CONFLICT else ())
        source = Source("reader", 2, 1, can_confirm, True, lease)
        history = (replace(base, kind=Kind.FINAL_NON_EXECUTION),) if prior else ()
        outcome = inspect(context, source, Reading(label, claims), now, history)
        expected = (label == Label.COMMIT and kind == Kind.COMMITTED
                    and scope_ok and auth and final and can_confirm
                    and 4 <= now <= min(lease, valid) and not prior)
        assert (outcome.action == Decision.CONFIRM) == expected
        assert outcome.action != Decision.REISSUE
        if (label == Label.HARD_CONFLICT and scope_ok and auth and final
                and can_confirm and kind == Kind.COMMITTED):
            assert outcome.terminal and outcome.action == Decision.STOP
        checked += 1
        confirmations += int(expected)
    return {"cases": checked, "confirmations": confirmations,
            "invalid_authorizations": 0, "scope": "finite_simulation_only"}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False))
