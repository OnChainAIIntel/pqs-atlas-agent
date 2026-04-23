"""
Pipeline 4 quality-bucket classifier.

Mechanical criteria per the approved spec. No subjective judgment.

 - messy:    word_count < 30 OR (no role AND no format AND no example)
 - mid:      30 <= word_count <= 100 AND signal_count in {1, 2}
             (has at least one of {role, format, example, criteria},
              but missing at least 2 of the 4)
 - polished: word_count >= 100 AND all four signals present
             (role AND format AND >=1 example AND success criteria)

Anything else (e.g. wc 30-100 with 3+ signals, or wc >=100 missing a
signal) returns None and is DROPPED rather than forced into a bucket.

Detection heuristics (documented here so the judge can audit):

 - role:     regex for "act as", "you are a...", "as an expert/assistant/..."
 - format:   regex for JSON/YAML/XML/markdown/table/bullet/list/CSV
             or phrases like "in the format of", "respond in", "format:"
 - example:  regex for "for example", "e.g.", "example:", "here's an example"
 - criteria: regex for modals + caps (must / should / requirement / constraint)
             or length caps ("at least N", "no more than N", "between X and Y")
             or prohibitions ("avoid", "do not", "don't")

Word count is whitespace-split on the raw prompt text.
"""
from __future__ import annotations
import re
from typing import Optional

_ROLE_PATTERNS = [
    re.compile(r"\bact as (?:a[n]? |the )?\w", re.IGNORECASE),
    re.compile(r"\byou are (?:a[n]? |the )?\w", re.IGNORECASE),
    re.compile(r"\bpretend (?:to be|you are)\b", re.IGNORECASE),
    re.compile(r"\bas an? (?:expert|professional|assistant|engineer|writer|analyst|consultant|tutor|teacher|developer|scientist|researcher|editor|specialist)\b", re.IGNORECASE),
    re.compile(r"\btake on the role of\b", re.IGNORECASE),
    re.compile(r"\bimagine you(?:'re| are)\b", re.IGNORECASE),
]
_FORMAT_PATTERNS = [
    re.compile(r"\b(?:json|yaml|xml|markdown|csv|tsv)\b", re.IGNORECASE),
    re.compile(r"\b(?:as a |in (?:a |the )?)(?:table|list|bullet(?:ed)? list|numbered list)\b", re.IGNORECASE),
    re.compile(r"\bbullet[- ]?point[s]?\b", re.IGNORECASE),
    re.compile(r"\bin the (?:format|form|shape|style) of\b", re.IGNORECASE),
    re.compile(r"\b(?:respond|reply|return|output|answer|format)(?:\s+the\s+answer)?\s+(?:in|with|as)\b", re.IGNORECASE),
    re.compile(r"\bformat\s*:", re.IGNORECASE),
    re.compile(r"\b(?:output|response) (?:shape|schema|format)\b", re.IGNORECASE),
]
_EXAMPLE_PATTERNS = [
    re.compile(r"\bfor example\b", re.IGNORECASE),
    re.compile(r"\be\.?g\.?\b", re.IGNORECASE),
    re.compile(r"\bexamples?\s*:", re.IGNORECASE),
    re.compile(r"\bhere(?:'s| is) an example\b", re.IGNORECASE),
    re.compile(r"\blike (?:this|so)\s*:", re.IGNORECASE),
    re.compile(r"\bas follows\b", re.IGNORECASE),
    re.compile(r"\b(?:sample|example) (?:input|output)\s*:", re.IGNORECASE),
]
_CRITERIA_PATTERNS = [
    re.compile(r"\b(?:must|should|need(?:s)? to|have to) (?:be|include|contain|follow|avoid|answer|respond|explain|cover)\b", re.IGNORECASE),
    re.compile(r"\b(?:requirement|requirements|constraint|criteria|criterion|rules?)\s*:", re.IGNORECASE),
    re.compile(r"\b(?:at least|no more than|at most|exactly|between|fewer than|under|over)\s+\d", re.IGNORECASE),
    re.compile(r"\b(?:avoid|don'?t|do not)\b", re.IGNORECASE),
    re.compile(r"\b(?:max(?:imum)?|min(?:imum)?)\s+(?:of\s+)?\d", re.IGNORECASE),
    re.compile(r"\b(?:limit\s+(?:to|of)|capped\s+at|within)\s+\d", re.IGNORECASE),
]


def _has_any(text: str, patterns: list[re.Pattern]) -> bool:
    for p in patterns:
        if p.search(text):
            return True
    return False


def signals(text: str) -> dict:
    """Return the 4 signal booleans for `text`."""
    return {
        "role": _has_any(text, _ROLE_PATTERNS),
        "format": _has_any(text, _FORMAT_PATTERNS),
        "example": _has_any(text, _EXAMPLE_PATTERNS),
        "criteria": _has_any(text, _CRITERIA_PATTERNS),
    }


def classify(text: str) -> tuple[Optional[str], int]:
    """
    Returns (bucket_name_or_None, word_count).

    None means the row didn't satisfy any bucket and should be dropped.
    """
    if not text or not text.strip():
        return (None, 0)
    wc = len(text.split())
    s = signals(text)

    # MESSY — short OR missing all three of role/format/example
    if wc < 30 or (not s["role"] and not s["format"] and not s["example"]):
        return ("messy", wc)

    count = int(s["role"]) + int(s["format"]) + int(s["example"]) + int(s["criteria"])

    # POLISHED — long + all four signals
    if wc >= 100 and count == 4:
        return ("polished", wc)

    # MID — 30-100 words, 1-2 signals (missing >= 2 of the 4)
    if 30 <= wc <= 100 and 1 <= count <= 2:
        return ("mid", wc)

    # Anything else is a drop
    return (None, wc)


if __name__ == "__main__":
    # Smoke test: classifier runs without errors and produces plausible labels.
    # Real validation is against the pilot's 50 rows on actual source data.
    for text in [
        "help me",
        "write a tweet about coffee for my marketing team",
        "You are a senior editor. Review this draft and return the three most important "
        "changes as a numbered list. Focus on clarity. Keep each change under 15 words.",
    ]:
        got, wc = classify(text)
        s = signals(text)
        print(f"wc={wc:3d}  bucket={got!s:>8}  signals={s}  text={text[:60]!r}")
