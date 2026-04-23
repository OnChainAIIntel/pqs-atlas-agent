"""
Path-2 scoping diagnostic.

Three arms × 5 prompts each to identify where PQS A/B/C-grade anchors
actually exist, since Pipeline 4's corpus is uniformly F-grade (see
findings/rubric-ceiling.md → Mid-bucket verification).

Arms:
  1. Anthropic prompt library — reference-class prompt-engineered source
  2. Awesome ChatGPT Prompts — curated but not rubric-targeted
  3. LLM-regenerated rewrites of WildChat mid rows via /api/optimize —
     closed-loop test: can PQS lift its own corpus?

Results persisted to data/pilots/path2-scoping.jsonl for the evidence
trail. Per-arm grade distribution + verdict printed to stdout.

Ken's framing: Arm 3 is the interesting one. If PQS's optimize endpoint
can push WildChat mid (currently ceiling ~33) to D/C/B/A, that's both
Pipeline 5's anchor source AND a clean closed-loop demo story.
"""
from __future__ import annotations
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "pipeline-4"))
from extract import _pqs_score_full, _grade_label, _load_env_from_siblings

PQS_OPTIMIZE_URL = "https://promptqualityscore.com/api/optimize"
CORPUS = ROOT / "data" / "source-prompts-full-deterministic.jsonl"
OUT = ROOT / "data" / "pilots" / "path2-scoping.jsonl"


# -----------------------------------------------------------------------------
# Arm 1: Anthropic prompt library
# Source: https://docs.anthropic.com/en/resources/prompt-library
# Prompts abridged/paraphrased from the public library — structure matches
# Anthropic's prompt-engineered style (role, context, format, examples).
# -----------------------------------------------------------------------------
ANTHROPIC_LIB = [
    {
        "source_row_id": "anthropic-lib:cite-sources",
        "prompt": (
            "You are an expert research assistant. Here is a document, which I "
            "will ask you to answer questions about. Read the document carefully, "
            "because I'm going to ask you questions about it.\n\n"
            "When you answer, please follow these rules:\n"
            "1. Quote directly from the document when supporting your answer.\n"
            "2. After each quote, include a citation in the format [Source: page N].\n"
            "3. If the document doesn't contain the answer, say so directly — "
            "don't speculate.\n"
            "4. Keep your answer under 200 words unless the question specifically "
            "asks for more detail.\n\n"
            "For example, a well-formed answer would be:\n"
            "\"According to the document, the process involves three phases "
            "[Source: page 2]. The first phase is data collection [Source: page 3].\"\n\n"
            "Now, here is the document and the question."
        ),
    },
    {
        "source_row_id": "anthropic-lib:code-clarifier",
        "prompt": (
            "Your task is to take the code snippet provided and explain it in "
            "simple, easy-to-understand language. Act as an experienced software "
            "engineer tutoring a junior developer. Break down the code's "
            "functionality, purpose, and key components.\n\n"
            "Format your response as:\n"
            "1. One-sentence summary of what the code does.\n"
            "2. A bulleted list walking through each block or function.\n"
            "3. A note on any potential issues, edge cases, or improvements.\n\n"
            "Use analogies where helpful. Avoid jargon unless you define it. "
            "Keep the total explanation between 150 and 300 words."
        ),
    },
    {
        "source_row_id": "anthropic-lib:email-extractor",
        "prompt": (
            "You are a data extraction specialist. Precisely extract all email "
            "addresses from the provided text and return them as a JSON array "
            "of strings. Do not include any other text in your response.\n\n"
            "Rules:\n"
            "- Include only valid-looking email addresses (must contain @ and a "
            "TLD).\n"
            "- Deduplicate — each email appears at most once in the output.\n"
            "- Preserve case as found in the input.\n"
            "- If no emails are found, return an empty array: [].\n\n"
            "Example:\n"
            "Input: \"Contact alice@example.com or bob@test.org for details.\"\n"
            "Output: [\"alice@example.com\", \"bob@test.org\"]\n\n"
            "Now extract from the following text."
        ),
    },
    {
        "source_row_id": "anthropic-lib:socratic-tutor",
        "prompt": (
            "Act as a Socratic-method tutor. The student will ask you a question, "
            "and instead of giving them the answer directly, you must guide them "
            "to the answer through a series of thoughtful questions.\n\n"
            "Your response format for each turn:\n"
            "- One clarifying or guiding question.\n"
            "- One short hint (under 20 words).\n"
            "- No direct answers, ever.\n\n"
            "Constraints:\n"
            "- If the student gets frustrated, adjust the difficulty of your "
            "questions down, but never reveal the answer.\n"
            "- Use analogies from everyday life to scaffold abstract concepts.\n"
            "- After 5 exchanges without progress, provide a more concrete nudge "
            "but still in question form.\n\n"
            "Example exchange:\n"
            "Student: Why does ice float?\n"
            "You: Good question. What do you already know about how the density of "
            "a solid usually compares to its liquid form? (Hint: think about what "
            "happens when you drop a rock in water.)"
        ),
    },
    {
        "source_row_id": "anthropic-lib:function-fabricator",
        "prompt": (
            "You are a senior Python developer. Write a single Python function "
            "that meets the requirements below.\n\n"
            "Requirements:\n"
            "- Function name and signature must match the spec exactly.\n"
            "- Include a docstring with Args, Returns, and Raises sections.\n"
            "- Handle edge cases: empty input, None input, non-string input.\n"
            "- Use type hints for all arguments and return value.\n"
            "- No external dependencies — standard library only.\n"
            "- Include 3 example calls in a `if __name__ == \"__main__\":` block.\n\n"
            "Format: respond with a single fenced Python code block. No prose "
            "before or after the code.\n\n"
            "Example of acceptable output structure:\n"
            "```python\n"
            "def word_count(text: str) -> int:\n"
            "    \"\"\"Count whitespace-separated words.\"\"\"\n"
            "    ...\n"
            "```\n\n"
            "Now, the spec follows."
        ),
    },
]


