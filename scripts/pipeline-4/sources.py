"""
Per-source row loaders for Pipeline 4.

Each loader is a generator that yields dicts with the uniform shape:

  {
    "prompt": str,
    "source_row_id": str,
    "source_split": str,
    "vertical_source_label": str | None,  # verbatim from source
    "license_flag": str,                  # SPDX or CC-BY-NC-4.0 or LMSYS-custom
  }

Loaders stream rows in native source order. Public sources use fsspec+pyarrow
over HTTPS. The gated LMSYS source uses huggingface_hub.hf_hub_download with
HF_TOKEN auth so the parquets resolve correctly through the gated-repo flow.

Sources:

 - no_robots               -> `load_no_robots()`
 - oasst2                  -> `load_oasst2()`
 - natural_instructions    -> `load_natural_instructions()`  (unused; retained
                              for methodology reference — pilot evidence
                              showed NI definitions fail PQS insurance)
 - openorca                -> `load_openorca()`  (replaces NI as polished source)
 - wildchat                -> `load_wildchat()`
 - lmsys                   -> `load_lmsys()`  (gated; requires HF_TOKEN env)

Schema notes confirmed by probing HF parquet 2026-04-22:

 - no_robots: {prompt, prompt_id, messages, category}
 - oasst2:    {message_id, parent_id, user_id, created_date, text, role, lang,
               review_count, review_result, deleted, rank, synthetic, ...}
   Filter: parent_id IS NULL AND role == 'prompter'
 - natural_instructions: {task_name, id, definition, inputs, targets}
   Dedupe on (definition, task_name).
 - wildchat: {conversation_hash, model, timestamp, conversation[list], turn,
              language, openai_moderation, detoxify_moderation, toxic,
              redacted, state, country, hashed_ip, header}
   First-turn conversation[0] has keys {content, role, turn_identifier, ...}.
   source_row_id = f"{conversation_hash}:{conversation[0].turn_identifier}"
"""
from __future__ import annotations
import os
import fsspec
import pyarrow.parquet as pq
from typing import Iterator

# One shared filesystem instance reduces connection churn.
_FS = fsspec.filesystem("https")

# Datasets-server base URL — gives us canonical parquet shard URLs.
# Each source resolves to 1+ parquet shards under a config+split.
_PARQUET_URLS = {
    "no_robots": [
        "https://huggingface.co/datasets/HuggingFaceH4/no_robots/resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet",
    ],
    "oasst2": [
        "https://huggingface.co/datasets/OpenAssistant/oasst2/resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet",
    ],
    # natural-instructions has 15 train shards. Unused in current pipeline
    # (swapped out for OpenOrca per pilot evidence) but retained for methodology
    # reproducibility.
    "natural_instructions": [
        f"https://huggingface.co/datasets/Muennighoff/natural-instructions/resolve/refs%2Fconvert%2Fparquet/default/train/{i:04d}.parquet"
        for i in range(15)
    ],
    # OpenOrca has 2 train shards (partial-train/0000 and 0001) ~3GB total.
    # Contains FLAN/T0/CoT/NIV distillations with explicit system prompts,
    # which makes it a better natural fit for polished bucket than bare
    # natural-instructions definitions.
    "openorca": [
        f"https://huggingface.co/datasets/Open-Orca/OpenOrca/resolve/refs%2Fconvert%2Fparquet/default/partial-train/{i:04d}.parquet"
        for i in range(2)
    ],
    # WildChat-1M has 14 train shards. Messy rows (short prompts) are common
    # so 1-2 shards usually suffice for 160-320 rows.
    "wildchat": [
        f"https://huggingface.co/datasets/allenai/WildChat-1M/resolve/refs%2Fconvert%2Fparquet/default/train/{i:04d}.parquet"
        for i in range(14)
    ],
}


def _iter_parquet_rows(urls: list[str], batch_size: int = 2048) -> Iterator[dict]:
    """
    Stream rows from a sequence of HTTPS parquet shards via pyarrow+fsspec.
    """
    for url in urls:
        pf = pq.ParquetFile(url, filesystem=_FS)
        for batch in pf.iter_batches(batch_size=batch_size):
            for row in batch.to_pylist():
                yield row


