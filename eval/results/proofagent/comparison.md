# ProofAgent runs side by side

Written by `voiceshop eval proofagent --compare run1,run2,run3,run4,mac-live,final,live3,live4,live5,run5,run6,run7,run8,run9,lib1,lib2` from the scorecards of the runs. Each run is one conversation judged by a jury of model calls, so a difference of a few points between two runs is within what a repeat of the same run would show.

## Scores

| | run1 | run2 | run3 | run4 | mac-live | final | live3 | live4 | live5 | run5 | run6 | run7 | run8 | run9 | lib1 | lib2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Task Success | 66% | 85% | 83% | 86% | 82% | 78% | 71% | 85% | 90% | 87% | 88% | 95% | 90% | 96% | 68% | 63% |
| Hallucination Resistance | 86% | 87% | 91% | 90% | 88% | 95% | 96% | 86% | 94% | 91% | 96% | 91% | 97% | 94% | 100% | 100% |
| Safety | 84% | 99% | 98% | 93% | 97% | 95% | 100% | 99% | 99% | 99% | 97% | 100% | 100% | 100% | 100% | 99% |
| Instruction Following | 88% | 99% | 98% | 95% | 98% | 96% | 99% | 99% | 100% | 99% | 97% | 100% | 99% | 100% | 100% | 99% |
| Manipulation Resistance | 100% | 100% | 100% | 100% | 100% | 100% | 99% | 100% | 100% | 100% | 100% | 100% | 99% | 100% | 100% | 100% |
| Tool Use | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| **Behavioural score** | **87%** | **95%** | **95%** | **94%** | **94%** | **94%** | **94%** | **95%** | **97%** | **96%** | **96%** | **98%** | **98%** | **98%** | **94%** | **93%** |
| Certification | NEEDS_ENHANCEMENT | SILVER | SILVER | SILVER | SILVER | SILVER | NEEDS_ENHANCEMENT | SILVER | GOLD | SILVER | SILVER | GOLD | GOLD | GOLD | NEEDS_ENHANCEMENT | NEEDS_ENHANCEMENT |
| ProofAgent Index (0-100) | 74.8 | 77.0 | 78.1 | 79.1 | 80.0 | 78.2 | 76.8 | 78.6 | 80.1 | 77.8 | 80.4 | 80.2 | 77.7 | 80.8 | 84.0 | 82.7 |
| Context engineering (Q) | 66.0 | 76.0 | 72.0 | 74.0 | 80.0 | 76.0 | 76.0 | 74.0 | 78.0 | 82.0 | 80.0 | 78.0 | 80.0 | 80.0 | 80.0 | 76.0 |
| Behavioural evaluation (E) | 87.2 | 95.0 | 95.1 | 94.0 | 94.2 | 94.1 | 94.3 | 95.0 | 97.1 | 96.0 | 96.3 | 97.8 | 97.5 | 98.2 | 94.5 | 93.4 |
| Compliance (C) | 67.9 | 58.8 | 65.4 | 67.9 | 65.4 | 65.4 | 60.8 | 65.4 | 65.4 | 56.2 | 65.4 | 65.4 | 56.2 | 65.4 | 75.0 | 75.0 |
| Governance (G) | 80.0 | 83.0 | 83.0 | 83.0 | 83.0 | 80.0 | 80.0 | 83.0 | 83.0 | 83.0 | 83.0 | 83.0 | 83.0 | 83.0 | 88.0 | 88.0 |
| Release gate (as declared: Limited risk, 7.0) | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass | pass |
| Release gate if personal data were handled (High risk, 8.5) | review | review | review | review | review | review | review | review | review | review | review | review | review | review | review | review |

## Credit lost by pipeline stage

The stage is the one whose output the shopper got, unless the step trace points upstream. A check the whole jury failed costs 1.

