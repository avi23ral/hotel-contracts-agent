"""Step 4 checks that need no API key: intent set, house rules, router plumbing."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.extract import LLMExtraction  # noqa: E402
from contract_agent.ingest import SourceDoc  # noqa: E402
from contract_agent.intent import IntentResult, Label, _tidy, classify_rules  # noqa: E402
from contract_agent.pipeline import keep_types  # noqa: E402
from contract_agent.router import merge  # noqa: E402
from contract_agent.schema import (ARI_CHANGES, PROMO_CHANGES, ChangeSet, ExtractionResult,  # noqa: E402
                                   GoldLabel, Intent)

MSGS = [json.loads(x) for x in (ROOT / "data/intents/messages.jsonl").read_text().splitlines()]


def test_intent_set_is_well_formed():
    ids = [m["id"] for m in MSGS]
    assert len(ids) == len(set(ids)) >= 40
    for m in MSGS:
        assert m["labels"] and all(lab in Label.__members__ for lab in m["labels"]), m["id"]
        assert ("text" in m) != ("file" in m), m["id"]
        if "not_a_contract" in m["labels"] or "new_contract" in m["labels"]:
            assert len(m["labels"]) == 1, m["id"]
    ari = sum("ari_update" in m["labels"] for m in MSGS)
    promo = sum("promotion" in m["labels"] for m in MSGS)
    assert ari > promo   # research: rate and availability updates dominate traffic


def test_house_rules_on_labels():
    t = lambda labels: [x.value for x in _tidy(IntentResult(reasoning="", labels=labels, confidence=1)).labels]  # noqa: E731
    assert t([Label.not_a_contract, Label.ari_update]) == ["ari_update"]
    assert t([Label.new_contract, Label.promotion]) == ["new_contract"]
    assert t([]) == ["not_a_contract"]


def test_rules_baseline_catches_the_obvious():
    assert classify_rules("Please stop sale all rooms 3-6 June").labels == [Label.ari_update]
    assert classify_rules("Stay 7 pay 6 in October").labels == [Label.promotion]
    assert classify_rules("Invoice INV-1 attached").labels == [Label.not_a_contract]


def test_paths_only_keep_their_own_change_types():
    g = GoldLabel.model_validate_json((ROOT / "data/gold/003_sol_de_marina_chat.json").read_text())
    a = LLMExtraction(intent=Intent.amendment, change_set=g.change_set.model_copy(deep=True))
    assert [c.change_type for c in keep_types(a, ARI_CHANGES).change_set.changes] == ["stop_sale"]
    a = LLMExtraction(intent=Intent.amendment, change_set=g.change_set.model_copy(deep=True))
    assert {c.change_type for c in keep_types(a, PROMO_CHANGES).change_set.changes} == {"offer_changed", "offer_added"}


def test_merge_rebuilds_the_full_change_set():
    g = GoldLabel.model_validate_json((ROOT / "data/gold/003_sol_de_marina_chat.json").read_text())
    ari = ChangeSet(hotel_name=g.change_set.hotel_name, changes=g.change_set.changes[:1])
    promo = ChangeSet(hotel_name=g.change_set.hotel_name, changes=g.change_set.changes[1:])
    doc = SourceDoc(path="x", channel="chat", text="")
    parts = [ExtractionResult(source_file="x", intent=Intent.amendment, change_set=cs) for cs in (ari, promo)]
    assert merge(doc, parts).change_set == g.change_set
