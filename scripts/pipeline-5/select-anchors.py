"""
Pipeline 5 — Gate A: Anchor selection.

Deterministic selection of 15 anchor prompts (5 F / 5 D / 5 B) for the
3-rater kappa calibration. Uses SEED=42 throughout.

F-band sources: data/source-prompts-clean-deterministic.jsonl
  (bucket in {messy, mid}, source priority WildChat > no_robots > OpenOrca >> oasst2)

D-band sources: data/pilots/path2-scoping.jsonl
  (Arms 1+2 curated prompts that scored D-grade)

B-band sources: data/pilots/path2-scoping.jsonl
  (Arm 3 F->B Lift outputs via mcp__pqs__optimize_prompt)

Writes data/pipeline-5-anchors.jsonl with fields:
  anchor_id, target_band, prompt_text, source_row_id,
  source_file, generation_method
"""
from __future__ import annotations
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = 42

CORPUS = ROOT / "data" / "source-prompts-clean-deterministic.jsonl"
PATH2 = ROOT / "data" / "pilots" / "path2-scoping.jsonl"
OUT = ROOT / "data" / "pipeline-5-anchors.jsonl"

# F-band source priority. LMSYS accepted only if bucket == "messy"
# and we need to reach 5 rows. Ship claim: WildChat > no_robots >
# OpenOrca >> oasst2.
F_BAND_PRIORITY = [
    "allenai/WildChat-1M",
    "HuggingFaceH4/no_robots",
    "Open-Orca/OpenOrca",
    "OpenAssistant/oasst2",
]


def _load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.open()]


def _select_f_band(rng: random.Random) -> list[dict]:
    """Sample 5 messy/mid rows walking the source-priority order."""
    rows = _load_jsonl(CORPUS)
    pool = [r for r in rows if r["quality_bucket"] in {"messy", "mid"}]
    selected: list[dict] = []
    for src in F_BAND_PRIORITY:
        if len(selected) >= 5:
            break
        bucket_pool = [r for r in pool if r["source_dataset"] == src]
        if not bucket_pool:
            continue
        needed = 5 - len(selected)
        take = min(needed, len(bucket_pool))
        selected.extend(rng.sample(bucket_pool, take))
    assert len(selected) == 5, f"F-band selection returned {len(selected)} rows"

    out = []
    for i, r in enumerate(selected, 1):
        out.append({
            "anchor_id": f"F-{i:02d}",
            "target_band": "F",
            "prompt_text": r["prompt"],
            "source_row_id": r["source_row_id"],
            "source_file": str(CORPUS.relative_to(ROOT)),
            "generation_method": f"pipeline-4-corpus:{r['source_dataset']}:{r['quality_bucket']}",
        })
    return out


def _select_d_band(rng: random.Random) -> list[dict]:
    """Sample 5 D-grade rows from Path-2 Arms 1+2."""
    rows = _load_jsonl(PATH2)
    d_pool = [r for r in rows
              if r["arm"] in {"anthropic", "awesome-chatgpt"}
              and r.get("pre_grade") == "D"]
    # Arms 1+2 landed 8 D-grade rows (4 per arm). Sample 5 deterministically.
    assert len(d_pool) >= 5, f"D-band pool has {len(d_pool)} rows, need >=5"
    selected = rng.sample(d_pool, 5)

    # Path-2 stored only prompt_preview (200 chars). For Arms 1+2 we have
    # the full prompt in scope-path2.py — parse its AST to extract the
    # ANTHROPIC_LIB and AWESOME_CHATGPT list literals without triggering
    # its sibling imports (fsspec, etc).
    import ast
    scope_src = (ROOT / "scripts" / "pipeline-4" / "scope-path2.py").read_text()
    tree = ast.parse(scope_src)
    full_lib: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in {"ANTHROPIC_LIB", "AWESOME_CHATGPT"}:
                for entry in ast.literal_eval(node.value):
                    full_lib[entry["source_row_id"]] = entry["prompt"]

    out = []
    for i, r in enumerate(selected, 1):
        full_prompt = full_lib.get(r["source_row_id"], r["prompt_preview"])
        out.append({
            "anchor_id": f"D-{i:02d}",
            "target_band": "D",
            "prompt_text": full_prompt,
            "source_row_id": r["source_row_id"],
            "source_file": str(PATH2.relative_to(ROOT)),
            "generation_method": f"path2-arm:{r['arm']}",
        })
    return out


