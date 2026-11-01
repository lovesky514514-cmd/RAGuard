# RAGuard: A reproducible Python–Go testbed for ambiguous side-effect recovery in tool-using agents

*Original Software Publication — English author-review draft, 1 November 2026. This Markdown document is not a substitute for the current official SoftwareX Word/LaTeX template. Author metadata and archival software DOI remain to be completed.*

**Authors:** [Author list and order to be confirmed]  
**Affiliations:** [Institutional English names to be confirmed]  
**Corresponding author:** [Authorized public contact to be confirmed]

## Abstract

A timed-out tool write may have committed even when an agent receives no acknowledgement. This ambiguity complicates reliable retries and makes task-level success an insufficient measure of external side-effect safety. RAGuard is an Apache-2.0 Python–Go research software framework for reproducing and auditing these failures in a local environment. Its Python layer provides stable business identities, persistent recovery records, an independently implemented durable B5 baseline, and evidence-aware experimental adapters. An isolated Go HTTP service injects controlled faults and retains a physical-effect ledger accessible to the evaluator but not to the recovery policy. The framework records safe confirmations, unresolved outcomes, duplicate effects, actual write attempts, and evidence-query costs, with paired-case execution and SHA-256 result manifests. In a restricted 44-episode HTTP study across two recovery engines, two fault positions, and eleven evidence variants, both engines produced the same outcomes: two safe confirmations and 42 unresolved episodes, with no observed unsafe confirmation or recovery-stage HTTP write. The latter property is enforced by a shared experimental no-reissue adapter, not by a production service-side fencing guarantee. RAGuard is therefore presented as a transparent and reproducible research instrument for exploring the limits of observable tool-recovery contracts, rather than as a novel exactly-once algorithm.

**Keywords:** research software; LLM agents; side-effect recovery; fault injection; idempotency; reproducibility; evidence audit

## Code metadata

| Field | Description |
|---|---|
| Software name | RAGuard |
| Repository | https://github.com/lovesky514514-cmd/RAGuard |
| Research source snapshot | https://github.com/lovesky514514-cmd/RAGuard/tree/research/submission-readiness |
| Reference commit for evaluated recovery pipeline | `3883d10744d0168afc80ffce89d5cc4ad62ead11` |
| Software version | Research snapshot; formal release tag not yet created |
| Permanent archived code DOI | **Pending** (do not substitute an unrelated DOI) |
| License | Apache License 2.0 |
| Languages and dependencies | Python 3.12; Go 1.23 in the tested GitHub Actions environment; Python 3.11+/Go 1.21+ are documented local targets |
| Platforms evaluated | GitHub Actions Linux and Windows; full HTTP evidence archive produced on Linux |
| User documentation | `README.md`, `docs/USAGE.md`, `docs/SECURITY.md`, `docs/EXPERIMENTS.md` |
| Contact | To be supplied by authors after privacy review |

## 1. Motivation and significance

Tool-using agents invoke external APIs whose effects are not atomic with the delivery of their responses. A lost acknowledgement, an in-flight request, or an expired server-side idempotency record can leave an agent unable to determine whether a mutation took effect. An optimistic retry may duplicate the action; a conservative stop may leave a legitimate task unfinished. These outcomes should not be collapsed into a binary task-success indicator.

Idempotent API contracts and stable caller-supplied request identifiers are established remedies in distributed systems [1]. In agent research, Verified Tool Calls studies postcondition verification and verify-before-retry wrappers [2]. LIMBO offers a much broader deterministic benchmark, including six services, twelve fault modes, and 25,930 evaluated episodes, and demonstrates the limits of verification-only recovery under unbounded late commits [3]. RAGuard does not claim to originate these ideas, to outperform LIMBO, or to ensure exactly-once execution.

