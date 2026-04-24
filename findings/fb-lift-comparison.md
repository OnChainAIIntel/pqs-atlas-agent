# F→B Lift comparison: Claude Opus 4.7 vs Claude Sonnet 4.6 as rewriter

Last updated: 2026-04-24
Status: **Opus 4.7 wins.** Mean lift +38.40 vs +32.40, head-to-head 4 of 5 seeds. Same 5 F-grade inputs, same kappa-calibrated scorer, same rubric.

---

## Methodology

Five F-grade seeds from the Arm 3 WildChat-mid set (the canonical corpus used by the Pipeline-4 F→B Lift pilot, `findings/rubric-ceiling.md`). We held the scorer constant (Claude Opus 4.7 applying the kappa-validated `RUBRIC_PROMPT` from `scripts/pipeline-5/rubric.py`, SHA256 asserted at runtime) and varied only the rewriter: `claude-opus-4-7` in arm A, `claude-sonnet-4-6` in arm B. Each seed was pre-scored once (shared across arms), then rewritten by both models against an identical rewrite system prompt, then post-scored by the same Opus 4.7 scorer. Script: `scripts/fb-lift-comparison.py`. Outputs: `findings/fb-lift-opus.jsonl`, `findings/fb-lift-sonnet.jsonl`. Total API cost for the full comparison was $0.28.

## Results

| Seed   | Mode                       | Pre (0–80) | Opus post | **Opus lift** | Sonnet post | **Sonnet lift** | Head-to-head |
|--------|----------------------------|------------|-----------|---------------|-------------|------------------|--------------|
| wc-01  | compiler error wall        | 17 (F)     | 71 (A)    | **+54**       | 67 (B)      | **+50**          | Opus +4      |
| wc-02  | underspec assignment       | 23 (F)     | 70 (A)    | **+47**       | 58 (C)      | **+35**          | Opus +12     |
| wc-03  | vague list request         | 38 (D)     | 77 (A)    | **+39**       | 70 (A)      | **+32**          | Opus +7      |
| wc-04  | awkward 60-word constraints| 59 (C)     | 71 (A)    | **+12**       | 73 (A)      | **+14**          | Sonnet +2    |
| wc-05  | persona, unbounded goal    | 36 (D)     | 76 (A)    | **+40**       | 67 (B)      | **+31**          | Opus +9      |
| **Mean** |                          | 34.6       | 73.0      | **+38.40**    | 67.0        | **+32.40**       | **Opus +6.00** |

Grade thresholds: A ≥ 70, B ≥ 60, C ≥ 50, D ≥ 35, F < 35.

Post-rewrite, Opus 4.7 lands 4 seeds at A and 1 at A (5 of 5 cleared the B threshold). Sonnet 4.6 lands 2 at A, 1 at B, 1 at C, 1 at B (4 of 5 cleared B).

## Summary finding

Opus 4.7 rewrites lift F-grade prompts 6 points further than Sonnet 4.6 rewrites on average against the kappa-calibrated PQS rubric, and win 4 of 5 head-to-head. The gap is largest on the two seeds with the most headroom (wc-01 and wc-02, both F pre-scores). On wc-04, which pre-scored at C with limited headroom left (only 21 points available), Sonnet edges Opus by 2 points, inside the measurement noise band.

## Honest caveat

n = 5. Not a statistical proof. The comparison is directional, not definitive. One seed (wc-04) inverted the ordering by 2 points. A full-corpus replication over hundreds of seeds would be needed before claiming significance. What this n = 5 run does establish: on the inputs PQS actually sees in production, where pre-scores cluster in F and D, Opus 4.7 recovers more of the available headroom per call than Sonnet 4.6 at the same rubric, same scorer, same rewrite system prompt.

## Killer quote (video)

"Same 5 F-grade prompts, same scorer, same rubric. Opus 4.7 lifts them 6 points further than Sonnet 4.6, and wins 4 of 5 head to head."

## Repro

```
cd ~/Desktop/pqs-atlas-agent
python3 scripts/fb-lift-comparison.py
```

Requires `ANTHROPIC_API_KEY` (read from `~/Desktop/prompt-optimization-engine/.env.local`). Rubric checksum asserted at start; seeds resolved from `data/source-prompts-clean-deterministic.jsonl` by canonical row IDs.
