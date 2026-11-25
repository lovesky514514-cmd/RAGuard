# SQJ research artifact: historical evidence snapshot (2026-11-25)

This archive preserves the historical experimental outputs used in the SQJ manuscript, *Auditing Recovery Reliability in Tool-Using AI Agents: A Fault-Injection Study of Evidence Validity and Execution Constraints*. It does not include the unpublished manuscript or publisher template.

| Study | Original results package | SHA-256 |
| --- | --- | --- |
| Pilot | `RAGuard_Real_LLM_Results.zip` | `d0446c603bccea3969350430dd1716088342827cb042152673947059a8d5da83` |
| Stage 3 | `RAGuard_Stage3_Results.zip` | `15028eff6efd2f88dd56ef5f6a5899846d6905f2ab99d823e209425ce4aceba8` |
| Stage 4 | `RAGuard_Stage4_Results.zip` | `ab608262177b34b63ae47918a2e87c095f9de008c8f1ece69f506675cd940f10` |
| Stage 5 | `RAGuard_Stage5_Results_3repeats.zip` | `d43549f9d0fe2e898abe012c1d9d6e96dcfe8510ebee1dad06d309add64af1aa` |
| Stage 6 | `RAGuard_Stage6_Results.zip` | `0a67dc480ebcae2d8a59cb1a9ae7a615d8d16a9774da556f9e6fbff69617cad7` |

All five original ZIP packages passed CRC and their internal SHA-256 checks before archiving. Extract the ZIP files without changing their contents to verify the embedded `SHA256SUMS.json`. These checks establish byte integrity, not independent replication or scientific validity.

The original 44-episode calibration is preserved separately in `../audited_recovery_http_20261031/` and the historical `v1.1.0-research` release (DOI: https://doi.org/10.5281/zenodo.24392269). The evaluated source revision for that protocol is `d6604a3781b28cf164c125e8c6ae6c332a8effe9`.

Historical source branch snapshots recorded in the 2026-11-24/25 audit:

- Stage 3: `439aadf16a12fb23f6d9c43e3b516e4fa58f3df6` (`research/sqj-stage3-evidence-conditioned`)
- Stage 4: `1524481f2d5a1760fa49a8bd328449f99dad2dba` (`research/sqj-stage4-evidence-policy`)
- Stage 5: `be1441a8faa3f99883560eacf54c1226594e04a4` (`research/sqj-stage5-contract-boundary`)
- Stage 6: `a6821f71bf461db23d99235fb0ef48136e2d9aee` (`research/sqj-stage6-concurrent-recovery`)

These are source references, not proof that every historical output was regenerated from the archive commit. The pilot and Stages 3-4 contain real local Qwen advice with incomplete raw model traces; Stages 5-6 use scripted advice. The experiments have different observation units and must not be pooled as independent samples. Shared admission rules and simulated provider conditions limit causal attribution; the Stage 6 verified treatment uses supervisor-provided pending-state information. No general exactly-once guarantee or production failure-rate estimate is claimed.
