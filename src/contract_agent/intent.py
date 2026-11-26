"""Step 4: intent detection.

What is this message, before we spend money extracting it? A message can
hold more than one thing ("stop sale these dates, and here's a new offer"),
so this is multi-label: it returns a SET of intents, and the router sends
each part down its own path.

Intents (from the research: rate and availability changes are most of the
traffic after signing; promotions are frequent but lower-volume and live in
their own versioned layer):
  new_contract    full terms for a hotel or season
  ari_update      Availability, Rates, Inventory: rate changes, stop sales and
                  re-opens, allotment / release / min-stay changes, closures
  promotion       special offers: added, changed, extended or ended
  not_a_contract  invoices, marketing, booking admin, complaints, our own
                  requests and counter-offers that nobody has agreed yet

Two classifiers so we can compare them in the eval:
- classify_rules(): keyword rules. Free, instant, transparent, brittle.
- classify_llm(): a small, cheap model (Haiku) with structured output.
  If it isn't confident, the router escalates to the big model (a cascade).
"""
from __future__ import annotations

import re
import time
from enum import Enum

from pydantic import BaseModel, Field

from .llm import EXTRACT_MODEL, FAST_MODEL, get_client


class Label(str, Enum):
    new_contract = "new_contract"
    ari_update = "ari_update"
    promotion = "promotion"
    not_a_contract = "not_a_contract"


class IntentResult(BaseModel):
    """What the classifier returns. Field order matters: reasoning first."""
    reasoning: str = Field(description="One or two sentences: what the sender is asking us to do")
    labels: list[Label] = Field(description="Every intent present. not_a_contract only on its own.")
    confidence: float = Field(description="0 to 1: how sure you are of the labels")


SYSTEM = """You triage messages that hotels send to a UK tour operator's contracting team. \
Decide which of these the message contains. A message can contain more than one.

- new_contract: full commercial terms for a hotel or season (rooms, seasons, rates, policies). \
A negotiation thread counts if it ends in agreed terms.
- ari_update: a change to availability, rates or inventory under an existing contract: rate \
changes or corrections, supplement changes, stop sales, re-opening sales, closures, allotment, \
release or minimum-stay changes, moving between allotment and free sale.
- promotion: a special offer being added, changed, extended or ended (early booking, \
last minute, stay X pay Y, % discounts, kids-stay-free deals).
- not_a_contract: anything else: invoices, marketing with no commercial terms, booking admin, \
complaints, out-of-office replies, our own requests, and counter-offers nobody has agreed yet.

Rules: only label what the HOTEL has stated or agreed. If the message is from us (the operator) \
and nothing is agreed, it is not_a_contract. not_a_contract never appears with other labels. \
If a message has full new terms, label new_contract only."""


def _usage(r) -> dict:
    return {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens}


def classify_llm(text: str, model: str = FAST_MODEL, effort: str = "low") -> tuple[IntentResult, dict]:
    client = get_client()
    t0 = time.time()
    r = client.messages.parse(
        model=model,
        max_tokens=2048,
        system=SYSTEM,
        output_config={"effort": effort},
        messages=[{"role": "user", "content": f"<message>\n{text[:12000]}\n</message>"}],
        output_format=IntentResult,
    )
    out = r.parsed_output
    if out is None:
        raise RuntimeError(f"no intent output: stop_reason={r.stop_reason}")
    out = _tidy(out)
    return out, {"model": model, "seconds": round(time.time() - t0, 2), **_usage(r)}


def _tidy(out: IntentResult) -> IntentResult:
    """House rule enforced in code: not_a_contract is exclusive, new_contract wins."""
    labels = list(dict.fromkeys(out.labels))
    if Label.new_contract in labels:
        labels = [Label.new_contract]
    elif len(labels) > 1 and Label.not_a_contract in labels:
        labels = [x for x in labels if x != Label.not_a_contract]
    out.labels = labels or [Label.not_a_contract]
    return out


# ---------- rules baseline ----------

RULES = {
    Label.new_contract: r"\bcontract\b|\bagreement\b|validity|seasons?\b.*\brates?\b|terms for",
    Label.ari_update: r"stop.?sal|stop.?sell|close (sales|.*for)|fully booked|\bopen\b.*again|"
                      r"allotment|release|min(imum)? stay|rate update|increase|reduce|correct|"
                      r"supplement|free sale|closed",
    Label.promotion: r"offer|discount|\d+ ?% off|early booking|stay \d+ pay \d+|promotion|deal",
}


def classify_rules(text: str) -> IntentResult:
    t = text.lower()
    labels = [lab for lab, pat in RULES.items() if re.search(pat, t)]
    return _tidy(IntentResult(reasoning="keyword rules", labels=labels, confidence=1.0))


ESCALATE_BELOW = 0.7


def classify(text: str) -> tuple[IntentResult, list[dict]]:
    """Cascade: small model first; if unsure, ask the big model. Returns result + call log."""
    res, u = classify_llm(text)
    calls = [u]
    if res.confidence < ESCALATE_BELOW:
        res, u2 = classify_llm(text, model=EXTRACT_MODEL, effort="medium")
        calls.append(u2)
    return res, calls