# ---------------------------------------------------------------------------
# Per-source loaders
# ---------------------------------------------------------------------------

def load_no_robots() -> Iterator[dict]:
    """
    HuggingFaceH4/no_robots — NC-licensed, 9500-row train split.

    License: CC-BY-NC-4.0 (non-commercial inheritance — flagged per row).
    """
    for row in _iter_parquet_rows(_PARQUET_URLS["no_robots"]):
        prompt = row.get("prompt")
        rid = row.get("prompt_id")
        if not prompt or not rid:
            continue
        yield {
            "prompt": prompt,
            "source_row_id": rid,
            "source_split": "train",
            "vertical_source_label": row.get("category"),  # "Summarize", "Generation", etc.
            "license_flag": "CC-BY-NC-4.0",
        }


def load_oasst2() -> Iterator[dict]:
    """
    OpenAssistant/oasst2 — Apache-2.0. Atlas uses ROOT prompter messages only
    (parent_id IS NULL AND role == 'prompter'). ~10-15k root prompters
    scattered through 128k total messages.
    """
    for row in _iter_parquet_rows(_PARQUET_URLS["oasst2"]):
        if row.get("parent_id") is not None:
            continue
        if row.get("role") != "prompter":
            continue
        if row.get("deleted"):
            continue
        prompt = row.get("text")
        mid = row.get("message_id")
        if not prompt or not mid:
            continue
        yield {
            "prompt": prompt,
            "source_row_id": mid,
            "source_split": "train",
            # oasst2 has no vertical field. `lang` is language code, not
            # vertical — leave null rather than mislabel.
            "vertical_source_label": None,
            "license_flag": "Apache-2.0",
        }


def load_natural_instructions() -> Iterator[dict]:
    """
    Muennighoff/natural-instructions — Apache-2.0 wrapper on Super-NI.
    280k rows but many instances share identical `definition` — dedupe on
    (definition, task_name) BEFORE the caller samples.

    source_row_id is the per-instance `id`, NOT a composite. We emit one
    row per unique (definition, task_name); multiple instance ids get
    collapsed and we keep the first one in stream order.
    """
    seen: set[tuple[str, str]] = set()
    for row in _iter_parquet_rows(_PARQUET_URLS["natural_instructions"]):
        definition = row.get("definition")
        task_name = row.get("task_name")
        rid = row.get("id")
        if not definition or not task_name or not rid:
            continue
        key = (definition, task_name)
        if key in seen:
            continue
        seen.add(key)
        yield {
            "prompt": definition,
            "source_row_id": rid,
            "source_split": "train",
            "vertical_source_label": task_name,  # e.g. "task001_quoref_question_generation"
            "license_flag": "Apache-2.0",
        }


def load_lmsys() -> Iterator[dict]:
    """
    lmsys/lmsys-chat-1m — GATED, LMSYS-custom license. 6 train shards, ~1M rows.

    Access requires HF auth + LMSYS license acceptance. Reads HF_TOKEN from
    the env. If HF_TOKEN is missing, caller is expected to route through the
    documented fallback (reallocate LMSYS's 120 messy to WildChat +80, oasst2
    +40) rather than let us yield an empty stream silently.

    Unlike WildChat, `conversation_id` is unique per row — no composite key
    needed. source_row_id = conversation_id verbatim. Prompt = first user
    turn's content.
    """
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "HF_TOKEN not set in env. LMSYS is gated — either export HF_TOKEN "
            "or remove LMSYS from the source mix."
        )
    # Lazy import so public-only runs don't pull huggingface_hub into the path
    from huggingface_hub import list_repo_files, hf_hub_download

    shard_filenames = sorted(
        f for f in list_repo_files("lmsys/lmsys-chat-1m", repo_type="dataset", token=token)
        if f.startswith("data/train-") and f.endswith(".parquet")
    )

    for filename in shard_filenames:
        local_path = hf_hub_download(
            repo_id="lmsys/lmsys-chat-1m",
            filename=filename,
            repo_type="dataset",
            token=token,
        )
        pf = pq.ParquetFile(local_path)
        for batch in pf.iter_batches(batch_size=2048):
            for row in batch.to_pylist():
                conv = row.get("conversation") or []
                if not conv:
                    continue
                first = conv[0]
                if not isinstance(first, dict):
                    continue
                if first.get("role") != "user":
                    continue
                prompt = first.get("content")
                rid = row.get("conversation_id")
                if not prompt or not rid:
                    continue
                yield {
                    "prompt": prompt,
                    "source_row_id": rid,
                    "source_split": "train",
                    # LMSYS has no vertical field. `language` is linguistic.
                    # Leave null rather than mislabel.
                    "vertical_source_label": None,
                    "license_flag": "LMSYS-custom",
                }


