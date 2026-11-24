# Evidence-conditioned Agent study (SQJ stage 3)

The original Qwen2.5 7B pilot is archived under `research/archives/agent_model_qwen25_7b_20261124/`. It contains 12 method-level rows from 6 real LLM decisions. The model chose `retry_same` in all six. Both B5 and RAGuard achieved 6/6 acknowledgements without observed duplicate effects **under the Go sandbox's native idempotency contract**. The methods tied.

This next study changes the question rather than merely repeating the same prompt.

## Experimental conditions

- Two ambiguous fault positions: before commit and after commit.
- Two evidence-access conditions: `visible` and `unavailable`. In the visible case the agent can request a real Go HTTP positive receipt. The other case withholds the receipt. Missing evidence does **not** prove final non-execution.
- Two independent persistent engines, B5 and RAGuard, are given **the same agent suggestion**.
- The agent first chooses `audit`, `stop`, `retry_same` or `retry_replanned`. If it requests an audit, it receives an actual readback observation and then chooses `confirm`, `stop`, `retry_same` or `retry_replanned`.
- Confirmation still requires each engine to validate its own positive evidence. An LLM suggestion alone cannot directly confirm an operation.
- A fresh Go sandbox and SQLite store are created per engine episode. Physical effect history is available to the evaluator only.

## Run local script mode (CI-level integration, NOT an LLM)

```powershell
python -m unittest bench.test_agent_evidence_study -v
python -m bench.agent_evidence_study --mode scripted --repeats 3 --seed 20261124 --output results/agent_evidence_scripted
```

## Run Qwen2.5 7B through local Ollama

```powershell
ollama list
python -m bench.agent_evidence_study --mode model --endpoint http://127.0.0.1:11434/v1/chat/completions --model qwen2.5:7b --repeats 3 --seed 20261124 --output results/agent_evidence_qwen25_7b
```

The latter makes up to two real model requests per evidence condition. With three repeats and four conditions, there are 24 method-level Go executions and up to 24 model calls. The actual number of model calls depends on whether `audit` was selected. A `model_calls` field is stored per method row; paired rows reuse the same model decision and should not be double-counted.

The runner prints progress during execution. If a model response does not match strict JSON, an error is raised and should be reported, not silently excluded.

## Outputs and limitations

`episodes.csv`, `protocol.json`, `REPORT.md`, and `SHA256SUMS.json` are created only after all required conditions complete. Submit the entire results folder as a ZIP; do not send API tokens or real customer records. Hashes of model replies are kept, not raw conversation text.

This study is **not** an independent real provider test or a non-idempotent third-party service trial. It tests model decisions with a controlled Go-sandbox readback. The hidden fault position is not sent to the model. The engine's recovery pathway still uses the repository's shared research adapter, so the results do not establish an RAGuard advantage over B5. Treat observed results as pilot integration evidence, not a production guarantee.