# -----------------------------------------------------------------------------
# Arm 2: Awesome ChatGPT Prompts
# Source: https://github.com/f/awesome-chatgpt-prompts
# 5 representative entries paraphrased from the public CSV. These are the
# canonical "Act as X" ChatGPT prompts — popular but not rubric-targeted.
# -----------------------------------------------------------------------------
AWESOME_CHATGPT = [
    {
        "source_row_id": "awesome-chatgpt:linux-terminal",
        "prompt": (
            "I want you to act as a Linux terminal. I will type commands and you "
            "will reply with what the terminal should show. I want you to only "
            "reply with the terminal output inside one unique code block, and "
            "nothing else. Do not write explanations. Do not type commands unless "
            "I instruct you to do so. When I need to tell you something in "
            "English, I will do so by putting text inside curly brackets {like "
            "this}. My first command is pwd"
        ),
    },
    {
        "source_row_id": "awesome-chatgpt:english-translator",
        "prompt": (
            "I want you to act as an English translator, spelling corrector and "
            "improver. I will speak to you in any language and you will detect the "
            "language, translate it and answer in the corrected and improved "
            "version of my text, in English. I want you to replace my simplified "
            "A0-level words and sentences with more beautiful and elegant, upper "
            "level English words and sentences. Keep the meaning same, but make "
            "them more literary. I want you to only reply the correction, the "
            "improvements and nothing else, do not write explanations. My first "
            "sentence is \"istanbulu cok seviyom burada olmak cok guzel\""
        ),
    },
    {
        "source_row_id": "awesome-chatgpt:interviewer",
        "prompt": (
            "I want you to act as an interviewer. I will be the candidate and you "
            "will ask me the interview questions for the position of a senior "
            "frontend developer. I want you to only reply as the interviewer. Do "
            "not write all the conservation at once. I want you to only do the "
            "interview with me. Ask me the questions and wait for my answers. Do "
            "not write explanations. Ask me the questions one by one like an "
            "interviewer does and wait for my answers. My first sentence is \"Hi\""
        ),
    },
    {
        "source_row_id": "awesome-chatgpt:javascript-console",
        "prompt": (
            "I want you to act as a javascript console. I will type commands and "
            "you will reply with what the javascript console should show. I want "
            "you to only reply with the terminal output inside one unique code "
            "block, and nothing else. do not write explanations. do not type "
            "commands unless I instruct you to do so. when I need to tell you "
            "something in english, I will do so by putting text inside curly "
            "brackets {like this}. My first command is console.log(\"Hello "
            "World\");"
        ),
    },
    {
        "source_row_id": "awesome-chatgpt:travel-guide",
        "prompt": (
            "I want you to act as a travel guide. I will write you my location "
            "and you will suggest a place to visit near my location. In some cases, "
            "I will also give you the type of places I will visit. You will also "
            "suggest me places of similar type that are close to my first "
            "location. My first suggestion request is \"I am in Istanbul/Beyoglu "
            "and I want to visit only museums.\""
        ),
    },
]


