"""Step 2: baseline extractor. One Claude call, structured output.

This is deliberately the simplest thing that could work, so later steps
(confidence, routing, RAG, ReAct) can be measured against it.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .ingest import SourceDoc, load_source
from .llm import EXTRACT_MODEL, get_client
from .schema import ChangeSet, Contract, ExtractionResult, FieldMeta, Intent, Status


class FieldNote(BaseModel):
    """The model's own evidence and confidence for one field."""
    path: str = Field(description='Field path, e.g. "rates[season=High,room=DBL,board=HB].price" or "release_days"')
    evidence: str = Field(description="Exact quote copied from the source text that supports the value")
    confidence: float = Field(description="0 to 1: how sure you are the value is correct")


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
changes terms of an existing contract (stop sales, offer changes, rate changes); \
"not_a_contract" otherwise. Fill only contract OR change_set to match.
- In email threads, the latest agreed terms win. Ignore counter-offers that were not accepted.
- Use these room codes: Double Standard=DBL, Double Sea View=DSV, Family Room=FAM, \
Standard Double=STD, Junior Suite=JSU. Otherwise make a short uppercase code.
- Board codes: RO, BB, HB, FB, AI. If a board price is given as a supplement, compute the full price.
- Dates are ISO (YYYY-MM-DD). If a year is missing, infer it from the contract period.
- A free child band is discount_pct 100.
- Never invent values. If something isn't stated, leave it null.
- For every important field (prices, dates, release, cancellation, offers), add a field_note \
with an exact quote from the source and your confidence.
- Return your answer by calling the record_extraction tool exactly once."""


def extract(doc: SourceDoc, model: str = EXTRACT_MODEL) -> tuple[ExtractionResult, dict]:
    client = get_client()
    # Strict structured outputs (messages.parse) rejected this schema: "compiled
    # grammar is too large". So we use a non-strict tool call and validate
    # the result with Pydantic ourselves. Validation errors are real eval failures.
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": f"Source channel: {doc.channel}\n\n<source>\n{doc.text}\n</source>"}],
        tools=[{"name": "record_extraction",
                "description": "Record the structured extraction for this source.",
                "input_schema": LLMExtraction.model_json_schema()}],
        # This model doesn't accept a forced tool_choice, so we leave it on auto
        # and tell it to call the tool. A reply without the tool call is an error.
        tool_choice={"type": "auto"},
    )
    usage = {"model": model, "input_tokens": response.usage.input_tokens,
             "output_tokens": response.usage.output_tokens, "stop_reason": response.stop_reason}
    block = next((b for b in response.content if b.type == "tool_use"), None)
    if block is None:
        raise RuntimeError(f"No structured output for {doc.path}: stop_reason={response.stop_reason}")
    out = LLMExtraction.model_validate(block.input)

    # Baseline: trust the model's own confidence. Step 3 replaces this with a
    # combined score (evidence check + validators + agreement).
    meta = {
        n.path: FieldMeta(confidence=n.confidence, evidence=n.evidence,
                          status=Status.auto_accept if n.confidence >= 0.8 else Status.needs_review)
        for n in out.field_notes
    }
    result = ExtractionResult(source_file=doc.path, intent=out.intent, contract=out.contract,
                              change_set=out.change_set, field_meta=meta)
    return result, usage


def extract_file(path: Path, model: str = EXTRACT_MODEL):
    return extract(load_source(path), model=model)
