"""Step 3: check evidence quotes against the source text.

The model must quote the source for each field. We then check two things,
both in plain code:
1. Is the quote really in the source? If not, the model may have made it up
   (or paraphrased), so the field can't be trusted on the model's word.
2. Does the value appear in the quote? A price of 155 backed by a quote that
   says "145" is a red flag. Derived values (AI = HB + 70) are exempt,
   because by definition they aren't printed.
"""
from __future__ import annotations

import re
from datetime import date
from difflib import SequenceMatcher

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_QUOTES = {ord("‘"): "'", ord("’"): "'", ord("“"): '"', ord("”"): '"'}


def normalise(text: str) -> str:
    t = text.translate(_DASHES).translate(_QUOTES).replace("(cid:127)", " ").lower()
    return re.sub(r"\s+", " ", t).strip()


def quote_in_source(quote: str | None, source: str, fuzzy: float = 0.9) -> bool:
    """True if every part of the quote is in the source.

    Parts are split on '...' and ' / ' so a quote that skips text or joins
    two chat messages still counts, as long as each part is real. Exact
    match after normalising spaces, dashes and case; otherwise a close fuzzy
    match (>= 90% similar) of a same-length window, to forgive small slips
    in wording. Numbers are never forgiven: every number in the quote must
    appear in the matched window, so "21 days" can't pass for "14 days".
    """
    if not quote or not quote.strip():
        return False
    src = normalise(source)
    parts = [normalise(p) for p in re.split(r"\.\.\.|…|\s/\s", quote) if normalise(p)]
    return all(_part_in(p, src, fuzzy) for p in parts)


def _part_in(part: str, src: str, fuzzy: float) -> bool:
    if part in src:
        return True
    n = len(part)
    if n < 12:                      # too short for fuzzy matching to mean anything
        return False
    numbers = re.findall(r"\d+(?:[.,]\d+)?", part)
    step = max(1, n // 8)
    for i in range(0, max(1, len(src) - n + 1), step):
        window = src[max(0, i - step):i + n + step]
        sm = SequenceMatcher(None, part, src[i:i + n], autojunk=False)
        if sm.quick_ratio() >= fuzzy and sm.ratio() >= fuzzy:
            in_window = re.findall(r"\d+(?:[.,]\d+)?", window)
            if all(x in in_window for x in numbers):
                return True
    return False


def value_in_quote(value, quote: str | None) -> bool | None:
    """True/False for numbers and dates; None when the check doesn't apply."""
    if quote is None:
        return None
    q = normalise(quote)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        forms = {f"{v:g}", f"{v:.2f}", f"{v:.2f}".replace(".", ","), f"{v:.1f}"}
        return any(re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", q) for f in forms)
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        d = date.fromisoformat(value)
        day = re.search(rf"(?<!\d)0?{d.day}(?:st|nd|rd|th)?(?!\d)", q)
        month = re.search(rf"[/.-]0?{d.month}(?!\d)|\b{MONTHS[d.month - 1]}", q)
        return bool(day and month)
    return None