The software's proposed utility is a **compact, inspectable recovery-evaluation workflow** in which persistent business identity, recovery-visible evidence, and externally recorded physical effects can be examined separately. It enables researchers to reproduce disagreement between what a recovery algorithm can observe and what the isolated service actually committed; to contrast equally provisioned recovery implementations; and to report the cost of conservative suspension without concealing negative results. The present project has been used in internal controlled experiments. Independent external adoption has not been demonstrated.

## 2. Software description

### 2.1. Architecture and design separation

RAGuard separates the following responsibilities:

1. **Experiment input and identity:** Python maps a trusted business reference and a logical intent to stable recovery identifiers. Agent-authored descriptions alone do not establish a new business identity.
2. **Durable recovery implementations:** A persistent baseline (B5) and the RAGuard Gateway are exercised independently. The former is not implemented as a call to the latter.
3. **Isolated effect simulator:** A local Go HTTP service injects pre-commit and post-commit failures and retains committed physical effects.
4. **Evidence and evaluation:** Recovery adapters receive only permitted readbacks. A separate evaluator reads physical-effect truth and computes outcomes.
5. **Research outputs:** Each protocol writes structured CSV data, a protocol description, a human-readable report, and checksums.

The experimental data path is:

`declared case → B5 or Gateway → Go HTTP side effect → controlled loss of response → recovery-visible readback → persistent decision`

A separate observer compares this decision with the Go service's physical-effect ledger. The physical-effect log must never be passed to the policy as an omniscient oracle.

### 2.2. Evidence semantics and controlled fault injection

A missing observation is not equivalent to a proof of non-execution. An old positive receipt may establish that a historical event occurred, but an expired statement of current status cannot necessarily authorize a new action. The research evidence audit checks semantic proof type, trusted issuer, business identity, scope, execution generation, validity bounds, and inconsistent terminal claims. Invalid, unauthorized, or contradictory statements fail closed.

The evaluated research adapter also blocks all second writes during the recovery phase for **both** engines. This isolates the behavior of readback-driven confirmation. It is intentionally not represented as an independently implemented production Gateway guarantee or as a server-side execution fence. The local trusted-loopback assumption is not a cryptographic signature protocol. Contradictory and malformed proofs are injected at the Python experimental boundary, rather than being issued by the upstream Go service.

### 2.3. Implementation and reuse

The project uses Python for orchestration, persistent recovery evaluation, and analysis, and Go for the networked side-effect simulator. Its tests include adverse evidence combinations and independent reference checks. Research scripts are command-line tools with explicit output directories; experimental artifacts do not modify live external services. The default distribution uses an Apache-2.0 license and contains a source manifest for detecting accidental changes to the studied code.

## 3. Illustrative example and reproducibility

An ambiguous write is sent to the local sandbox. The network adapter drops the acknowledgement either before or after the service applies its physical effect. The same declared case is run with B5 and the Gateway. On recovery, the adapter performs a permitted readback; only an evidence-audited positive receipt can lead to confirmation. If the proof is absent, expired, scope-mismatched, unsupported, or contradictory, the system retains an unresolved state. The evaluator independently checks that no new physical effect or write attempt occurred after recovery began.

