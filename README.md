# VeriAgent

**A verifiable multi-agent framework for grounded data analysis, visualization and report generation.**
Major project YTS-B-066 · Ravuri Navaneeth Kumar · Vel Tech R&D Institute of Science and Technology

Upload a CSV, ask a question in plain English, and a team of agents analyses the data, draws charts,
writes a report, and **checks every number and every chart before you see them**.

```
Planner → Analysis (sandboxed pandas) → Visualization ⇄ Chart critic → Report → Verifier (⇄ repair)
                    ↘          shared evidence memory (vector DB)          ↗
```

## What is new (the novelty)

| # | Component | What it does |
|---|---|---|
| C1 | **Shared evidence memory** | Every computed value is stored with an ID (`EV-007`), the exact code that produced it and a dataset hash. All agents read from it; the report cites it. |
| C2 | **Multimodal chart critic** | Rule checks plus a vision LLM that looks at the **rendered PNG** and sends charts back with specific fixes (up to 3 rounds). |
| C3 | **Evidence-bound writing + claim-level verifier** | The writer inserts numbers only as placeholders (`{{EV-003}}`, `{{pct_change:EV-001,EV-002}}`) that code fills in. The verifier then recomputes every remaining number in Python, corrects wrong values, flags wrong directions ("grew" vs a fall) and qualitative claims ("doubled"), and marks unsupported numbers. Unsupported sentences go back to the writer once. |

## Quick start

Requires Python 3.10+ and (for the web UI) Node 18+.

```bash
cd veriagent
python -m venv .venv
.venv\Scripts\activate          # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
```

Open `.env` and set your provider and key. Free options:

| Provider | Get a key | `.env` |
|---|---|---|
| Google Gemini (recommended) | https://aistudio.google.com/apikey | `LLM_PROVIDER=gemini` + `GEMINI_API_KEY=...` |
| Groq | https://console.groq.com/keys | `LLM_PROVIDER=groq` + `GROQ_API_KEY=...` |
| Ollama (local, no key) | https://ollama.com | `LLM_PROVIDER=ollama` (pull `llama3.1:8b` and `llama3.2-vision`) |
| Mock (no key, for testing only) | — | `LLM_PROVIDER=mock` |

Model names change over time. If a call fails with "model not found", set `TEXT_MODEL` / `VISION_MODEL`
in `.env` to a current model from your provider's list.

### 1. Build the benchmark datasets
```bash
python eval/build_datasets.py
```

### 2. Try it from the command line
```bash
python run.py --csv eval/datasets/retail_sales.csv --goal "Why did profit fall in 2023 and what should we do?"
```
Open `runs/<folder>/report.html` to see the verified report and verification log.

### 3. Web app
```bash
uvicorn api.main:app --port 8000            # terminal 1
cd frontend && npm install && npm run dev   # terminal 2, then open http://localhost:5173
```
Or build once (`npm run build`) and open http://localhost:8000 directly.

### 4. Run the tests (no API key needed)
```bash
python -m pytest -q tests
```

## Experiments for the paper

Configurations (`veriagent/config.py`):

| Config | Planner | Critic (C2) | Memory (C1) | Evidence-bound + verifier (C3) |
|---|---|---|---|---|
| `B1` single LLM | – | – | – | – |
| `B2` LightVA-style | ✓ | – | – | – |
| `full` VeriAgent | ✓ | ✓ | ✓ | ✓ |
| `no_verifier` | ✓ | ✓ | ✓ | – |
| `no_critic` | ✓ | – | ✓ | ✓ |
| `no_memory` | ✓ | ✓ | – | LLM-only text check |

Benchmark: 50 tasks over 5 datasets (`eval/benchmark.py`), each with ground-truth answers computed in pandas.

```bash
# quick check first (2 tasks, 1 run)
python eval/run_experiments.py --runs 1 --tasks R02 C02

# everything: 6 configs x 3 runs x 50 tasks (resumable, safe to stop and restart)
python eval/run_experiments.py

# error-injection test of the verifier (Section V-F)
python eval/injection_test.py --n 20

# optional but recommended: human check of a 20% sample of claim labels
python eval/claims_labels.py export --sample 0.2
#   fill manual_status in results/claims_to_label.csv, then:
python eval/claims_labels.py apply

# all numbers for the paper, printed as LaTeX rows
python eval/compute_paper_numbers.py results
```

Free tiers have rate limits. If you see 429 errors, set `REQUEST_DELAY=4` in `.env`; the runner retries
automatically and caches every LLM response in `.cache/llm`, so re-running costs nothing.

### Metrics
- **Task success (TSR)**: the final report contains every ground-truth answer (1% tolerance or displayed rounding).
- **Factual accuracy (FA) / hallucination rate (HR)**: an independent auditor agent recomputes each numeric claim
  from the raw data; claims are labelled correct, wrong or unsupported. Spot-check with `claims_labels.py`.
- **Chart pass rate (CPR)**, VisEval-style: the chart renders (validity), uses an allowed chart type and plots the
  reference values (legality), and gets readability ≥ 3 from a vision judge.
- **Injection test**: detected / corrected out of N injected errors, plus false alarms on clean reports.

## Project layout
```
veriagent/            core package
  agents/             planner, analyst, visualizer, critic (C2), reporter (C3a), verifier (C3b), single (B1)
  evidence.py         C1 evidence memory (ChromaDB or TF-IDF fallback)
  sandbox.py          restricted execution of LLM-written pandas code
  charts.py           deterministic matplotlib renderer
  pipeline.py         LangGraph workflow and run artifacts
  llm.py              OpenAI-compatible client (Gemini/Groq/OpenAI/Ollama), cache, retries
  mock_llm.py         offline mock for tests (never use its numbers)
api/main.py           FastAPI backend (REST + WebSocket)
frontend/             React UI (agent timeline, report with clickable evidence, verification log)
eval/                 datasets, benchmark, runner, scoring, injection test, paper numbers
tests/                31 unit and integration tests
```

## Notes and limitations
- The verifier only confirms numbers that rest on values in the evidence memory; causal statements and outside facts
  are flagged as unsupported, not confirmed.
- Claims in words ("nearly doubled") are checked only for doubled/tripled/halved/quadrupled/majority/most.
- Charts are rendered from a spec with a fixed template, so all configurations share the same drawing quality.
- The mock LLM exists only to test the plumbing. Its outputs are not results.