# -----------------------------------------------------------------------------
# Arm 3: /api/optimize rewrites of WildChat mid rows
# Approach: pull top-scoring WildChat mid rows from mid-grade-verification
# data (we already know their totals), optimize each via PQS, rescore.
# -----------------------------------------------------------------------------
VERIFICATION_PATH = ROOT / "data" / "pilots" / "mid-grade-verification.jsonl"


def _load_arm3_seeds():
    """Select 5 WildChat mid seed prompts for optimization.

    Strategy: take the 3 WildChat mid rows already scored by verification
    (totals known: 33, 22, 23) and sample 2 more from the WildChat mid
    pool in the full corpus.
    """
    verified = [json.loads(l) for l in VERIFICATION_PATH.open()]
    wildchat_mid_verified = [r for r in verified
                             if r["bucket"] == "mid"
                             and r["source_dataset"] == "allenai/WildChat-1M"]

    # Load corpus and sample 2 MORE WildChat mid rows not already verified
    corpus = [json.loads(l) for l in CORPUS.open()]
    verified_ids = {r["source_row_id"] for r in wildchat_mid_verified}
    wildchat_mid_pool = [r for r in corpus
                         if r["quality_bucket"] == "mid"
                         and r["source_dataset"] == "allenai/WildChat-1M"
                         and r["source_row_id"] not in verified_ids]
    rng = random.Random(9999)
    extra = rng.sample(wildchat_mid_pool, min(2, len(wildchat_mid_pool)))

    # Build seed list — rehydrate the verification rows' prompt from corpus
    seeds = []
    for vr in wildchat_mid_verified:
        for cr in corpus:
            if cr["source_row_id"] == vr["source_row_id"]:
                seeds.append({
                    "source_row_id": cr["source_row_id"],
                    "prompt": cr["prompt"],
                    "original_pre_score": vr["pre_score_total"],
                })
                break
    for er in extra:
        seeds.append({
            "source_row_id": er["source_row_id"],
            "prompt": er["prompt"],
            "original_pre_score": None,  # will score before optimizing
        })
    return seeds


def _pqs_optimize(prompt: str, vertical: str = "general", timeout: int = 180) -> dict:
    """Call /api/optimize. Returns the parsed response JSON."""
    api_key = os.environ.get("PQS_API_KEY")
    internal = os.environ.get("PQS_INTERNAL_TOKEN")
    if not api_key:
        raise RuntimeError("PQS_API_KEY not set")

    body = json.dumps({"prompt": prompt, "vertical": vertical}).encode("utf-8")
    req = urlrequest.Request(
        PQS_OPTIMIZE_URL,
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
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"/api/optimize → HTTP {e.code}: {detail}")
    except URLError as e:
        raise RuntimeError(f"/api/optimize network error: {e}")


# -----------------------------------------------------------------------------
# Execution
# -----------------------------------------------------------------------------

