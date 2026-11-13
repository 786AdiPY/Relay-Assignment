# The Relay — Results and Evaluation Report

## Table 2a: Latency
| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| real_steady | real | 4 | 18.9 | 40.5 | 9717 / 12746 / 12915 | 9717 / 12746 / 12915 |
| steady | stand-in | 18 | 31.1 | 52.7 | 1288 / 1288 / 1288 | 8997 / 11513 / 11717 |
| burst | stand-in | 24 | 4.5 | 1440.0 | n/a / n/a / n/a | 3550 / 3652 / 4326 |
| sustained_overload | stand-in | 30 | 5.3 | 1800.0 | n/a / n/a / n/a | 442 / 4640 / 5162 |
| per_minute_limit_halved | stand-in | 20 | 5.2 | 1200.0 | n/a / n/a / n/a | 4257 / 5054 / 5105 |
| daily_budget_exhausted | stand-in | 12 | 0.2 | 720.0 | n/a / n/a / n/a | 5 / 9 / 10 |

## Table 2b: Outcomes
| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 answers |
| --- | --- | --- | --- | --- | --- | --- | --- |
| real_steady | 100.0 | 0.0 | 0.0 | 0.25 | 2 | 2466.0 | $0.2730 |
| steady | 5.6 | 66.7 | 27.8 | 1.61 | 5 | 4486.0 | $0.4669 |
| burst | 0.0 | 0.0 | 100.0 | 0.04 | 20 | 247.1 | $0.0273 |
| sustained_overload | 0.0 | 46.7 | 53.3 | 0.07 | 18 | 164.3 | $0.0179 |
| per_minute_limit_halved | 0.0 | 35.0 | 65.0 | 0.15 | 13 | 150.2 | $0.0176 |
| daily_budget_exhausted | 0.0 | 100.0 | 0.0 | 0.0 | 3 | 0.0 | $0.0000 |

## Table 3: Retrieval Settings
| Setting | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| small_k3_norank (180 tok, k=3, rerank=no) | 16 | 31.2 | 37.5 | 31.2 | 1575.7 | 1137 / 1957 | 1137 / 1957 |
| med_k5_rerank (320 tok, k=5, rerank=yes) | 16 | 12.5 | 56.2 | 31.2 | 3053.5 | 1125 / 1527 | 1125 / 1527 |
| large_k8_norank (550 tok, k=8, rerank=no) | 16 | 12.5 | 56.2 | 31.2 | 4366.5 | 1069 / 1293 | 1069 / 1293 |

## Heading 4: Where the Time Goes (Slow Request Trace Breakdown)
- **Request ID:** `real_steady_003`
- **Scenario:** `real_steady`
- **Status:** `ok`
- **Total Request Latency:** `12957.24 ms`
  - **Waiting (Queue Wait):** `4157.66 ms`
  - **Working (Pipeline Steps):** `8799.04 ms`
  - **Retrying (Backoff):** `2090.27 ms`
- **First Verified Token Delay:** `12957.2 ms` (delay occurred **before** the first verified token due to the Response Gate checking citations)
- **Post-First-Token Streaming:** `0.03 ms`
- **Step Breakdown:**
  - `step_a_understand` (loop 0): start=4157.9ms, duration=1769.2ms
  - `step_b_retrieve` (loop 0): start=5927.2ms, duration=11.6ms
  - `step_c_decide` (loop 0): start=5938.8ms, duration=1934.4ms
  - `step_b_retrieve` (loop 1): start=7873.3ms, duration=4.3ms
  - `step_c_decide` (loop 1): start=7877.6ms, duration=1202.6ms
  - `step_c_draft` (loop 0): start=9080.2ms, duration=3876.9ms
  - `safe_refusal` (loop 0): start=12957.2ms, duration=0.0ms
