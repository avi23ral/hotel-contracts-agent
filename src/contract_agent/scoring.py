"""Compare a predicted record against gold, field by field.

Lists are keyed by meaning, not position: rates by season|room|board,
rooms by code, etc. So a correct rate listed in a different order still
counts as correct.

Step 3 fixes two kinds of false miss found in the first eval:
- Season labels: "Season A" and "A" are the same season. Labels are
  canonicalised before they are used as keys or compared.
- Amendment keys: an offer change was keyed by its stay dates, so when the
  model left the dates out (they live in the existing contract) every field
  of the change counted as missing AND extra. Offer changes are now keyed
  by change type + offer type, and the dates are scored as ordinary values.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

_SEASON_WORD = re.compile(r"^\s*season\s+|\s+season\s*$", re.I)


def canonical_season(name: Any) -> str:
    """'Season A' -> 'A', 'high season' -> 'High', ' Low ' -> 'Low'."""
    s = _SEASON_WORD.sub("", str(name or "")).strip()
    return s[:1].upper() + s[1:].lower() if s else s


def _change_key(x: dict) -> str:
    ct = x.get("change_type")
    offer = x.get("offer") or {}
    if ct in ("offer_added", "offer_changed", "offer_ended") and offer.get("type"):
        return f"{ct}|{offer['type']}"
    return f"{ct}|{x.get('date_from')}"


LIST_KEYS = {
    "rooms": lambda x: x.get("code"),
    "seasons": lambda x: canonical_season(x.get("name")),
    "rates": lambda x: f'{canonical_season(x.get("season"))}|{x.get("room_code")}|{x.get("board")}',
    "child_policy": lambda x: f'{float(x.get("age_from", 0)):g}',
    "cancellation": lambda x: str(x.get("days_from")),
    "special_offers": lambda x: f'{x.get("type")}|{x.get("stay_from")}',
    "changes": _change_key,
}
SKIP = {"name", "supplier_email", "payment_terms", "note", "destination"}  # free text: judged later, not exact-matched
CANON_VALUES = {"season": canonical_season}   # leaf values normalised before comparison


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json", exclude_none=True)
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in SKIP and prefix:   # keep top-level hotel name etc. out of exact match
                continue
            if k in CANON_VALUES and isinstance(v, str):
                v = CANON_VALUES[k](v)
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list):
        field = prefix.rsplit(".", 1)[-1]
        keyfn = LIST_KEYS.get(field)
        if keyfn and all(isinstance(i, dict) for i in obj):
            for item in obj:
                out.update(flatten(item, f"{prefix}[{keyfn(item)}]"))
        else:  # list of scalars, e.g. boards: compare as a sorted set
            out[prefix] = sorted(map(str, obj))
    else:
        out[prefix] = obj
    return out


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) < 0.01
    if isinstance(a, str) and isinstance(b, str):   # case, spaces and a trailing full stop don't matter
        return a.strip().rstrip(".").lower() == b.strip().rstrip(".").lower()
    return a == b


class FieldScore(BaseModel):
    correct: list[str] = []
    wrong: list[tuple[str, Any, Any]] = []     # path, predicted, gold
    missing: list[str] = []                    # in gold, not predicted
    extra: list[str] = []                      # predicted, not in gold (possible hallucination)

    @property
    def precision(self) -> float:
        n = len(self.correct) + len(self.wrong) + len(self.extra)
        return len(self.correct) / n if n else 1.0

    @property
    def recall(self) -> float:
        n = len(self.correct) + len(self.wrong) + len(self.missing)
        return len(self.correct) / n if n else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def outcome(self, path: str) -> str:
        """correct | wrong | extra for a predicted field (used for calibration)."""
        if path in self._correct_set:
            return "correct"
        return "extra" if path in self._extra_set else "wrong"

    @property
    def _correct_set(self):
        return set(self.correct)

    @property
    def _extra_set(self):
        return set(self.extra)


def score_fields(pred: Any, gold: Any) -> FieldScore:
    p, g = flatten(pred) if pred else {}, flatten(gold) if gold else {}
    s = FieldScore()
    for path, gv in g.items():
        if path not in p:
            s.missing.append(path)
        elif _equal(p[path], gv):
            s.correct.append(path)
        else:
            s.wrong.append((path, p[path], gv))
    s.extra = [k for k in p if k not in g]
    return s
