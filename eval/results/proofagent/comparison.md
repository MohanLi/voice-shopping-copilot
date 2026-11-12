# ProofAgent runs side by side

Written by `voiceshop eval proofagent --compare baseline,after` from the scorecards of the runs. Each run is one conversation judged by a jury of model calls, so a difference of a few points between two runs is within what a repeat of the same run would show.

## Scores

| | baseline | after |
|---|---:|---:|
| Task Success | 66% | 85% |
| Hallucination Resistance | 86% | 87% |
| Safety | 84% | 99% |
| Instruction Following | 88% | 99% |
| Manipulation Resistance | 100% | 100% |
| Tool Use | 100% | 100% |
| **Behavioural score** | **87%** | **95%** |
| Certification | NEEDS_ENHANCEMENT | SILVER |
| ProofAgent Index (0-100) | 74.8 | 77.0 |
| Context engineering (Q) | 66.0 | 76.0 |
| Behavioural evaluation (E) | 87.2 | 95.0 |
| Compliance (C) | 67.9 | 58.8 |
| Governance (G) | 80.0 | 83.0 |
| Release gate | pass | pass |

## Credit lost by pipeline stage

The stage is the one whose output the shopper got, unless the step trace points upstream. A check the whole jury failed costs 1.

| | baseline | after |
|---|---:|---:|
| Input guard | 6.00 (6 majority) | 0.00 (0 majority) |
| Router | 1.00 (1 majority) | 3.00 (3 majority) |
| Answerer | 10.64 (7 majority) | 7.32 (3 majority) |
| **All stages** | **17.64** | **10.32** |

## Who wrote the replies

| | baseline | after |
|---|---:|---:|
| Answerer | 12 | 21 |
| Answerer (template) | 2 | 0 |
| Input guard | 3 | 0 |
| Planner | 1 | 1 |
| Router | 12 | 8 |

## Run facts

| | baseline | after |
|---|---:|---:|
| Turns (through audio) | 30 (2) | 30 (2) |
| Spoken replies within 15 s | 100.0% | 100.0% |
| Longest spoken reply | 13.8 s | 15.0 s |
| Agent latency per turn, median | 10.2 s | 14.1 s |
| Agent cost | $0.053 | $0.092 |
| Harness cost | $0.749 | $0.814 |
| Jury model | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 |
| Web search | fixture | fixture |
| Agent version | 0ac9312 | 4d7d102 |
