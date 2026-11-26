"""Step 7: the whole flow, end to end.

    input file or pasted text
      -> ingest (text + channel)
      -> route: intent, then the right extraction path(s)         [steps 2-4]
      -> retrieve the existing contract for amendments           [step 5]
      -> resolver agent on fields that fail a check              [step 6]
      -> confidence and validators                               [step 3]
      -> review queue: only the fields a human must check        [step 7]
      -> approve -> new contract version -> offer file export    [step 7]
      -> corrections logged as new eval data                     [step 7]

"Replays" are saved runs, so the demo works with no API key and no cost.
"""
from __future__ import annotations

import json
from pathlib import Path

from .ingest import SourceDoc, load_source
from .rag import Store

ROOT = Path(__file__).resolve().parents[2]
REPLAYS = ROOT / "data" / "demo" / "replays"
INPUTS = ROOT / "data" / "inputs"
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}   # $ per 1M tokens in/out


def usd(calls: list[dict]) -> float:
    return round(sum((c.get("input_tokens", 0) * PRICES.get(c.get("model"), (4, 20))[0]
                      + c.get("output_tokens", 0) * PRICES.get(c.get("model"), (4, 20))[1]) / 1e6
                     for c in calls), 5)


def kind_of(routes: list[str]) -> str:
    return "contract" if "new_contract" in routes else "ignore" if routes == ["ignore"] else "amendment"


def match_hotel(store: Store, text: str) -> tuple[str | None, str]:
    hid, _, how = store.find_hotel(text)[0]
    return (hid, how) if how in ("email domain", "name match") else (None, "no confident match")


def run_live(doc: SourceDoc, model: str | None = None, passes: int = 1, store: Store | None = None) -> dict:
    """One real run through the router (costs money). Haiku and one pass by default."""
    from .llm import FAST_MODEL
    from .router import route_doc
    store = store or Store()
    routed = route_doc(doc, model=model or FAST_MODEL, store=store, passes=passes, resolve=True)
    kind = kind_of(routed.routes)
    hid, how = (routed.result.change_set.hotel_id, routed.retrieved.hotel_match if routed.retrieved else "none") \
        if kind == "amendment" and routed.result else match_hotel(store, doc.text)
    return {
        "source_file": doc.path, "channel": doc.channel, "source_text": doc.text, "kind": kind,
        "hotel_id": hid, "hotel_match": how,
        "intent": {"labels": [str(getattr(x, "value", x)) for x in routed.intent.labels],
                   "confidence": routed.intent.confidence, "reasoning": routed.intent.reasoning},
        "routes": routed.routes,
        "retrieved": routed.retrieved.model_dump() if routed.retrieved else None,
        "prediction": routed.result.model_dump(mode="json", exclude_none=True) if routed.result else None,
        "calls": routed.calls, "usd": usd(routed.calls), "seconds": routed.seconds, "mode": "live",
    }


def load_replays() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(REPLAYS.glob("*.json"))]


def build_replays() -> list[Path]:
    """Package saved eval runs (steps 4 and 5) as demo replays. Free: no model calls."""
    results = ROOT / "evals" / "results"
    routed = json.loads((results / "20261126_025553_claude-opus-5-5.json").read_text())   # step 4, routed, Opus
    rag = json.loads((results / "rag_20261126_030532_claude-haiku-5-5.json").read_text())  # step 5, with RAG, Haiku
    store = Store()
    REPLAYS.mkdir(parents=True, exist_ok=True)
    written = []
    picks = [(r, r["model"]) for r in routed["rows"] if not r["file"].startswith("003")]
    run = next(x for x in rag["runs"] if x["condition"] == "with RAG")
    chat = next(r for r in routed["rows"] if r["file"].startswith("003"))
    picks.append(({**chat, **run, "file": chat["file"], "intent_labels": chat["intent_labels"]}, rag["model"]))
    for row, model in picks:
        doc = load_source(INPUTS / row["file"])
        kind = kind_of(row["routes"])
        if kind == "amendment":
            hid, how = row["prediction"]["change_set"].get("hotel_id"), row["retrieved"]["hotel_match"]
        else:
            hid, how = match_hotel(store, doc.text)
        payload = {
            "source_file": doc.path, "channel": doc.channel, "source_text": doc.text, "kind": kind,
            "hotel_id": hid, "hotel_match": how,
            "intent": {"labels": row["intent_labels"], "confidence": None, "reasoning": None},
            "routes": row["routes"], "retrieved": row.get("retrieved"), "prediction": row["prediction"],
            "calls": row["calls"], "usd": usd(row["calls"]), "seconds": row.get("seconds"),
            "mode": "replay", "model": model,
        }
        out = REPLAYS / (Path(row["file"]).stem + ".json")
        out.write_text(json.dumps(payload, indent=1, ensure_ascii=False, default=str))
        written.append(out)
    return written


if __name__ == "__main__":
    for p in build_replays():
        print("wrote", p.relative_to(ROOT))