def _select_b_band(rng: random.Random) -> list[dict]:
    """All 5 Arm-3 F->B Lift rows (data/pilots/path2-scoping.jsonl).

    The Arm 3 rows store only prompt_preview (200 chars). The optimized
    full prompts were captured in the session evidence but not persisted
    as text in the JSONL (only score + preview). For the kappa run we
    need the full optimized prompts, so we re-derive them here from the
    original MCP responses captured in this script's OPTIMIZED_PROMPTS
    dict below (which was the full text returned on 2026-04-23).
    """
    rows = _load_jsonl(PATH2)
    b_pool = [r for r in rows if r["arm"] == "wildchat-optimize-mcp"]
    assert len(b_pool) == 5, f"B-band Arm-3 pool has {len(b_pool)} rows, need 5"
    # Deterministic ordering by source_row_id so re-running selects identically
    b_pool.sort(key=lambda r: r["source_row_id"])

    out = []
    for i, r in enumerate(b_pool, 1):
        full = OPTIMIZED_PROMPTS.get(r["source_row_id"])
        if not full:
            raise RuntimeError(
                f"No full optimized prompt cached for {r['source_row_id']}. "
                "OPTIMIZED_PROMPTS dict must be populated before Gate A runs."
            )
        out.append({
            "anchor_id": f"B-{i:02d}",
            "target_band": "B",
            "prompt_text": full,
            "source_row_id": r["source_row_id"],
            "source_file": str(PATH2.relative_to(ROOT)),
            "generation_method": "f-to-b-lift:mcp__pqs__optimize_prompt",
        })
    return out


