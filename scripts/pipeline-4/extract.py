"""
Pipeline 4 atlas source-corpus extractor.

Produces 4 output files (500 rows each, 250 messy / 150 mid / 100 polished):
  - data/source-prompts-full-deterministic.jsonl
  - data/source-prompts-full-sampled.jsonl
  - data/source-prompts-clean-deterministic.jsonl
  - data/source-prompts-clean-sampled.jsonl

Plus evidence files:
  - data/pipeline-4-pilot.jsonl              (Gate B: 50-row pilot)
  - data/pipeline-4-polished-rejections.jsonl (insurance audit trail)

Mix tables (LMSYS fallback applied — no HF auth):

  FULL CORPUS (includes no_robots, NC-tagged):
    WildChat     160 messy   20 mid    0 polished
    oasst2        90 messy   50 mid   10 polished
    no_robots      0 messy   50 mid   20 polished   (CC-BY-NC-4.0)
    natural-ins    0 messy   30 mid   70 polished
                  250        150      100

  CLEAN CORPUS (drops no_robots, tops up via oasst2 + natural-ins):
    WildChat     160 messy   20 mid    0 polished
    oasst2        90 messy  100 mid   10 polished
    natural-ins    0 messy   30 mid   90 polished
                  250        150      100

Polished insurance: pre-score 150 ni + 30 oasst2 candidates via
/api/score/full (bearer PQS_API_KEY + X-PQS-Internal PQS_INTERNAL_TOKEN),
keep those with pre_grade in {A, B, C}. Rejections logged to
pipeline-4-polished-rejections.jsonl. If insufficient passes, top up
candidates +50 and re-score.

Usage:

  # Gate B pilot (50 rows, proportional):
  python3 extract.py --pilot

  # Gate C full (all 4 files, 500 rows each):
  python3 extract.py

Env required:
  PQS_API_KEY        — bearer for /api/score/full
  PQS_INTERNAL_TOKEN — attribution header (keeps atlas traffic off customer
                       analytics)
  The script sources these from prompt-optimization-engine/.env.local if not
  already set.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterator, Optional
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

# Module path resolution — works whether script is invoked directly or as package.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from buckets import classify  # noqa: E402
from sources import (  # noqa: E402
    load_no_robots,
    load_oasst2,
    load_openorca,
    load_wildchat,
    load_lmsys,
)


REPO_ROOT = _HERE.parent.parent  # scripts/pipeline-4/ -> repo root
DATA_DIR = REPO_ROOT / "data"
PILOT_PATH = DATA_DIR / "pipeline-4-pilot.jsonl"
REJECTIONS_PATH = DATA_DIR / "pipeline-4-polished-rejections.jsonl"

# Canonical PQS grade thresholds (mirror of lib/pqs-grading.js).
GRADE_A = 70
GRADE_B = 60
GRADE_C = 50

# Polished insurance endpoint (Ken confirmed override for this loop only).
PQS_SCORE_FULL_URL = "https://promptqualityscore.com/api/score/full"

# Candidate overshoot for insurance — pull extra so we have headroom
# when some get rejected. OpenOrca replaced natural-instructions after
# pilot evidence showed NI definitions PQS-score as D/F despite hitting
# the structural bucket criteria.
ORCA_CANDIDATES_INITIAL = 150
OASST2_CANDIDATES_INITIAL = 30
ORCA_CANDIDATES_TOPUP = 50

# Seed for --sampled variant (both corpora).
RANDOM_SEED = 42


# ---------------------------------------------------------------------------
# Env loading — read PQS_API_KEY + PQS_INTERNAL_TOKEN from sibling .env.local
# ---------------------------------------------------------------------------

def _load_env_from_siblings():
    """
    Populate required env vars from sibling repo .env files if the shell
    didn't export them.

    - PQS_API_KEY, PQS_INTERNAL_TOKEN  <- ~/Desktop/prompt-optimization-engine/.env.local
    - HF_TOKEN                          <- ~/Desktop/pqs-atlas-agent/.env
    """
    def _ingest(candidate: Path, keys: set[str]):
        if not candidate.exists():
            return
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key in keys and not os.environ.get(key):
                os.environ[key] = val

    _ingest(
        Path.home() / "Desktop" / "prompt-optimization-engine" / ".env.local",
        {"PQS_API_KEY", "PQS_INTERNAL_TOKEN"},
    )
    _ingest(
        Path.home() / "Desktop" / "pqs-atlas-agent" / ".env",
        {"HF_TOKEN"},
    )


# ---------------------------------------------------------------------------
# Polished insurance
# ---------------------------------------------------------------------------

def _grade_label(total: int) -> str:
    if total >= GRADE_A:
        return "A"
    if total >= GRADE_B:
        return "B"
    if total >= GRADE_C:
        return "C"
    if total >= 35:
        return "D"
    return "F"


def _pqs_score_full(prompt: str, vertical: str = "general", timeout: int = 90) -> dict:
    """
    Call /api/score/full with bearer auth + X-PQS-Internal attribution header.
    Returns the parsed response JSON. Raises RuntimeError on non-2xx.
    """
    api_key = os.environ.get("PQS_API_KEY")
    internal = os.environ.get("PQS_INTERNAL_TOKEN")
    if not api_key:
        raise RuntimeError("PQS_API_KEY not set in env (and not found in .env.local)")

    body = json.dumps({"prompt": prompt, "vertical": vertical}).encode("utf-8")
    req = urlrequest.Request(
        PQS_SCORE_FULL_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            **({"X-PQS-Internal": internal} if internal else {}),
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"/api/score/full → HTTP {e.code}: {detail}")
    except URLError as e:
        raise RuntimeError(f"/api/score/full network error: {e}")


def polished_insurance(
    candidates: list[dict],
    source_dataset: str,
    need: int,
    topup_loader: Optional[Iterator[dict]] = None,
    topup_size: int = ORCA_CANDIDATES_TOPUP,
) -> list[dict]:
    """
    Pre-score each candidate via /api/score/full. Keep those with pre_grade
    in {A, B, C}. Log rejections to REJECTIONS_PATH.

    `candidates` is an ordered list of dicts in "candidate" shape (prompt +
    all traceability fields). Returns a LIST of passed candidates (ordered
    deterministically by source_row_id ascending) large enough to satisfy
    `need`. Tops up from `topup_loader` if short.
    """
    passed: list[dict] = []
    rejected_log = REJECTIONS_PATH.open("a", encoding="utf-8")
    try:
        candidate_queue = list(candidates)
        topup_round = 0
        while candidate_queue:
            # Drain one candidate at a time; short-circuit once `need` passed
            cand = candidate_queue.pop(0)
            try:
                resp = _pqs_score_full(cand["prompt"], vertical="general")
                score = (resp.get("original") or {}).get("score") or {}
                total = score.get("total")
                if not isinstance(total, int):
                    raise RuntimeError(f"non-integer total: {total!r}")
                grade = _grade_label(total)
                entry = {**cand, "_insurance": {"total": total, "grade": grade, "score_obj": score}}
                if grade in ("A", "B", "C"):
                    passed.append(entry)
                    print(f"[insurance] ✓ {source_dataset} {cand['source_row_id'][:12]}… total={total} grade={grade}  (pool {len(passed)}/{need})")
                else:
                    rejected_log.write(json.dumps({
                        "source_dataset": source_dataset,
                        "source_row_id": cand["source_row_id"],
                        "prompt_preview": cand["prompt"][:200],
                        "word_count": cand.get("_wc"),
                        "pre_score_total": total,
                        "pre_grade": grade,
                        "dimensions": score.get("dimensions"),
                        "timestamp": time.time(),
                    }) + "\n")
                    rejected_log.flush()
                    print(f"[insurance] ✗ {source_dataset} {cand['source_row_id'][:12]}… total={total} grade={grade} (rejected)")
            except RuntimeError as e:
                # API failures shouldn't reject the candidate — log as rejected
                # with error so methodology is honest.
                print(f"[insurance] !! {source_dataset} {cand['source_row_id'][:12]}… api error: {e}")
                rejected_log.write(json.dumps({
                    "source_dataset": source_dataset,
                    "source_row_id": cand["source_row_id"],
                    "prompt_preview": cand["prompt"][:200],
                    "error": str(e),
                    "timestamp": time.time(),
                }) + "\n")
                rejected_log.flush()

            if len(passed) >= need:
                break

            # If queue drained and we still need more, top up
            if not candidate_queue and len(passed) < need and topup_loader is not None and topup_round < 3:
                topup_round += 1
                added = 0
                for row in topup_loader:
                    bucket, wc = classify(row["prompt"])
                    if bucket == "polished":
                        cand_new = {**row, "_wc": wc}
                        candidate_queue.append(cand_new)
                        added += 1
                        if added >= topup_size:
                            break
                if added == 0:
                    print(f"[insurance] top-up exhausted; {len(passed)}/{need} achieved")
                    break
                print(f"[insurance] top-up round {topup_round}: +{added} candidates queued")
    finally:
        rejected_log.close()

    # Deterministic output order — source_row_id ascending
    passed.sort(key=lambda c: c["source_row_id"])
    return passed


# ---------------------------------------------------------------------------
# Candidate collection per source per bucket
# ---------------------------------------------------------------------------

def _collect_bucketed(
    loader_fn,
    source_dataset: str,
    targets: dict[str, int],
    overshoot: dict[str, int] | None = None,
) -> dict[str, list[dict]]:
    """
    Run `loader_fn()` and bucket rows. Stop per bucket once `targets[bucket]
    + overshoot.get(bucket,0)` rows are collected. Returns:
      { "messy": [rows], "mid": [rows], "polished": [rows] }
    Each row is a dict with atlas traceability fields + _wc + sampling_seed=None.

    Enforces a MAX_PROMPT_CHARS cap (matches PQS's 10,000-char limit with
    some headroom) so insurance calls don't HTTP 400 downstream. Dropped
    rows count as a silent filter — the classifier would have accepted
    them but they're unusable for insurance.
    """
    MAX_PROMPT_CHARS = 9500  # PQS /api/score/full rejects >10,000
    overshoot = overshoot or {}
    caps = {b: targets[b] + overshoot.get(b, 0) for b in targets}
    buckets: dict[str, list[dict]] = {"messy": [], "mid": [], "polished": []}
    seen_prompts: set[str] = set()  # within-source dedup on prompt text

    for raw in loader_fn():
        prompt = raw["prompt"]
        if len(prompt) > MAX_PROMPT_CHARS:
            continue  # too long for PQS endpoint
        if prompt in seen_prompts:
            continue
        seen_prompts.add(prompt)
        bucket, wc = classify(prompt)
        if bucket is None or bucket not in targets:
            continue
        if len(buckets[bucket]) >= caps[bucket]:
            continue
        row_out = {
            "prompt": prompt,
            "source_dataset": source_dataset,
            "source_row_id": raw["source_row_id"],
            "source_split": raw["source_split"],
            "vertical_source_label": raw["vertical_source_label"],
            "license_flag": raw["license_flag"],
            "_wc": wc,
            "_bucket": bucket,
        }
        buckets[bucket].append(row_out)
        # Are we done?
        if all(len(buckets[b]) >= caps[b] for b in targets):
            break

    return buckets


# ---------------------------------------------------------------------------
# Row assembly — convert an internal candidate into the output shape
# ---------------------------------------------------------------------------

def _materialize(cand: dict, sampling_method: str, seed: Optional[int]) -> dict:
    """Produce the 10-field atlas traceability row per the spec."""
    return {
        "prompt": cand["prompt"],
        "quality_bucket": cand["_bucket"],
        "source_dataset": cand["source_dataset"],
        "source_row_id": cand["source_row_id"],
        "source_split": cand["source_split"],
        "vertical_source_label": cand["vertical_source_label"],
        "word_count": cand["_wc"],
        "sampling_method": sampling_method,
        "sampling_seed": seed,
        "license_flag": cand["license_flag"],
    }


def _write_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Mix tables
# ---------------------------------------------------------------------------

# OPTION 1 MIX — after pilot v3 (0/16) and pilot v4 (1/11, ~9%) both
# demonstrated the rubric ceiling: HF public datasets do not yield polished
# rows that pass PQS's 8-dim pre-flight rubric at useful volume. Polished
# target dropped to 0; freed 100 slots reallocated to messy (+50) and the
# pre-existing fallback mid bucket is retained (+50 was absorbed in v4).
#
# Final distribution: 500 rows = 300 messy / 200 mid / 0 polished per file.
# See findings/rubric-ceiling.md for insurance dim breakdowns.
#
#   Full messy 300:   LMSYS 160, WildChat 90, oasst2 50
#   Full mid 200:     WildChat 40, oasst2 80, no_robots 50, OpenOrca 30
#
#   Clean messy 300:  LMSYS 160, WildChat 90, oasst2 50
#   Clean mid 200:    WildChat 40, oasst2 130, OpenOrca 30
FULL_MIX = {
    "lmsys/lmsys-chat-1m":     {"messy": 160, "mid": 0,   "polished": 0},
    "allenai/WildChat-1M":     {"messy": 90,  "mid": 40,  "polished": 0},
    "OpenAssistant/oasst2":    {"messy": 50,  "mid": 80,  "polished": 0},
    "HuggingFaceH4/no_robots": {"messy": 0,   "mid": 50,  "polished": 0},
    "Open-Orca/OpenOrca":      {"messy": 0,   "mid": 30,  "polished": 0},
}
CLEAN_MIX = {
    "lmsys/lmsys-chat-1m":     {"messy": 160, "mid": 0,   "polished": 0},
    "allenai/WildChat-1M":     {"messy": 90,  "mid": 40,  "polished": 0},
    "OpenAssistant/oasst2":    {"messy": 50,  "mid": 130, "polished": 0},
    "Open-Orca/OpenOrca":      {"messy": 0,   "mid": 30,  "polished": 0},
}

LOADER_BY_SOURCE = {
    "HuggingFaceH4/no_robots": load_no_robots,
    "OpenAssistant/oasst2": load_oasst2,
    "Open-Orca/OpenOrca": load_openorca,
    "allenai/WildChat-1M": load_wildchat,
    "lmsys/lmsys-chat-1m": load_lmsys,
}


# ---------------------------------------------------------------------------
# Pilot mode
# ---------------------------------------------------------------------------

# FALLBACK 50-row pilot — proportional to the reduced 250/200/50 full mix.
#   messy 25:     LMSYS 12, WildChat 8, oasst2 5
#   mid 20:       WildChat 4, oasst2 8, no_robots 5, OpenOrca 3
#   polished 5:   oasst2 1, no_robots 1, OpenOrca 3
PILOT_TARGETS = {
    "lmsys/lmsys-chat-1m":     {"messy": 12, "mid": 0, "polished": 0},
    "allenai/WildChat-1M":     {"messy": 8,  "mid": 4, "polished": 0},
    "OpenAssistant/oasst2":    {"messy": 5,  "mid": 8, "polished": 1},
    "HuggingFaceH4/no_robots": {"messy": 0,  "mid": 5, "polished": 1},
    "Open-Orca/OpenOrca":      {"messy": 0,  "mid": 3, "polished": 3},
}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _run(mode: str):
    """mode in {'pilot', 'full'}"""
    # Start fresh on rejections log each run (clobber, not append).
    REJECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    REJECTIONS_PATH.unlink(missing_ok=True)

    if mode == "pilot":
        mixes = {"pilot": PILOT_TARGETS}
    else:
        mixes = {"full": FULL_MIX, "clean": CLEAN_MIX}

    # Pool per (source, bucket) — collected once per source, used across
    # corpora (full and clean can share rows).
    pool: dict[tuple[str, str], list[dict]] = {}

    # Figure out max target per (source, bucket) across all mixes so we
    # only collect as much as needed.
    max_target: dict[tuple[str, str], int] = defaultdict(int)
    for m in mixes.values():
        for src, bucket_map in m.items():
            for b, n in bucket_map.items():
                if n > 0:
                    max_target[(src, b)] = max(max_target[(src, b)], n)

    # Overshoot for sampled variant: collect 1.5x so random.sample has headroom.
    overshoot_factor = 1.5

    # Candidate pools for polished insurance
    orca_polished_candidates: list[dict] = []
    oasst2_polished_candidates: list[dict] = []

    sources_used = sorted({src for m in mixes.values() for src in m.keys()})
    print(f"\n[extract] mode={mode} sources={sources_used}")

    # --- Phase 1: collect per-source pools ---
    for src in sources_used:
        loader_fn = LOADER_BY_SOURCE[src]

        # Compute targets for this source across all buckets present
        src_targets = {}
        for m in mixes.values():
            for b, n in m.get(src, {}).items():
                if n > 0:
                    src_targets[b] = max(src_targets.get(b, 0), n)

        if not src_targets:
            continue

        overshoot = {b: max(0, int(n * (overshoot_factor - 1))) for b, n in src_targets.items()}

        # Polished overshoot needs to be much larger — insurance rejects a
        # substantial fraction. Pilot v2 saw 100% rejection on NI-like content;
        # flan/cot/t0 OpenOrca rows should pass better but we still need
        # headroom. Collect 3x target as a floor.
        if "polished" in src_targets:
            overshoot["polished"] = max(overshoot.get("polished", 0), src_targets["polished"] * 2)

        # For full mode, pull the full insurance-candidate budget
        if src == "Open-Orca/OpenOrca" and "polished" in src_targets and mode == "full":
            overshoot["polished"] = max(overshoot.get("polished", 0), ORCA_CANDIDATES_INITIAL - src_targets["polished"])
        if src == "OpenAssistant/oasst2" and "polished" in src_targets and mode == "full":
            overshoot["polished"] = max(overshoot.get("polished", 0), OASST2_CANDIDATES_INITIAL - src_targets["polished"])

        # For pilot: just take what we need per pilot spec + small headroom
        print(f"[extract] collecting from {src} targets={src_targets} overshoot={overshoot}")
        t0 = time.time()
        buckets = _collect_bucketed(loader_fn, src, src_targets, overshoot=overshoot)
        for b, rows in buckets.items():
            pool[(src, b)] = rows
            print(f"[extract]   {src} {b}: {len(rows)} rows (target {src_targets.get(b,0)}, overshoot {overshoot.get(b,0)})")
        print(f"[extract]   runtime: {time.time() - t0:.1f}s")

    # --- Phase 2: polished insurance (full/clean mode only; pilot does insurance on fewer candidates) ---
    if mode == "full":
        # OpenOrca candidates = pool's polished rows (we asked for 150)
        orca_polished_candidates = pool.get(("Open-Orca/OpenOrca", "polished"), [])
        oasst2_polished_candidates = pool.get(("OpenAssistant/oasst2", "polished"), [])

        # Top-up loader for OpenOrca if we need +50 more polished
        orca_topup_iter = load_openorca()
        orca_seen = {c["source_row_id"] for c in orca_polished_candidates}
        def _orca_topup():
            for row in orca_topup_iter:
                if row["source_row_id"] in orca_seen:
                    continue
                orca_seen.add(row["source_row_id"])
                yield row

        # Max needed: clean corpus wants 90 orca polished; full wants 70. Use 90.
        orca_need = max(FULL_MIX["Open-Orca/OpenOrca"].get("polished", 0),
                        CLEAN_MIX["Open-Orca/OpenOrca"].get("polished", 0))
        # oasst2: 10 in both.
        oasst2_need = max(FULL_MIX["OpenAssistant/oasst2"].get("polished", 0),
                          CLEAN_MIX["OpenAssistant/oasst2"].get("polished", 0))

        print(f"\n[insurance] OpenOrca candidates={len(orca_polished_candidates)} need={orca_need}")
        orca_passed = polished_insurance(
            orca_polished_candidates,
            "Open-Orca/OpenOrca",
            orca_need,
            topup_loader=_orca_topup(),
            topup_size=ORCA_CANDIDATES_TOPUP,
        )
        print(f"[insurance] OpenOrca passed: {len(orca_passed)}/{orca_need}")

        print(f"\n[insurance] oasst2 candidates={len(oasst2_polished_candidates)} need={oasst2_need}")
        oasst2_passed = polished_insurance(
            oasst2_polished_candidates,
            "OpenAssistant/oasst2",
            oasst2_need,
            topup_loader=None,
            topup_size=0,
        )
        print(f"[insurance] oasst2 passed: {len(oasst2_passed)}/{oasst2_need}")

        # Replace pool entries for polished buckets with insurance-passed rows only.
        pool[("Open-Orca/OpenOrca", "polished")] = orca_passed
        pool[("OpenAssistant/oasst2", "polished")] = oasst2_passed
    elif mode == "pilot":
        # Pilot runs insurance on the polished candidates it collected.
        orca_cands = pool.get(("Open-Orca/OpenOrca", "polished"), [])
        oa_cands = pool.get(("OpenAssistant/oasst2", "polished"), [])
        if orca_cands:
            orca_need_pilot = PILOT_TARGETS["Open-Orca/OpenOrca"]["polished"]
            orca_passed = polished_insurance(orca_cands, "Open-Orca/OpenOrca", orca_need_pilot, None, 0)
            pool[("Open-Orca/OpenOrca", "polished")] = orca_passed
        if oa_cands:
            oa_need_pilot = PILOT_TARGETS["OpenAssistant/oasst2"]["polished"]
            oa_passed = polished_insurance(oa_cands, "OpenAssistant/oasst2", oa_need_pilot, None, 0)
            pool[("OpenAssistant/oasst2", "polished")] = oa_passed

    # --- Phase 3: produce output files per mix (and variant, for full mode) ---
    def _build_file(mix: dict, variant: str, seed: Optional[int]) -> list[dict]:
        out: list[dict] = []
        for src in sorted(mix.keys()):
            for bucket in ("messy", "mid", "polished"):
                n = mix[src].get(bucket, 0)
                if n <= 0:
                    continue
                source_pool = pool.get((src, bucket), [])
                if variant == "deterministic":
                    # Sort by source_row_id ascending for stable order
                    ordered = sorted(source_pool, key=lambda c: c["source_row_id"])
                    chosen = ordered[:n]
                else:
                    rng = random.Random(seed)
                    if len(source_pool) >= n:
                        chosen = rng.sample(source_pool, n)
                    else:
                        chosen = list(source_pool)
                if len(chosen) < n:
                    print(f"[WARN] {src} {bucket} only {len(chosen)} rows (need {n})")
                sm = "sampled" if variant == "sampled" else "deterministic"
                ss = RANDOM_SEED if variant == "sampled" else None
                out.extend(_materialize(c, sm, ss) for c in chosen)
        return out

    if mode == "pilot":
        # Pilot: one file, deterministic order
        rows = _build_file(PILOT_TARGETS, "deterministic", None)
        _write_jsonl(PILOT_PATH, rows)
        print(f"\n[pilot] wrote {len(rows)} rows to {PILOT_PATH.relative_to(REPO_ROOT)}")
        _report_distribution("pilot", rows)
    else:
        produced = {}
        for corpus_name, mix in [("full", FULL_MIX), ("clean", CLEAN_MIX)]:
            for variant in ("deterministic", "sampled"):
                rows = _build_file(mix, variant, RANDOM_SEED)
                path = DATA_DIR / f"source-prompts-{corpus_name}-{variant}.jsonl"
                _write_jsonl(path, rows)
                produced[f"{corpus_name}-{variant}"] = rows
                print(f"[extract] wrote {len(rows):>4} rows -> {path.relative_to(REPO_ROOT)}")
        print("")
        for label, rows in produced.items():
            _report_distribution(label, rows)


def _report_distribution(label: str, rows: list[dict]):
    by_source_bucket: dict[tuple[str, str], int] = defaultdict(int)
    for r in rows:
        by_source_bucket[(r["source_dataset"], r["quality_bucket"])] += 1
    total_by_bucket: dict[str, int] = defaultdict(int)
    for (_src, b), n in by_source_bucket.items():
        total_by_bucket[b] += n
    print(f"\n[distribution: {label}] total={len(rows)}  messy={total_by_bucket['messy']}  mid={total_by_bucket['mid']}  polished={total_by_bucket['polished']}")
    # Per-source breakdown
    sources = sorted({s for s, _b in by_source_bucket.keys()})
    print(f"  {'source':<40}  messy  mid   polished")
    for src in sources:
        m = by_source_bucket.get((src, "messy"), 0)
        mi = by_source_bucket.get((src, "mid"), 0)
        p = by_source_bucket.get((src, "polished"), 0)
        print(f"  {src:<40}  {m:>5}  {mi:>5}  {p:>5}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="Run 50-row pilot (Gate B)")
    args = ap.parse_args()

    _load_env_from_siblings()
    if not os.environ.get("PQS_API_KEY"):
        print("ERROR: PQS_API_KEY not set — cannot run polished insurance.")
        sys.exit(2)

    mode = "pilot" if args.pilot else "full"
    t0 = time.time()
    _run(mode)
    print(f"\n[extract] total runtime: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
