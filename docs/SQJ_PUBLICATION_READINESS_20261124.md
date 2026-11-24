# SQJ Submission Readiness: RAGuard evidence and novelty audit (2026-11-24)

**Status: NOT READY for submission.** This is a research-quality review, not a new experiment and not a claim of algorithmic superiority. Based on the stage-specific original CSV, protocol JSON and SHA-256 archives, plus the previous 44-case manuscript.

## Reproducible result summary

| Study | Independent unit | Observed result | Critical limitation |
|---|---|---|---|
| Earlier audited HTTP study | 44 engine–fault–variant episodes | 2 safe confirmations, 42 unresolved; B5 and Gateway tie | A shared no-reissue research adapter blocks recovery writes; no real LLM decisions |
| Stage 3 | 12 distinct Qwen2.5:7b suggestions, reused for 24 engine episodes | 12/12 retry_same; 0 voluntary audit requests | No second-turn evidence branch was exercised |
| Stage 4 | Phase A 9 optional-audit decisions; Phase B 18 supplied-evidence decisions reused for 36 engine episodes | 0/9 voluntary audit requests; 0/18 matches to the preregistered conservative strategy | Semantic evidence summaries, not raw provider attestations; policy agreement is NOT measured physical harm |
| Stage 5 | 96 method episodes, 48 matched engine comparisons | Unaware: 24 duplicate effects and 24 unsafe confirmations; common contract admission: 0 of each, 36 unresolved | Same admission function is given to both real engines, not native to RAGuard |
| Stage 6 | 162 method episodes, 81 matched engine comparisons | Unaware: 53 duplicate effects; client-age: 51; verified: 0 with 54 unresolved | Verified admission has privileged experiment-supervisor knowledge of a pending old request |

**Stage 6 paired countercheck:** all 81 B5/Gateway pairs agree on the unsafe-confirmation and safe-completion labels. Seven pairs differ in extra physical effect counts and 22 in HTTP write counts, plausibly due to concurrency scheduling and state contention. These differences do not establish a causal RAGuard advantage. Do not add duplicate effects to unsafe confirmations as independent incident counts. Fixed scenario repeats are not samples from the production incident distribution.

**Stage 5 engine-level countercheck:** each engine contributes 12 duplicate effects under unaware admission and zero under the common contract gate. Each contributes 18 unresolved outcomes when the common gate is enabled. The strongest comparison **ties** on those safety metrics.

## Data integrity (external ZIP SHA-256)

- Local Qwen pilot: d0446c603bccea3969350430dd1716088342827cb042152673947059a8d5da83
- Stage 3 real Qwen: 15028eff6efd2f88dd56ef5f6a5899846d6905f2ab99d823e209425ce4aceba8
- Stage 4 real Qwen: ab608262177b34b63ae47918a2e87c095f9de008c8f1ece69f506675cd940f10
- Stage 5 3-repeat Go: d43549f9d0fe2e898abe012c1d9d6e96dcfe8510ebee1dad06d309add64af1aa
- Stage 6 3-repeat Go: 0a67dc480ebcae2d8a59cb1a9ae7a615d8d16a9774da556f9e6fbff69617cad7

Five user/conversation-owned result ZIPs were independently opened; ZIP CRC and every named internal SHA-256 digest matched. This is integrity verification, not third-party validation of the causal interpretation. Stage 5/6 original ZIPs should be deposited in durable, version-pinned supplementary storage before submission; CI artifacts alone expire.

## Closest competing work — VERIFIED on original abstract pages

1. Li (2026) LIMBO: https://arxiv.org/abs/2609.29095 — much larger agent/model/harness/tool-contract sandbox with independent effect ground truth and late commits.
2. Liu, Liu, Lu (2026) RetryBench: https://arxiv.org/abs/2610.15884 — already separates retry permission from settled outcome and tests prompt sensitivity.
3. Li (2026) Checking Retry Transparency in Agent Tools: https://arxiv.org/abs/2611.23307 — interleavings, tool-effect contracts and actual MCP components.
4. Xu (2026) Idempotency Is Not Recovery: https://arxiv.org/abs/2611.23353 — ledger, reconciliation, idempotency and safety/completion cost through fault injection.
5. Mansoor et al. (2026) Verified Tool Calls: https://arxiv.org/abs/2608.02645 — verify-before-retry and postcondition checking.

These are not replicated head-to-head in the RAGuard study. Treat arXiv items as preprints, not journal-accepted papers. This is a targeted comparison, **not a systematic literature review**.

## Claim gate for a Software Quality Journal paper

**Supported (with qualifiers):** a reproducible Python–Go, case-level testing artifact distinguishing advice, durable-engine outcome, admitted evidence, and independently recorded physical effects; matched B5/Gateway baseline; demonstrable contract-dependent safety versus unresolved tradeoff in fixed local simulations.

**Not supported:** new exactly-once algorithm; production API or provider reliability rates; statistical RAGuard superiority; general model failure frequency; Stage 5/6 as real LLM experiments; deployable status oracle from a research supervisor.

**Critical manuscript gap:** the prior SQJ v4 LaTeX article is a 44-case study; the newer Stage 3–6 protocols have incompatible units and conditions and are not integrated into its RQs/results/limitations. Do not simply append totals. The alternate document version uses software-description/illustrative-example sections and must not be conflated with the SQJ research-paper structure.

**Journal check:** official SQJ guidelines https://link.springer.com/journal/11219/submission-guidelines expressly specify Springer Nature's sn-jnl LaTeX class and a 150–250 word abstract, as well as author–year citations, source files, no parallel consideration, and coauthor approval. The publisher elsewhere says Word files are accepted; follow the journal-specific LaTeX requirement for the submission route. Scope https://link.springer.com/journal/11219/aims-and-scope includes software testing and measurement, but scope fit is not sufficient evidence of methodological novelty.

## Editorial action / release gate

1. Reframe as a **research-software fault-injection audit** rather than a new safe-retry algorithm.
2. Show a *nearest-neighbor measurement coverage matrix* proving a concrete new executable capability versus LIMBO, RetryBench and Checking Retry Transparency, or refrain from a novelty claim.
3. Preserve the negative B5 tie and unresolved tradeoff. Declare the shared gate and privileged pending information in each table caption and abstract.
4. Trace each result row to its protocol, source commit, archive checksum, and measurement unit. Pin the actual Ollama model digest/prompt version where possible; disclose unavailable raw model output logs rather than inventing them.
5. Reconcile the author-approved manuscript and its publication/submission status; prepare journal-specific source after the scientific review.
6. If these cannot be satisfied, publish the negative reproducibility benchmark and software as a non-SQJ artifact rather than manufacturing superiority.

**Final research decision:** NO-GO for the current manuscript; CONDITIONAL GO to continue drafting a sharply scoped testbed-based original paper. No new simulation or inference was run for this audit.