# Full optimized prompts returned by mcp__pqs__optimize_prompt on
# 2026-04-23 for the 5 WildChat mid seeds. Stored here so Gate A is
# self-contained and deterministic without re-calling the paid MCP tool.
OPTIMIZED_PROMPTS = {
    "07ad16b4621469b3f48a816d6d14db1c:109809": (
        "You are a compiler engineer tasked with implementing a complete "
        "program execution pipeline. Your objective is to build a system "
        "that compiles source code directly to runtime backend objects "
        "and executes the compiled program.\n\n"
        "**Requirements:**\n"
        "1. **Grammar Implementation**: Create an ANTLR4 grammar file "
        "`src/PL.g4` that supports the minimal language features needed "
        "to compile and run these four test programs:\n"
        "   - [Include the 4 specific test programs here]\n"
        "   - Grammar must handle: variables, expressions, control flow, "
        "functions, data types\n\n"
        "2. **Backend Implementation**: Develop Kotlin classes in "
        "`src/backend.kt` containing:\n"
        "   - Runtime backend object classes (e.g., CompiledFunction, "
        "CompiledExpression, RuntimeValue)\n"
        "   - Visitor class implementing Syntax-Directed Definition (SDD) "
        "pattern\n"
        "   - Compilation methods that transform AST nodes to executable "
        "backend objects\n"
        "   - Execution engine that runs compiled programs\n\n"
        "3. **Integration**: Ensure the pipeline flows: source code -> "
        "ANTLR parsing -> AST -> visitor compilation -> backend objects "
        "-> execution\n\n"
        "**Constraints:**\n"
        "- Use provided skeleton code in `src/` as foundation\n"
        "- Utilize provided Makefile for compilation process\n"
        "- Support only minimal features required by test programs\n"
        "- Follow SDD compilation methodology\n\n"
        "**Deliverables:**\n"
        "1. Complete `src/PL.g4` grammar file\n"
        "2. Complete `src/backend.kt` with all required classes\n"
        "3. Functional end-to-end pipeline that successfully compiles "
        "and executes all four test programs\n\n"
        "**Testing Approach:**\n"
        "1. Parse each test program using your grammar\n"
        "2. Compile to backend objects using your visitor\n"
        "3. Execute compiled program\n"
        "4. Verify correct output for all test cases"
    ),
    "2c2064ec094d57a664a76da3cef17f7a:112324": (
        "You are a world-renowned SEO expert and educational content "
        "strategist with 15+ years of experience helping students overcome "
        "learning challenges. Your task is to create a comprehensive guide "
        "addressing common student pain points.\n\n"
        "Create a list of exactly 20 pain points that students and learners "
        "commonly face. For each pain point:\n"
        "1. Write a clear, descriptive title (5-8 words)\n"
        "2. Provide 2-3 paragraphs (150-200 words total per pain point) "
        "that:\n"
        "   - Explain why this pain point occurs\n"
        "   - Offer 2-3 specific, actionable solutions\n"
        "   - Include at least one concrete example or technique\n\n"
        "Target audience: High school and college students across all "
        "subjects\n"
        "Tone: Encouraging, practical, and accessible\n"
        "Focus areas: Study habits, time management, motivation, "
        "comprehension, test anxiety, digital distractions, note-taking, "
        "memory retention, procrastination, and academic stress\n\n"
        "Format each pain point as:\n"
        "**Pain Point #[number]: [Title]**\n"
        "[Paragraph 1: Problem explanation]\n"
        "[Paragraph 2: Solutions and examples]\n"
        "[Paragraph 3: Additional tips/techniques if needed]\n\n"
        "Ensure solutions are evidence-based and immediately implementable "
        "by students with limited resources."
    ),
    "45503aaeb51ac7a7c49be6ca1e5b3842:103198": (
        "You are a senior DevOps engineer and C/C++ build system expert. "
        "I'm encountering a clang compilation failure while building FFmpeg "
        "from source. The configure script reports 'clang is unable to "
        "create an executable file' and suggests using --enable-cross-"
        "compile if clang is a cross-compiler. Environment details: "
        "[specify OS, clang version, target architecture]. Please analyze "
        "this step-by-step: 1) Diagnose the root cause of why clang cannot "
        "create executables 2) Determine if this is actually a cross-"
        "compilation scenario 3) Provide specific troubleshooting steps in "
        "order of likelihood 4) Include relevant clang and linker flag "
        "recommendations 5) Suggest how to examine ffbuild/config.log for "
        "diagnostic clues. Format your response as: **Root Cause Analysis:** "
        "[analysis], **Troubleshooting Steps:** [numbered list], **Cross-"
        "Compilation Check:** [how to verify], **Config Log Analysis:** "
        "[what to look for], **Prevention:** [future recommendations]."
    ),
    "8662da0afd8a3427bfd6c64d689cb9a0:106771": (
        "You are a classical literature expert tasked with creating "
        "database update statements. For each ancient Greek comedy listed "
        "below, write a 60-word description of its central meaning and "
        "themes without mentioning the title or author's name. Format each "
        "response as an SQL UPDATE statement using the provided ID.\n\n"
        "Books to analyze:\n"
        "- Aristophanes' Knights (id=74): A political satire about "
        "demagogues\n"
        "- Aristophanes' Lysistrata (id=75): A comedy about women's war "
        "protest\n"
        "- Aristophanes' Peace (id=76): An anti-war comedy\n\n"
        "Required format:\n"
        "UPDATE texts SET `description`=\"[exactly 60 words describing "
        "themes/meaning]\" WHERE id=[book_id];\n\n"
        "Constraints:\n"
        "- Exactly 60 words per description\n"
        "- No mention of book titles or \"Aristophanes\"\n"
        "- Focus on themes, meaning, and significance\n"
        "- Use proper SQL syntax"
    ),
    "8a912051c89d2781dd963232f9593eb0:102751": (
        "You are a 16-year-old high school student from Sydney, Australia, "
        "passionate about technology and eager to build a career in data "
        "science. Your goal is to develop advanced Python programming and "
        "data science skills within the next 12 months to prepare for "
        "university applications and potential internship opportunities. "
        "You have basic computer literacy, access to a laptop with internet, "
        "and can dedicate 10-15 hours per week to learning. You prefer "
        "hands-on, project-based learning and want to connect with mentors "
        "in the Australian tech industry. Create a comprehensive 6-month "
        "learning plan that includes: 1) Three specific course "
        "recommendations (free and paid options) with rationale, 2) Two "
        "mentorship strategies with concrete steps to find mentors, 3) "
        "Three relevant communities or groups in Sydney/Australia to join, "
        "4) A weekly schedule showing how to balance learning with school "
        "commitments, 5) Three milestone projects to track progress, and "
        "6) Specific resources for staying updated with Australian data "
        "science job market trends. Format your response as a structured "
        "action plan with timelines, expected outcomes, and backup options "
        "for each recommendation."
    ),
}


def main():
    rng = random.Random(SEED)
    f_anchors = _select_f_band(rng)
    d_anchors = _select_d_band(rng)
    b_anchors = _select_b_band(rng)  # deterministic-by-sort, ignores rng

    all_anchors = f_anchors + d_anchors + b_anchors
    assert len(all_anchors) == 15

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        for a in all_anchors:
            f.write(json.dumps(a) + "\n")

    print(f"[gate-a] wrote {len(all_anchors)} anchors -> {OUT.relative_to(ROOT)}")
    for a in all_anchors:
        preview = a["prompt_text"][:60].replace("\n", " ")
        print(f"  {a['anchor_id']}  {a['target_band']}  {a['source_row_id'][:40]:40s} '{preview}...'")


if __name__ == "__main__":
    main()