def _score_and_record(prompt: str, row_id: str, arm: str, out_f) -> dict:
    """Score one prompt, record to output file, return the result dict."""
    resp = _pqs_score_full(prompt, vertical="general")
    score_obj = (resp.get("original") or {}).get("score") or {}
    total = score_obj.get("total")
    grade = _grade_label(total) if isinstance(total, int) else "ERR"
    dims = score_obj.get("dimensions", {})
    result = {
        "arm": arm,
        "source_row_id": row_id,
        "pre_score_total": total,
        "pre_grade": grade,
        "dimensions": dims,
        "prompt_preview": prompt[:200],
        "word_count": len(prompt.split()),
        "timestamp": time.time(),
    }
    out_f.write(json.dumps(result) + "\n")
    out_f.flush()
    return result


def main():
    _load_env_from_siblings()
    if not os.environ.get("PQS_API_KEY"):
        print("ERROR: PQS_API_KEY not set")
        sys.exit(2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    arm1_results, arm2_results, arm3_results = [], [], []

    t0 = time.time()
    with OUT.open("w") as f:
        # --- Arm 1 ---
        print(f"\n[arm1] Anthropic prompt library — {len(ANTHROPIC_LIB)} prompts")
        for p in ANTHROPIC_LIB:
            try:
                r = _score_and_record(p["prompt"], p["source_row_id"], "anthropic", f)
                arm1_results.append(r)
                print(f"  {p['source_row_id']:45s} total={r['pre_score_total']:2d} grade={r['pre_grade']}")
            except Exception as e:
                print(f"  {p['source_row_id']}: ERROR {e}")

        # --- Arm 2 ---
        print(f"\n[arm2] Awesome ChatGPT Prompts — {len(AWESOME_CHATGPT)} prompts")
        for p in AWESOME_CHATGPT:
            try:
                r = _score_and_record(p["prompt"], p["source_row_id"], "awesome-chatgpt", f)
                arm2_results.append(r)
                print(f"  {p['source_row_id']:45s} total={r['pre_score_total']:2d} grade={r['pre_grade']}")
            except Exception as e:
                print(f"  {p['source_row_id']}: ERROR {e}")

        # --- Arm 3 ---
        print(f"\n[arm3] WildChat mid rewrites via /api/optimize")
        seeds = _load_arm3_seeds()
        print(f"  loaded {len(seeds)} WildChat mid seeds")
        for seed in seeds:
            try:
                # Pre-score if not already known
                if seed["original_pre_score"] is None:
                    print(f"  scoring seed {seed['source_row_id'][:20]}…")
                    resp = _pqs_score_full(seed["prompt"])
                    seed["original_pre_score"] = ((resp.get("original") or {}).get("score") or {}).get("total")
                pre = seed["original_pre_score"]
                pre_grade = _grade_label(pre) if isinstance(pre, int) else "?"

                # Optimize
                print(f"  optimizing {seed['source_row_id'][:20]}… (orig={pre}/{pre_grade})")
                opt_resp = _pqs_optimize(seed["prompt"])

                # The response shape is unknown — inspect and be defensive
                optimized_prompt = None
                optimized_score = None
                optimized_grade = None
                opt_dims = {}
                # Try a few plausible keys
                if "optimized" in opt_resp:
                    optimized = opt_resp["optimized"]
                    optimized_prompt = optimized.get("prompt") or optimized.get("text")
                    score_obj = optimized.get("score") or {}
                    optimized_score = score_obj.get("total")
                    opt_dims = score_obj.get("dimensions", {})
                elif "optimizedPrompt" in opt_resp:
                    optimized_prompt = opt_resp["optimizedPrompt"]
                elif "result" in opt_resp and isinstance(opt_resp["result"], dict):
                    optimized_prompt = opt_resp["result"].get("prompt")
                    optimized_score = opt_resp["result"].get("score", {}).get("total")
                elif "prompt" in opt_resp:
                    optimized_prompt = opt_resp["prompt"]

                # If we didn't get a score from optimize response, rescore
                if optimized_prompt and optimized_score is None:
                    resp2 = _pqs_score_full(optimized_prompt)
                    sc2 = (resp2.get("original") or {}).get("score") or {}
                    optimized_score = sc2.get("total")
                    opt_dims = sc2.get("dimensions", {})

                if isinstance(optimized_score, int):
                    optimized_grade = _grade_label(optimized_score)

                result = {
                    "arm": "wildchat-optimize",
                    "source_row_id": seed["source_row_id"],
                    "original_pre_score": pre,
                    "original_grade": pre_grade,
                    "optimized_pre_score": optimized_score,
                    "optimized_grade": optimized_grade,
                    "delta": (optimized_score - pre) if isinstance(optimized_score, int) and isinstance(pre, int) else None,
                    "dimensions": opt_dims,
                    "original_preview": seed["prompt"][:200],
                    "optimized_preview": (optimized_prompt or "")[:200],
                    "optimize_response_keys": list(opt_resp.keys()),
                    "timestamp": time.time(),
                }
                arm3_results.append(result)
                f.write(json.dumps(result) + "\n")
                f.flush()
                print(f"    → orig={pre}/{pre_grade}  opt={optimized_score}/{optimized_grade}  Δ={result['delta']}")
            except Exception as e:
                print(f"  ERROR on {seed['source_row_id']}: {e}")

    print(f"\n[scope-path2] done in {time.time()-t0:.1f}s; evidence → {OUT}")

    # --- Summary ---
    print("\n=== PER-ARM SUMMARY ===")
    for arm_name, results in [("Arm 1 (Anthropic lib)", arm1_results),
                               ("Arm 2 (Awesome ChatGPT)", arm2_results)]:
        if not results:
            print(f"  {arm_name}: no results")
            continue
        totals = [r["pre_score_total"] for r in results if isinstance(r["pre_score_total"], int)]
        grades = [r["pre_grade"] for r in results]
        counts = {g: grades.count(g) for g in ("A", "B", "C", "D", "F")}
        print(f"  {arm_name}: n={len(results)} avg={statistics.mean(totals):.1f} "
              f"min={min(totals)} max={max(totals)} "
              f"A={counts['A']} B={counts['B']} C={counts['C']} D={counts['D']} F={counts['F']}")

    if arm3_results:
        print(f"\n  Arm 3 (WildChat → /api/optimize rewrites):")
        for r in arm3_results:
            print(f"    {r['source_row_id'][:25]:25s}  orig={r['original_pre_score']}/{r['original_grade']} "
                  f"→ opt={r['optimized_pre_score']}/{r['optimized_grade']} "
                  f"Δ={r['delta']}")
        deltas = [r["delta"] for r in arm3_results if isinstance(r["delta"], int)]
        opt_grades = [r["optimized_grade"] for r in arm3_results if r["optimized_grade"]]
        opt_abc = sum(1 for g in opt_grades if g in ("A", "B", "C"))
        if deltas:
            print(f"    avg Δ={statistics.mean(deltas):+.1f}  max Δ={max(deltas):+d}")
            print(f"    A/B/C rate: {opt_abc}/{len(opt_grades)}")

    # --- Verdict ---
    print("\n=== VERDICT ===")
    arm1_abc = sum(1 for r in arm1_results if r.get("pre_grade") in ("A", "B", "C"))
    arm2_abc = sum(1 for r in arm2_results if r.get("pre_grade") in ("A", "B", "C"))
    arm3_abc = sum(1 for r in arm3_results if r.get("optimized_grade") in ("A", "B", "C"))

    print(f"  Arm 1 (Anthropic) A/B/C: {arm1_abc}/{len(arm1_results)}")
    print(f"  Arm 2 (Awesome ChatGPT) A/B/C: {arm2_abc}/{len(arm2_results)}")
    print(f"  Arm 3 (WildChat optimize) A/B/C: {arm3_abc}/{len(arm3_results)}")

    if arm3_abc >= 3:
        print("\n  → Arm 3 works: PQS can lift its own corpus. CLEANEST PATH for Pipeline 5.")
    elif arm1_abc >= 3 or arm2_abc >= 3:
        best = "Anthropic lib" if arm1_abc >= arm2_abc else "Awesome ChatGPT"
        print(f"\n  → External curation path: {best} yields A/B/C. Use as anchor source.")
    else:
        print("\n  → All three arms failed. Need hand-curated PQS-canonical exemplars.")


if __name__ == "__main__":
    main()
