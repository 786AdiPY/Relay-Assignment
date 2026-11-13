# The Relay — Results and Evaluation Report

## Table 2a: Latency
| Scenario | Real or stand-in | Requests sent | Duration (s) | Offered rate (per min) | First token p50 / p95 / p99 (ms) | Total time p50 / p95 / p99 (ms) |
| --- | --- | --- | --- | --- | --- | --- |
| real_steady | real | 4 | 21.6 | 48.9 | 6874 / 15239 / 16365 | 6874 / 15239 / 16365 |
| steady | stand-in | 18 | 31.0 | 52.4 | 1275 / 1275 / 1275 | 8915 / 11431 / 11596 |
| burst | stand-in | 24 | 4.6 | 1440.0 | n/a / n/a / n/a | 3568 / 3792 / 4392 |
| sustained_overload | stand-in | 30 | 5.4 | 1800.0 | n/a / n/a / n/a | 452 / 4718 / 5252 |
| per_minute_limit_halved | stand-in | 20 | 5.7 | 1200.0 | n/a / n/a / n/a | 4300 / 5232 / 5510 |
| daily_budget_exhausted | stand-in | 12 | 0.2 | 720.0 | n/a / n/a / n/a | 8 / 19 / 21 |

## Table 2b: Outcomes
| Scenario | Succeeded % | Rate-limited % | Timed out or given up % | Retries per request | Peak queue length | Tokens per answer | Cost per 1,000 attempts |
| --- | --- | --- | --- | --- | --- | --- | --- |
| real_steady | 100.0 | 0.0 | 0.0 | 0.25 | 1 | 2466.0 | $0.2730 |
| steady | 5.6 | 66.7 | 27.8 | 1.61 | 5 | 4486.0 | $0.0358 |
| burst | 0.0 | 0.0 | 100.0 | 0.04 | 20 | 247.1 | $0.0273 |
| sustained_overload | 0.0 | 46.7 | 53.3 | 0.07 | 17 | 164.3 | $0.0179 |
| per_minute_limit_halved | 0.0 | 35.0 | 65.0 | 0.15 | 13 | 155.3 | $0.0183 |
| daily_budget_exhausted | 0.0 | 100.0 | 0.0 | 0.0 | 3 | 0.0 | $0.0000 |

## Table 3: Retrieval Settings
| Setting | Questions asked | Correct and cited % | Wrong % | Correctly refused % | Tokens per answer | First token p50 / p95 (ms) | Total time p50 / p95 (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| small_k3_norank (180 tok, k=3, rerank=no) | 16 | 31.2 | 37.5 | 31.2 | 1575.7 | 1056 / 1913 | 1056 / 1913 |
| med_k5_rerank (320 tok, k=5, rerank=yes) | 16 | 12.5 | 56.2 | 31.2 | 3053.5 | 1073 / 1537 | 1073 / 1537 |
| large_k8_norank (550 tok, k=8, rerank=no) | 16 | 12.5 | 56.2 | 31.2 | 4366.5 | 1005 / 1134 | 1005 / 1134 |

## Heading 4: Where the Time Goes (Slow Request Trace Breakdown)
- **Request ID:** `real_steady_003`
- **Scenario:** `real_steady`
- **Status:** `ok`
- **Total Request Latency:** `16646.35 ms`
  - **Waiting (Queue Wait):** `3072.21 ms`
  - **Working (Pipeline Steps):** `13573.7 ms`
  - **Retrying (Backoff):** `3646.13 ms`
- **First Verified Token Delay:** `16646.31 ms` (delay occurred **before** the first verified token due to the Response Gate checking citations)
- **Post-First-Token Streaming:** `0.04 ms`
- **Step Breakdown:**
  - `step_a_understand` (loop 0): start=3072.3ms, duration=10248.3ms
  - `step_b_retrieve` (loop 0): start=13320.7ms, duration=8.4ms
  - `step_c_decide` (loop 0): start=13329.2ms, duration=1213.6ms
  - `step_b_retrieve` (loop 1): start=14542.8ms, duration=4.8ms
  - `step_c_decide` (loop 1): start=14547.7ms, duration=1120.1ms
  - `step_c_draft` (loop 0): start=15667.9ms, duration=978.4ms
  - `safe_refusal` (loop 0): start=16646.3ms, duration=0.0ms
