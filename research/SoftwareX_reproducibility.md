# RAGuard reproducibility and evidence register

**Status:** author-review research artifact, not independent replication or production certification.

## Frozen source and claims

- Source baseline: `research/audited-recovery-http`; documented CI run: `41217991475`.
- Paper preparation: `research/softwarex-submission` (do not confuse with tested baseline commit).
- The experiment integrates a trusted local Go HTTP readback with B5 and RAGuard persistent recovery through a shared Python audit adapter.
- The adapter blocks all recovery-stage external writes on **both** engines. Therefore the observed absence of re-execution is an experimental boundary, not a unique benefit of RAGuard.
- Commit receipts are assumed authentic solely because they came from a trusted loopback server. They are **not** independently cryptographically signed.
- No deployment to production systems, independent cloud service, or real financial operations is claimed.

## Exact baseline reproduction

Obtain the pinned source branch from GitHub, using Git and Python 3.12 and Go 1.23 (CI toolchain). Run from repository root:

```bash
python scripts/check_utf8.py
python scripts/check_public_release.py
python scripts/verify_manifest.py
python -m unittest discover -s tests -v
python -m unittest discover -s bench -p 'test_*.py' -v
cd mock
go test ./...
go vet ./...
cd ..
python -m bench.audited_recovery_http --output results/audited_recovery_http
```

The main experiment requires local port binding and a working Go compiler. It must not be run against live production endpoints.

## What is actually measured?

The experiment uses two engines (`b5`, `raguard`), two delivery fault stages (`before_commit`, `after_commit`) and eleven readback variants, yielding 44 cases. The evaluator separately reads the Go service's actual physical effect ledger, which is never supplied to the recovery policy. The run asserts both no new physical effects and no new HTTP execution attempts during recovery.

| Metric | Verified observation | Interpretation |
| --- | ---: | --- |
| Total cases | 44 | Fixed deterministic matrix, not 44 independent production samples |
| Safe confirmations | 2 | One validated after-commit confirmation per engine |
| Unresolved cases | 42 | Deliberate fail-closed behavior under no/invalid evidence |
| Unsafe confirmations | 0 observed | Does not establish a population safety guarantee |
| Added external write attempts in recovery | 0 observed | Both engines use the same write-blocking adapter |
| Engine advantage | None observed | B5 and RAGuard produce paired-identical results |

## Artifact verification

Retrieve `audited-recovery-http-ci` from the verified baseline workflow run. The artifact contains `episodes.csv`, `protocol.json`, `REPORT.md` and `SHA256SUMS.json`. Check the SHA-256 of each named file against the digest in the manifest. The GitHub Actions artifact expires according to retention policy; a permanent DOI archive **has not yet been completed**.

The per-case file records `engine`, `fault`, `variant`, `initial_status`, `final_status`, `audit_decision`, `physical_effects`, `safe_confirmation`, `unsafe_confirmation`, `duplicate_effects`, `blocked_recovery_writes`, `queries`, `real_execute_attempts`, `added_execute_attempts`, `unresolved` and `audit_reason`.

Review raw CSV values before final manuscript submission. Do not imply that this study tests cryptographic receipts, true server-side fencing, actual provider-specific idempotency contracts, or statistical superiority.

## Independent reproduction acceptance

A second investigator should:
1. Pin the exact research commit and record Python, Go, operating system, architecture, and SHA-256 checks.
2. Repeat the experiment in a clean environment without access to the authors' working directory.
3. Verify the expected complete set of 44 conditions and compare paired per-case states.
4. Inspect the physical-effect log as evaluator-only data and report any failed assertion.
5. Repeat with a genuinely independent server implementation or different tool contract before claiming external validity.

## Disclosure and privacy

No personal paths, private email, real API credentials, customer data or protected business identifiers are needed. Confirm copyright ownership and all third-party licensing before depositing a permanent archive.

## Submission gates

**Open:** permanent repository release and DOI, independent environment reproduction, complete comparison against LIMBO/Verified Tool Calls with full texts, journal formatting, institutional author metadata, authorship/AI use statement and funding disclosure. These cannot be marked complete from the present CI data alone.