The reference experiment is reproducible from the archived study commit:

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s bench -p 'test_*.py' -v
cd mock && go test ./... && cd ..
python -m bench.audited_recovery_http --output results/audited_recovery_http
python scripts/verify_manifest.py
```

Commands must be executed from the repository root except for the explicitly entered Go module directory. A working Python and Go toolchain is required to rebuild the Go service. The published CI run and accompanying archive provide the experimental CSV, protocol parameters, report, and SHA-256 digests. These checks establish data integrity, not reproducibility on a second laboratory's independently provisioned machine.

## 4. Empirical evaluation

The restricted audited-recovery study is a **finite, predeclared deterministic matrix**, not a representative random sample of production failures. It crosses two engines (B5 and RAGuard), two fault positions (before and after commit), and eleven readback variants, for 44 isolated Go HTTP episodes.

| Evaluated quantity | Observed total |
|---|---:|
| Independent episode executions | 44 |
| Safe confirmations | 2 |
| Unresolved outcomes | 42 |
| Unsafe confirmations | 0 |
| Duplicate physical effects | 0 |
| New recovery-stage HTTP write attempts | 0 |
| B5–RAGuard mismatched paired outcomes | 0 |

A valid positive receipt after a committed write permits one safe confirmation per engine. Cases lacking acceptable positive proof remain unresolved. Under the common no-reissue restriction, the measured outcome is intentionally **conservative**, not evidence that one engine achieves superior completion rates. Zero observed unsafe confirmations in 44 deterministic episodes is not a confidence bound on operational failure risk.

Separate exploratory experiments examine source freshness, visibility and evidence-query budgets. The candidate conditional evidence planner can exceed a weak fixed-route rule in a synthetic model, but **ties a metadata-aware handcrafted rule at matched cost**. Consequently, this software publication does not present the planner as an established novel algorithm or report statistical superiority.

## 5. Research impact, comparison, and limitations

LIMBO [3] studies agent behavior, harness effects, and tool contracts across a substantially broader benchmark; Verified Tool Calls [2] evaluates verification-aware wrappers. RAGuard's practical emphasis is smaller and more specialized: exposing recovery-state persistence, side-effect ground truth, evidence audit decisions, and matched independent baseline runs as inspectable software artifacts. This is a **design distinction**, not yet a demonstrated improvement over those research systems. No cross-framework head-to-head execution or external user study has been completed.

The system can support controlled research questions about how identity stability, idempotency-window expiry, readback authority, and recovery-time write barriers affect confirmation versus suspension. It can also help identify implementation errors in evidence validation before connecting to production services.

Important limits remain. The Go service models only a controlled subset of asynchronous and distributed failure behavior. Simulated authority and source freshness are not real vendor signing or fencing semantics. The common experimental block on recovery writes limits causal claims about algorithmic safety. The evaluation has not demonstrated a new upper bound on late commits, cryptographic terminal non-execution proof, or robust behavior against malicious servers. The release lacks a public immutable software archive DOI and an independent installation report at the time of drafting.

## 6. Conclusions

RAGuard supplies a reproducible Python–Go research workflow for investigating ambiguous external write outcomes without conflating visible acknowledgements with committed effects. Its strongest current contribution is an auditable implementation and paired fault-evaluation protocol with explicit negative findings. The package is useful as a focused research instrument; claims of production safety, broad scientific novelty, external adoption, or algorithmic superiority require further evidence.

## Declarations and availability

**Code availability:** Public GitHub repository at the URL above. A permanent DOI and tagged release are required before final submission.

**Data availability:** CI artifact `audited-recovery-http-ci`, produced by GitHub Actions run `41217991475`; result files include `episodes.csv`, `protocol.json`, `REPORT.md`, and `SHA256SUMS.json`. GitHub CI artifact retention is limited; the authors must archive the validated data permanently before submission.

**Generative AI assistance:** AI-assisted tools supported some software implementation, testing, research synthesis and manuscript drafting. Human authors must verify all technical claims, source references, experimental outputs and authorship statements and revise this disclosure to comply with the chosen journal's policy. No AI tool is listed as an author.

**Funding, conflicts of interest, author contributions, institutional permission and corresponding-author details:** To be confirmed by the human authors; none is assumed.

## References

[1] M. Featonby, “Making retries safe with idempotent APIs,” *Amazon Builders' Library*, 2021. https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/

[2] I. K. Mansoor, A. Phadke, P. Rana, “Verified Tool Calls Improve LLM Agent Reliability Under Non-Atomic Failures,” *arXiv* 2608.02645, 2026. https://arxiv.org/abs/2608.02645

[3] J. Li, “Where Does Exactly-Once Live? Model, Harness, and Tool-Contract Effects on Duplicate Side Effects in LLM Agents,” *arXiv* 2609.29095, 2026. https://arxiv.org/abs/2609.29095
