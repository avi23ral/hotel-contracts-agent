"""Step 3: one confidence score per field, and a per-field threshold.

The score starts from the model's own rating, then our checks can only push
it DOWN. Each red flag caps the score:

    signal                                          cap
    no evidence quote for the field                 0.50
    quote not found in the source text              0.40
    value not in its quote (and not derived)        0.70
    a validator rule failed on this field           0.30
    second extraction pass gave a different value   0.35
    second pass left the field out                  0.60

So a field is only auto-accepted when the model is sure AND the quote is
real AND the rules pass AND two runs agree. Caps (rather than a weighted
average) keep the score explainable: the reviewer sees exactly which check
pulled it down.

Thresholds follow the cost of a mistake (research in the build plan):
cancellation terms hurt most, then price, then dates, then board.
"""
from __future__ import annotations

CAPS = {
    "no_evidence": 0.50,
    "quote_not_in_source": 0.40,
    "value_not_in_quote": 0.70,
    "validator_error": 0.30,
    "passes_disagree": 0.35,
    "missing_in_second_pass": 0.60,
}
REASONS = {
    "no_evidence": "no supporting quote",
    "quote_not_in_source": "quote not found in the source",
    "value_not_in_quote": "value not in its quote",
    "validator_error": "broke a validator rule",
    "passes_disagree": "two extraction runs disagree",
    "missing_in_second_pass": "second run left it out",
}

THRESHOLDS = {"cancellation": 0.95, "price": 0.93, "dates": 0.90, "board": 0.80, "other": 0.75}
# Relative cost of one wrong field that slips through. Used to weight escapes.
COST_WEIGHTS = {"cancellation": 5, "price": 4, "dates": 3, "board": 2, "other": 1}

PRICE_LEAVES = {"price", "new_price", "single_use_price", "fixed_price", "discount_pct",
                "commission_pct", "amount", "charge_pct"}
DATE_LEAVES = {"contract_start", "contract_end", "date_from", "date_to", "book_from", "book_by",
               "stay_from", "stay_to", "release_days", "min_stay_nights"}
BOARD_LEAVES = {"board", "boards"}


def category(path: str) -> str:
    if path.startswith("cancellation"):
        return "cancellation"
    leaf = path.rsplit(".", 1)[-1].split("[")[0]
    if leaf in PRICE_LEAVES:
        return "price"
    if leaf in DATE_LEAVES:
        return "dates"
    if leaf in BOARD_LEAVES:
        return "board"
    return "other"


def combine(model_conf: float | None, *, has_evidence: bool, evidence_found: bool | None,
            value_in_quote: bool | None, derived: bool, validator_error: bool,
            agrees: bool | None) -> tuple[float, list[str]]:
    """Return (score, reasons). agrees=None means the second pass lacked the field."""
    score = 0.5 if model_conf is None else max(0.0, min(1.0, model_conf))
    hit = []
    if not has_evidence:
        hit.append("no_evidence")
    elif evidence_found is False:
        hit.append("quote_not_in_source")
    elif value_in_quote is False and not derived:
        hit.append("value_not_in_quote")
    if validator_error:
        hit.append("validator_error")
    if agrees is False:
        hit.append("passes_disagree")
    elif agrees is None:
        hit.append("missing_in_second_pass")
    for h in hit:
        score = min(score, CAPS[h])
    return round(score, 3), [REASONS[h] for h in hit]