def load_openorca() -> Iterator[dict]:
    """
    Open-Orca/OpenOrca — MIT-licensed wrapper on FLAN/T0/CoT/NIV distillations.

    Schema: {id, system_prompt, question, response}.
    We use `question` as the atlas prompt and DO NOT redistribute `response`
    (the distilled outputs) — we only use the prompt text.

    IMPORTANT — niv subset is excluded. OpenOrca's `niv.*` rows are
    Super-Natural-Instructions V2 distillations, which have the same
    benchmark-template quality issue that caused NI proper to fail PQS
    insurance 100% in the v1 pilot. Only flan/t0/cot submixes are emitted.

    Atlas field mapping:
      - prompt:                `question`
      - source_row_id:         `id` (e.g. "flan.564327", "cot.345678", "t0.789012")
      - vertical_source_label: submix prefix extracted from `id`
      - license_flag:          "MIT"

    Dedup key = (question, system_prompt) tuple. Emits first occurrence in
    stream order.
    """
    seen: set[tuple[str, str]] = set()
    for row in _iter_parquet_rows(_PARQUET_URLS["openorca"]):
        question = row.get("question")
        system_prompt = row.get("system_prompt") or ""
        rid = row.get("id")
        if not question or not rid:
            continue
        # Skip niv-prefixed rows — these are NI-equivalent content that
        # fails PQS insurance for the same structural reasons documented
        # in the v1 pilot rejections.
        prefix = rid.split(".", 1)[0] if "." in rid else None
        if prefix == "niv":
            continue
        key = (question, system_prompt)
        if key in seen:
            continue
        seen.add(key)
        yield {
            "prompt": question,
            "source_row_id": rid,
            "source_split": "train",
            "vertical_source_label": prefix,
            "license_flag": "MIT",
        }


def load_wildchat() -> Iterator[dict]:
    """
    allenai/WildChat-1M — ODC-BY. 1M conversations across 14 train shards.

    Per the brief, `conversation_hash` is NOT unique (some convos share
    hashes). Use composite source_row_id = f"{conversation_hash}:{turn_identifier}"
    where turn_identifier comes from the FIRST user turn in the conversation.

    Atlas prompt = conversation[0].content (the user's first message).
    """
    for row in _iter_parquet_rows(_PARQUET_URLS["wildchat"]):
        conv = row.get("conversation") or []
        if not conv:
            continue
        first = conv[0]
        if not isinstance(first, dict):
            continue
        if first.get("role") != "user":
            continue
        prompt = first.get("content")
        ch = row.get("conversation_hash")
        tid = first.get("turn_identifier")
        if not prompt or not ch or tid is None:
            continue
        yield {
            "prompt": prompt,
            "source_row_id": f"{ch}:{tid}",
            "source_split": "train",
            # WildChat has no vertical field. `language` is linguistic, not
            # vertical — leave null.
            "vertical_source_label": None,
            "license_flag": "ODC-BY",
        }


# ---------------------------------------------------------------------------
# Registry — the extractor looks up loaders by source identifier
# ---------------------------------------------------------------------------

SOURCE_LOADERS = {
    "HuggingFaceH4/no_robots": load_no_robots,
    "OpenAssistant/oasst2": load_oasst2,
    "Open-Orca/OpenOrca": load_openorca,
    "allenai/WildChat-1M": load_wildchat,
    "lmsys/lmsys-chat-1m": load_lmsys,
    # Muennighoff/natural-instructions removed from active registry per pilot
    # evidence — kept as `load_natural_instructions` function for methodology
    # reference.
}
