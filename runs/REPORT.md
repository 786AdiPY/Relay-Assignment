# The Relay — Results and Evaluation Report

## Table 2a: Latency
| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| real_steady | real | 4 | 19.9 | 49.9 | 12439 / 16581 / 16805 | 12439 / 16581 / 16805 |
| steady | stand-in | 18 | 12.1 | 547.1 | n/a / n/a / n/a | 9816 / 11214 / 11503 |
| burst | stand-in | 24 | 4.5 | 1440.0 | n/a / n/a / n/a | 3589 / 3777 / 4352 |
| sustained_overload | stand-in | 30 | 5.2 | 1800.0 | n/a / n/a / n/a | 436 / 4650 / 5124 |
| per_minute_limit_halved | stand-in | 20 | 5.2 | 1200.0 | n/a / n/a / n/a | 4292 / 5086 / 5123 |
| daily_budget_exhausted | stand-in | 12 | 0.2 | 720.0 | n/a / n/a / n/a | 6 / 9 / 10 |

## Table 2b: Outcomes
| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |
| --- | --- | --- | --- | --- | --- | --- | --- |
| real_steady | 100.0 | 0.0 | 0.0 | 0.0 | 2 | 2458.8 | $0.2715 |
| steady | 0.0 | 22.2 | 77.8 | 0.72 | 14 | 331.9 | $0.0359 |
| burst | 0.0 | 0.0 | 100.0 | 0.04 | 20 | 247.1 | $0.0273 |
| sustained_overload | 0.0 | 46.7 | 53.3 | 0.07 | 18 | 164.3 | $0.0179 |
| per_minute_limit_halved | 0.0 | 35.0 | 65.0 | 0.15 | 12 | 150.2 | $0.0176 |
| daily_budget_exhausted | 0.0 | 100.0 | 0.0 | 0.0 | 3 | 0.0 | $0.0000 |

## Table 3: Retrieval Settings
| Setting | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| small_k3_norank (180 tok, k=3, rerank=no) | 16 | 31.2 | 37.5 | 31.2 | 1575.7 | 1010 / 1882 | 1010 / 1882 |
| med_k5_rerank (320 tok, k=5, rerank=yes) | 16 | 12.5 | 56.2 | 31.2 | 3053.5 | 1051 / 1474 | 1051 / 1474 |
| large_k8_norank (550 tok, k=8, rerank=no) | 16 | 12.5 | 56.2 | 31.2 | 4366.5 | 996 / 1148 | 997 / 1148 |

## Heading 4: Where the Time Goes (Slow Request Trace Breakdown)
- **Request ID:** `real_steady_002`
- **Scenario:** `real_steady`
- **Status:** `ok`
- **Total Request Latency:** `16860.54 ms`
  - **Waiting (Queue Wait):** `6870.62 ms`
  - **Working (Pipeline Steps):** `9989.51 ms`
  - **Retrying (Backoff):** `0 ms`
- **First Verified Token Delay:** `16860.5 ms` (delay occurred **before** the first verified token due to the Response Gate checking citations)
- **Post-First-Token Streaming:** `0.04 ms`
- **Step Breakdown:**
  - `step_a_understand` (loop 0): start=6870.8ms, duration=2126.1ms
  - `step_b_retrieve` (loop 0): start=8996.9ms, duration=14.3ms
  - `step_c_decide` (loop 0): start=9011.3ms, duration=2660.3ms
  - `step_c_draft` (loop 0): start=11671.6ms, duration=3731.3ms
  - `step_d_verify` (loop 0): start=15403.0ms, duration=1457.5ms
  - `response_gate` (loop 0): start=16860.5ms, duration=0.0ms