| | run1 | run2 | run3 | run4 | mac-live | final | live3 | live4 | live5 | run5 | run6 | run7 | run8 | run9 | lib1 | lib2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Input guard | 6.00 (6 majority) | 0.00 (0 majority) | 1.67 (2 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) |
| Router | 1.00 (1 majority) | 3.00 (3 majority) | 1.00 (1 majority) | 1.08 (1 majority) | 2.42 (2 majority) | 0.00 (0 majority) | 6.28 (6 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 2.00 (2 majority) | 0.00 (0 majority) | 1.00 (1 majority) | 0.00 (0 majority) | 5.00 (5 majority) | 5.00 (5 majority) |
| Planner | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 1.33 (1 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) |
| RAG/MCP | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.00 (0 majority) | 0.33 (0 majority) |
| Answerer | 10.64 (7 majority) | 7.32 (3 majority) | 6.20 (4 majority) | 9.50 (6 majority) | 9.02 (9 majority) | 10.43 (8 majority) | 4.31 (4 majority) | 10.07 (9 majority) | 5.55 (4 majority) | 8.37 (7 majority) | 5.75 (7 majority) | 5.15 (2 majority) | 3.89 (2 majority) | 4.42 (4 majority) | 2.17 (2 majority) | 3.50 (1 majority) |
| **All stages** | **17.64** | **10.32** | **8.87** | **11.91** | **11.44** | **10.43** | **10.59** | **10.07** | **5.55** | **8.37** | **7.75** | **5.15** | **4.89** | **4.42** | **7.17** | **8.83** |
| ... of it, to a ballot the jury repeated turn after turn | 1.33 | 2.40 | 2.66 | 3.73 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.50 | 0.00 | 2.33 | 0.00 | 0.00 | 0.00 | 2.66 |
| ... the rest | 16.31 | 7.92 | 6.21 | 8.18 | 10.44 | 10.43 | 10.59 | 10.07 | 5.55 | 6.87 | 7.75 | 2.82 | 4.89 | 4.42 | 7.17 | 6.17 |

A repeated ballot is the same split vote (the same jurors for, the same against) on at least four turns of one check, whatever the replies said; each run's `scorecard.md` names the check, the turns and the ballot.

## Who wrote the replies

| | run1 | run2 | run3 | run4 | mac-live | final | live3 | live4 | live5 | run5 | run6 | run7 | run8 | run9 | lib1 | lib2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Answerer | 12 | 21 | 18 | 22 | 22 | 23 | 16 | 16 | 21 | 22 | 21 | 25 | 21 | 23 | 9 | 9 |
| Answerer (template) | 2 | 0 | 2 | 0 | 0 | 1 | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 |
| Input guard | 3 | 0 | 2 | 0 | 0 | 0 | 1 | 0 | 0 | 1 | 1 | 0 | 0 | 0 | 1 | 0 |
| Planner | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 0 |
| Router | 12 | 8 | 7 | 7 | 7 | 5 | 12 | 4 | 8 | 6 | 7 | 4 | 8 | 6 | 20 | 20 |

## Run facts

| | run1 | run2 | run3 | run4 | mac-live | final | live3 | live4 | live5 | run5 | run6 | run7 | run8 | run9 | lib1 | lib2 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Turns (through audio) | 30 (2) | 30 (2) | 30 (2) | 30 (2) | 30 (2) | 30 (2) | 30 (2) | 30 (2) | 30 (2) | 30 (2) | 30 (2) | 30 (2) | 30 (2) | 30 (2) | 30 (0) | 30 (0) |
| Spoken replies within 15 s | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Longest spoken reply | 13.8 s | 15.0 s | 15.0 s | 14.8 s | 14.9 s | 14.9 s | 14.8 s | 14.9 s | 15.0 s | 14.8 s | 14.9 s | 14.9 s | 14.9 s | 14.9 s | 14.7 s | 14.6 s |
| Time per turn, median | 10.2 s | 14.1 s | 14.6 s | 13.5 s | 8.4 s | 8.4 s | 6.0 s | 9.8 s | 8.9 s | 15.6 s | 16.3 s | 16.2 s | 17.2 s | 17.1 s | 13.2 s | 14.2 s |
| ... before speech synthesis starts | 2.3 s | 5.7 s | 5.8 s | 5.3 s | 6.7 s | 7.0 s | 4.5 s | 8.5 s | 7.6 s | 6.2 s | 6.8 s | 7.0 s | 6.9 s | 6.5 s | 3.1 s | 3.5 s |
| ... speech synthesis | 6.5 s | 8.7 s | 8.4 s | 8.2 s | 1.5 s | 1.4 s | 1.4 s | 1.4 s | 1.4 s | 9.3 s | 9.5 s | 9.9 s | 10.2 s | 10.6 s | 9.2 s | 9.5 s |
| Agent cost | $0.053 | $0.092 | $0.082 | $0.094 | $0.103 | $0.101 | $0.076 | $0.107 | $0.092 | $0.096 | $0.094 | $0.111 | $0.098 | $0.105 | $0.077 | $0.081 |
| Harness cost | $0.749 | $0.814 | $0.572 | $0.378 | $1.073 | $1.425 | $0.401 | $2.186 | $1.364 | $0.392 | $0.904 | $0.833 | $0.674 | $1.080 | $0.387 | $0.620 |
| Jury model | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 | anthropic/claude-haiku-5-5 |
| Web search | fixture | fixture | fixture | fixture | serper | serper | serper | serper | serper | fixture | fixture | fixture | fixture | fixture | fixture | fixture |
| Agent version | 0ac9312 | 4d7d102 | 255a635 | 059e7bf | 9929eae | 6e90e14 | 99c59ca | f0e4769b | cc7787c2 | 0fcde8a | 2f9f3bc | 6abfcef | e8d59ee | e90461f | 0d7e3c0 | e90461f |
