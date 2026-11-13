# The Relay — Quota-Aware Research Assistant Pipeline

An empirical, quota-aware, deadline-enforcing research assistant and evaluation harness for technical arXiv papers (*Attention Is All You Need*, *Moshi*, and *Dynamic Chunking*), calibrated against Groq's free-tier limits with an authentic streaming Stand-in service.

---

## Deliverables & Documentation

- **Full Submission Report (2 Pages):** [`THE_RELAY_SUBMISSION_REPORT.md`](THE_RELAY_SUBMISSION_REPORT.md)
  - Table 1 (Groq empirical boundaries & error behavior)
  - Tables 2a & 2b (Latency percentiles and outcomes across 6 load scenarios)
  - Table 3 (Retrieval parameter sweeps & token economics)
  - Trace waterfall breakdown (Queue wait vs. active pipeline execution vs. retries)
  - Quota policies, synchronized retry storm analysis, and 3 retriever trap cases
  - Product feedback on [Vaani](https://www.vaani.dev/)
  - AI Assistant collaboration log & distinct architectural decisions
- **Raw Benchmark Datasets:**
  - [`runs/results.csv`](runs/results.csv) — Exact 12-column telemetry dataset across all 156 benchmark runs
  - [`runs/traces.jsonl`](runs/traces.jsonl) — Granular monotonic span events for every request lifecycle
  - [`runs/table1_limits.json`](runs/table1_limits.json) — Calibrated Groq limit headers and probe data
  - [`runs/transcript.jsonl`](runs/transcript.jsonl) — Complete unedited assistant conversation history (API key redacted for security)
- **Assignment Brief:** [`The_Relay_Assignment_Brief.pdf`](The_Relay_Assignment_Brief.pdf)

---

## Architecture Overview

```
1. Client / Load Generator (Scenario runner with client abandonment timers)
   │
   ▼
2. Orchestration Layer (Admission queue, concurrency limit, quota tracking, single deadline)
   │
   ▼
3. AI Assistant Pipeline
   ├── Step A: Query Understanding & paper targeting
   ├── Step B: Hybrid FAISS dense + BM25 lexical retrieval
   ├── Step C: Sufficiency decision (Loop to B / Early Refuse / Draft)
   ├── Step D: Citation validation
   └── Final Response Gate: Certified grounding verification (max 1 correction)
   │
   ▼
4. Model Provider Interface (Circuit Breaker: CLOSED / OPEN / HALF_OPEN)
   ├── Groq Provider (Real API)
   └── Stand-in Provider (Calibrated local HTTP/SSE service with failure injection)
   │
   ▼
5. Out-of-Band Observability (Thread-safe event bus, non-blocking telemetry)
```

---

## Quickstart

### 1. Setup Virtual Environment
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\Activate.ps1
pip install -e .
```

### 2. Configure Environment (Optional for Real Groq)
```bash
cp .env.example .env
# Add your GROQ_API_KEY inside .env if calling live Groq
```

### 3. Run Automated Test Suite
```bash
pytest -v
```

### 4. Interactive CLI Queries
Query via local calibrated stand-in provider:
```bash
python run_pipeline.py -q "In Moshi, what is the frame rate of Mimi?" --source stand-in
```

Query via live Groq endpoint:
```bash
python run_pipeline.py -q "In Attention Is All You Need, what is d_model?" --source real
```
