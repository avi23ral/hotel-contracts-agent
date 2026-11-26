"""Step 4: the router. Intent decides which pipeline(s) a message goes to.

    message
      -> intent detection (small model; escalate to the big model if unsure)
      -> route, in code (not by the model):
           new_contract                -> full contract extraction (step 3 pipeline)
           ari_update                  -> ARI path: rate / availability / inventory changes
           promotion                   -> promotion path: offers added / changed / ended
           ari_update + promotion      -> BOTH paths, each sees only its own change types
           not_a_contract              -> no extraction; logged for a human to file
      -> merge the paths back into one result for review

Why route in code, not let one big prompt do everything:
- Cost: about a quarter of messages are not contracts. A cheap classifier
  skips the expensive extraction for them entirely.
- Focus: each path has a narrower job, and its own eval.
- Product fit: ARI updates are high volume and time-critical (a missed stop
  sale is an overbooking); promotions are lower volume and live in their own
  versioned layer, so an offer can be changed or ended without touching rates.
"""
from __future__ import annotations

import time

from pydantic import BaseModel

from .ingest import SourceDoc
from .intent import IntentResult, Label, classify
from .llm import EXTRACT_MODEL
from .pipeline import run_pipeline
from .schema import ARI_CHANGES, PROMO_CHANGES, ChangeSet, ExtractionResult, Intent

PATHS = {
    Label.ari_update: ("ari", ARI_CHANGES),
    Label.promotion: ("promotion", PROMO_CHANGES),
}


class Routed(BaseModel):
    intent: IntentResult
    routes: list[str]                       # e.g. ["ari", "promotion"], ["new_contract"], ["ignore"]
    result: ExtractionResult | None = None  # merged across paths
    calls: list[dict] = []                  # every model call: model, tokens, seconds, step
    seconds: float = 0.0


def route_doc(doc: SourceDoc, model: str = EXTRACT_MODEL) -> Routed:
    t0 = time.time()
    intent, calls = classify(doc.text)
    calls = [{**c, "step": "intent"} for c in calls]
    labels = set(intent.labels)

    if Label.new_contract in labels:
        res, usage = run_pipeline(doc, model=model)
        calls.append({**usage, "step": "extract:new_contract"})
        return Routed(intent=intent, routes=["new_contract"], result=res, calls=calls,
                      seconds=round(time.time() - t0, 1))

    paths = [PATHS[lab] for lab in (Label.ari_update, Label.promotion) if lab in labels]
    if not paths:
        return Routed(intent=intent, routes=["ignore"], calls=calls, seconds=round(time.time() - t0, 1))

    parts = []
    for focus, allowed in paths:   # each path: its own focused, two-pass, checked extraction
        res, usage = run_pipeline(doc, model=model, focus=focus, allowed_types=allowed)
        calls.append({**usage, "step": f"extract:{focus}"})
        parts.append(res)
    return Routed(intent=intent, routes=[f for f, _ in paths], result=merge(doc, parts), calls=calls,
                  seconds=round(time.time() - t0, 1))


def merge(doc: SourceDoc, parts: list[ExtractionResult]) -> ExtractionResult:
    """Combine ARI and promotion results into one change set for review."""
    hotel = next((p.change_set.hotel_name for p in parts if p.change_set), "")
    changes = [c for p in parts if p.change_set for c in p.change_set.changes]
    meta, issues = {}, []
    for p in parts:
        meta.update({k: v for k, v in p.field_meta.items() if k.startswith("changes[")})
        issues += p.issues
    if parts and "hotel_name" in parts[0].field_meta:
        meta["hotel_name"] = parts[0].field_meta["hotel_name"]
    return ExtractionResult(source_file=doc.path, intent=Intent.amendment,
                            change_set=ChangeSet(hotel_name=hotel, changes=changes),
                            field_meta=meta, issues=issues)
