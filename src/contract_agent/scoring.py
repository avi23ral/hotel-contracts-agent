"""Compare a predicted record against gold, field by field.

Lists are keyed by meaning, not position: rates by season|room|board,
rooms by code, etc. So a correct rate listed in a different order still
counts as correct.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

LIST_KEYS = {
    "rooms": lambda x: x.get("code"),
    "seasons": lambda x: x.get("name"),
    "rates": lambda x: f'{x.get("season")}|{x.get("room_code")}|{x.get("board")}',
    "child_policy": lambda x: f'{float(x.get("age_from", 0)):g}',
    "cancellation": lambda x: str(x.get("days_from")),
    "special_offers": lambda x: f'{x.get("type")}|{x.get("stay_from")}',
    "changes": lambda x: f'{x.get("change_type")}|{x.get("date_from") or (x.get("offer") or {}).get("stay_from")}',
}
SKIP = {"name", "supplier_email", "payment_terms", "note", "destination"}  # free text: judged later, not exact-matched


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json", exclude_none=True)
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in SKIP and prefix:   # keep top-level hotel name etc. out of exact match
                continue
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
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
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
