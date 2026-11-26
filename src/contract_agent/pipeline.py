"""Step 3 pipeline: extract twice, normalise, validate, check evidence, score.

    source text
      -> two independent extraction passes (run in parallel)
      -> normalise pass A (house conventions, no model)
      -> validator rules on pass A             (validators.py)
      -> for each field of pass A:
           find its evidence note, check the quote is in the source,
           check the value is in the quote    (evidence.py)
           compare with pass B                 (self-consistency)
           combine into one score + status    (confidence.py)

Pass A is the answer. Pass B is only a second opinion, used to spot
fields the model is unstable on. That doubles the cost of extraction;
the eval reports tokens so the trade-off is visible.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from .confidence import THRESHOLDS, category, combine
from .evidence import quote_in_source, value_in_quote
from .extract import FieldNote, LLMExtraction, baseline_status, call_model
from .ingest import SourceDoc
from .llm import EXTRACT_MODEL
from .schema import ExtractionResult, FieldMeta, Status
from .scoring import _SEASON_WORD, _equal, canonical_season, flatten
from .validators import validate


# ---------- normalise (deterministic house conventions) ----------

def normalise(out: LLMExtraction) -> LLMExtraction:
    c = out.contract
    if c:
        for s in c.seasons:
            s.name = canonical_season(s.name)
        for r in c.rates:
            r.season = canonical_season(r.season)
        all_boards = {r.board for r in c.rates}
        for o in c.special_offers:      # "all boards" listed out == empty list
            if all_boards and set(o.boards) >= all_boards:
                o.boards = []
    if out.change_set:
        for ch in out.change_set.changes:
            # For offer changes the board scope lives on the offer itself, so a
            # copy on the change is a duplicate (the schema has two places for it).
            if ch.offer and ch.boards and set(ch.boards) == set(ch.offer.boards or ch.boards):
                ch.offer.boards = ch.offer.boards or list(ch.boards)
                ch.boards = []
    return out


def target(out: LLMExtraction):
    return out.contract or out.change_set


# ---------- match evidence notes to fields ----------

def _canon_path(path: str) -> str:
    def fix(m):
        return "[" + "|".join(_SEASON_WORD.sub("", part).strip() for part in m.group(1).split("|")) + "]"
    return re.sub(r"\[([^\]]*)\]", fix, path.strip()).lower()


def note_index(notes: list[FieldNote]) -> dict[str, FieldNote]:
    idx = {}
    for n in notes:
        for p in n.paths:
            idx[_canon_path(p)] = n
    return idx


def find_note(leaf: str, idx: dict[str, FieldNote]) -> FieldNote | None:
    """Longest note path that equals the leaf or is a prefix of it."""
    lp = leaf.lower()
    best = None
    for p, n in idx.items():
        if lp == p or (lp.startswith(p) and lp[len(p)] in ".["):
            if best is None or len(p) > len(best[0]):
                best = (p, n)
    return best[1] if best else None


def _validator_hits(leaf: str, issues) -> list[str]:
    return [i.rule for i in issues if i.severity == "error"
            and any(leaf == p or leaf.startswith(p + ".") or leaf.startswith(p + "[") for p in i.paths)]


# ---------- the pipeline ----------

def build_meta(source_text: str, a: LLMExtraction, b: LLMExtraction | None, issues):
    leaves = flatten(target(a)) if target(a) else {}
    other = flatten(target(b)) if b is not None and target(b) and b.intent == a.intent else {}
    idx = note_index(a.field_notes)
    meta: dict[str, FieldMeta] = {}
    for leaf, value in leaves.items():
        note = find_note(leaf, idx)
        ev = note.evidence if note else None
        found = quote_in_source(ev, source_text) if note else None
        viq = value_in_quote(value, ev) if note else None
        derived = bool(note and note.derived)
        flags = _validator_hits(leaf, issues)
        if b is None:
            agrees = True               # second pass failed: no signal either way
        else:
            agrees = _equal(other[leaf], value) if leaf in other else None
        score, reasons = combine(note.confidence if note else None, has_evidence=note is not None,
                                 evidence_found=found, value_in_quote=viq, derived=derived,
                                 validator_error=bool(flags), agrees=agrees)
        cat = category(leaf)
        meta[leaf] = FieldMeta(
            confidence=score, evidence=ev, category=cat, threshold=THRESHOLDS[cat],
            status=Status.auto_accept if score >= THRESHOLDS[cat] else Status.needs_review,
            model_confidence=note.confidence if note else None, evidence_found=found,
            value_in_quote=viq, derived=derived, agrees=agrees if b is not None else None,
            flags=flags, reasons=reasons)
    return meta


def run_pipeline(doc: SourceDoc, model: str = EXTRACT_MODEL, passes: int = 2):
    with ThreadPoolExecutor(max_workers=passes) as pool:
        futures = [pool.submit(call_model, doc, model) for _ in range(passes)]
        results = []
        for f in futures:
            try:
                results.append(f.result())
            except Exception as e:      # pass B failing shouldn't sink pass A
                results.append(e)
    if isinstance(results[0], Exception):
        raise results[0]
    a, usage_a = results[0]
    b, usage_b = (results[1] if len(results) > 1 and not isinstance(results[1], Exception) else (None, {}))
    a = normalise(a)
    if b is not None:
        b = normalise(b)
    issues = validate(a.contract, a.change_set)
    meta = build_meta(doc.text, a, b, issues)
    result = ExtractionResult(source_file=doc.path, intent=a.intent, contract=a.contract,
                              change_set=a.change_set, field_meta=meta, issues=issues)
    usage = {"model": model, "passes": len(results),
             "second_pass_ok": b is not None,
             "second_pass_intent": b.intent.value if b is not None else None,
             "input_tokens": usage_a["input_tokens"] + usage_b.get("input_tokens", 0),
             "output_tokens": usage_a["output_tokens"] + usage_b.get("output_tokens", 0),
             "stop_reason": usage_a["stop_reason"]}
    return result, usage


def baseline_meta_status(meta: FieldMeta) -> Status:
    return baseline_status(meta.model_confidence)
