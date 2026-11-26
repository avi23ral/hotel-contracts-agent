"""Extractor: one Claude call, structured output.

Step 2 made this the baseline. Step 3 keeps the single call but asks for
evidence notes keyed by the same field paths the eval uses, so every field
can be checked against the source (see pipeline.py). The baseline's
"trust the model's own confidence" logic is kept as baseline_meta() so the
two policies can be compared on the same run.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .ingest import SourceDoc, load_source
from .llm import EXTRACT_MODEL, get_client
from .schema import ChangeSet, Contract, Intent, Status


class FieldNote(BaseModel):
    """The model's own evidence and confidence for one or more fields."""
    paths: list[str] = Field(description='Field paths or item prefixes, e.g. ["rates[High|DBL|HB]"], '
                                         '["seasons[Low].date_from", "seasons[Low].date_to"], ["release_days"]')
    evidence: str = Field(description="Exact quote copied character for character from the source text")
    confidence: float = Field(description="0 to 1: how sure you are the value is correct")
    derived: bool = Field(default=False, description="True if the value was computed (supplement added, "
                                                     "year inferred, per-person converted), not copied")


class LLMExtraction(BaseModel):
    """Shape we ask Claude to return. Uses lists only (no free-form dicts) so it
    works with strict structured outputs."""
    intent: Intent
    contract: Contract | None = None
    change_set: ChangeSet | None = None
    field_notes: list[FieldNote] = []


SYSTEM = """You are a hotel contracting assistant for a UK tour operator. You turn hotel \
contracts, emails and chats into structured records.

Rules:
- intent: "new_contract" if the source sets out full terms for a hotel; "amendment" if it \
changes terms of an existing contract (stop sales and re-opens, rate changes, allotment, \
release or min-stay changes, special offers added, changed or ended); \
"not_a_contract" otherwise. Fill only contract OR change_set to match.
- In email threads, the latest agreed terms win. Ignore counter-offers that were not accepted.
- Use these room codes: Double Standard=DBL, Double Sea View=DSV, Family Room=FAM, \
Standard Double=STD, Junior Suite=JSU. Otherwise make a short uppercase code.
- Board codes: RO, BB, HB, FB, AI. If a board price is given as a supplement, compute the full price.
- Dates are ISO (YYYY-MM-DD). If a year is missing, infer it from the contract period.
- Season names: use the short label only ("A", not "Season A"; "High", not "High season").
- Our default terms are net, per room per night. If the hotel quotes per person or commissionable, \
record exactly what they quote; do not convert.
- Rooms: inventory is "allotment" if the hotel guarantees a number of rooms per night, \
"on_request" if each booking needs confirmation, otherwise "free_sale". Only allotment rooms have \
an allotment count. Leave hotel_id and giata_id null (they come from our property master).
- A special offer valid on all boards has boards = [] (empty means all).
- A free child band is discount_pct 100.
- Never invent values. If something isn't stated, leave it null.

Evidence (field_notes): every value you return must be covered by a field_note. Paths use this format:
- top-level fields: "release_days", "contract_start", "currency"
- list items are keyed in square brackets: rooms[DBL], seasons[High], rates[High|DBL|HB] \
(season|room|board), child_policy[2] (age_from), cancellation[15] (days_from), \
special_offers[early_booking|2027-05-01] (type|stay_from), changes[stop_sale|2027-08-10] \
(change_type|date_from), changes[offer_changed|early_booking] (change_type|offer type)
- a sub-field: rates[High|DBL|HB].price, seasons[Low].date_to, changes[stop_sale|2027-08-10].room_codes
A note on an item or list covers all of its sub-fields. Give one note per item; add a separate note \
for a sub-field only when its evidence is different. The evidence must be an exact, contiguous quote \
from the source (you may join two quotes with " ... "). Set derived=true when you computed the value. \
Be honest with confidence: use below 0.9 whenever you had to infer, convert or choose between messages.
- Return your answer by calling the record_extraction tool exactly once."""


FOCUS = {
    "ari": "Focus: extract ONLY availability, rate and inventory changes (stop_sale, open_sale, "
           "rate_change, allotment_change, release_change, min_stay_change). Ignore special offers: "
           "another step handles them.",
    "promotion": "Focus: extract ONLY special-offer changes (offer_added, offer_changed, offer_ended). "
                 "Ignore stop sales, rate changes and other availability changes: another step "
                 "handles them.",
}


CONTEXT_NOTE = (
    "Reference material from our system follows: the hotel's existing contract (the parts relevant "
    "to this message) and house rules from our playbook. Use it to complete the changes: room codes, "
    "the year, and conditions an offer change inherits from the existing offer. The message wins "
    "wherever it states something explicitly. For a value taken from the existing contract, quote the "
    "reference text exactly as evidence and set derived=true.")


def call_model(doc: SourceDoc, model: str = EXTRACT_MODEL, focus: str | None = None,
               context: str | None = None) -> tuple[LLMExtraction, dict]:
    """One extraction pass. Returns the raw model output and token usage.

    focus narrows an amendment to one router path ("ari" or "promotion").
    context is retrieved reference material (step 5, RAG)."""
    client = get_client()
    # Strict structured outputs (messages.parse) rejected this schema: "compiled
    # grammar is too large". So we use a non-strict tool call and validate
    # the result with Pydantic ourselves. Validation errors are real eval failures.
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"Source channel: {doc.channel}\n\n<source>\n{doc.text}\n</source>"
                   + (f"\n\n{CONTEXT_NOTE}\n\n{context}" if context else "")
                   + (f"\n\n{FOCUS[focus]}" if focus else "")}],
        tools=[{"name": "record_extraction",
                "description": "Record the structured extraction for this source.",
                "input_schema": LLMExtraction.model_json_schema()}],
        # This model doesn't accept a forced tool_choice, so we leave it on auto
        # and tell it to call the tool. A reply without the tool call is an error.
        tool_choice={"type": "auto"},
    )
    usage = {"input_tokens": response.usage.input_tokens,
             "output_tokens": response.usage.output_tokens, "stop_reason": response.stop_reason}
    block = next((b for b in response.content if b.type == "tool_use"), None)
    if block is None:
        raise RuntimeError(f"No structured output for {doc.path}: stop_reason={response.stop_reason}")
    return LLMExtraction.model_validate(block.input), usage


def baseline_status(model_conf: float | None) -> Status:
    """Step 2 policy: one global bar on the model's own confidence."""
    return Status.auto_accept if (model_conf or 0) >= 0.8 else Status.needs_review


def extract_file(path: Path, model: str = EXTRACT_MODEL):
    """Full step 3 pipeline (two passes, checks, confidence). See pipeline.py."""
    from .pipeline import run_pipeline
    return run_pipeline(load_source(path), model=model)
