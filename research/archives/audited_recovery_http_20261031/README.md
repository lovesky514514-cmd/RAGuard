# Archived research evidence: audited recovery over local HTTP

This directory preserves the **pre-existing** 44-case result files from GitHub Actions run 41217991475, artifact `audited-recovery-http-ci`, dated 2026-10-31.

- Exact evaluated source commit: `d6604a3781b28cf164c125e8c6ae6c332a8effe9` (`research/audited-recovery-http` at experiment time).
- Archived raw files: `episodes.csv`, `protocol.json`, `REPORT.md`, `SHA256SUMS.json`.
- Study design: 2 engines x 2 fault positions x 11 evidence variants = 44 isolated episodes.
- Outcomes: 2 safe confirmations (1/engine), 42 unresolved (21/engine), 0 observed unsafe confirmations, 0 duplicate physical effects, 0 added real HTTP writes.
- Crucial scope restriction: **both engines** share a research-only no-reissue adapter; this does not prove production exactly-once guarantees or an RAGuard advantage.
- SHA-256 values reflect the **original exported bytes**. Text transfers may normalize line endings: check them before calling this an exact byte-identical copy. Never replace the original source artifact without explicitly recording any normalization.
- This Git branch/PR is a public source-control preservation layer. It is **not** a permanent DOI archive.
- Prior to publishing a release: verify the record, authors' consent, license, personal-data scan, and transfer to Zenodo.

To independently reproduce from the *evaluated commit* (not this documentation branch):
```bash
git checkout d6604a3781b28cf164c125e8c6ae6c332a8effe9
python -m bench.audited_recovery_http --output results/audited_recovery_http
```
The run requires Python and Go toolchains and local loopback port binding. Compare all 44 rows and their original digest manifest.

Paper affiliations and contribution roles are managed **separately** and are not demonstrated by this experiment archive.
