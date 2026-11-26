"""Step 3 checks that need no API key: evidence, validators, confidence, scoring fixes."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.confidence import THRESHOLDS, category, combine  # noqa: E402
from contract_agent.evidence import quote_in_source, value_in_quote  # noqa: E402
from contract_agent.extract import FieldNote, LLMExtraction  # noqa: E402
from contract_agent.ingest import load_source  # noqa: E402
from contract_agent.pipeline import build_meta, find_note, note_index  # noqa: E402
from contract_agent.schema import Contract, GoldLabel, Intent  # noqa: E402
from contract_agent.scoring import canonical_season, score_fields  # noqa: E402
from contract_agent.validators import validate  # noqa: E402

G1 = ROOT / "data/gold/001_sol_de_marina_contract.json"
G2 = ROOT / "data/gold/002_aegean_breeze_email.json"
G3 = ROOT / "data/gold/003_sol_de_marina_chat.json"


def gold(p):
    return GoldLabel.model_validate_json(p.read_text())


# ---------- scoring fixes ----------

def test_season_labels_match():
    assert canonical_season("Season A") == canonical_season("A") == "A"
    assert canonical_season("high season") == "High"
    d = json.loads(G2.read_text())["contract"]
    for s in d["seasons"]:
        s["name"] = "Season " + s["name"]
    for r in d["rates"]:
        r["season"] = "Season " + r["season"]
    s = score_fields(Contract.model_validate(d), gold(G2).contract)
    assert s.f1 == 1.0, (s.missing[:3], s.extra[:3])


def test_offer_change_without_dates_is_wrong_not_missing():
    g = gold(G3).change_set
    pred = g.model_copy(deep=True)
    pred.changes[1].offer.stay_from = None           # model can't know it without the contract
    s = score_fields(pred, g)
    assert s.extra == [] and s.missing == ["changes[offer_changed|early_booking].offer.stay_from"]


# ---------- evidence ----------

def test_quotes_checked_against_source():
    pdf = load_source(ROOT / "data/inputs/001_sol_de_marina_contract.pdf").text
    assert quote_in_source("Double Standard – BB 95 120 155 105", pdf)
    assert quote_in_source("Release period: 14 days prior to arrival", pdf)
    assert quote_in_source("Release period 14 days prior to arival", pdf)      # small slips are OK
    assert not quote_in_source("Release period: 21 days prior to arrival", pdf)  # a changed number is not
    assert not quote_in_source("", pdf)


def test_value_in_quote():
    assert value_in_quote(155, "Double Standard – BB 95 120 155 105")
    assert not value_in_quote(15, "Double Standard – BB 95 120 155 105")
    assert value_in_quote(2.2, "EUR 2.20 per person per night")
    assert value_in_quote("2027-08-10", "sea view rooms 10 to 17 August")
    assert value_in_quote("2027-05-01", "Low: 01/05 – 14/06")
    assert value_in_quote("HB", "half board") is None          # not checkable


# ---------- validators ----------

def test_gold_labels_pass_every_error_rule():
    for p in (G1, G2, G3):
        g = gold(p)
        errors = [i for i in validate(g.contract, g.change_set) if i.severity == "error"]
        assert errors == [], (p.name, errors)


def test_validators_catch_planted_errors():
    d = json.loads(G1.read_text())["contract"]
    d["cancellation"][2]["charge_pct"] = 30            # charge falls closer to arrival
    d["rates"][0]["price"] = 950                       # typo: 95 -> 950
    d["rooms"][0]["inventory"] = "free_sale"           # but still has an allotment count
    rules = {i.rule for i in validate(Contract.model_validate(d), None)}
    assert {"cancel_charge_falls", "price_outlier", "allotment_on_free_sale"} <= rules


# ---------- confidence ----------

def test_categories_and_thresholds():
    assert category("cancellation[15].charge_pct") == "cancellation"
    assert category("rates[High|DBL|HB].price") == "price"
    assert category("seasons[Low].date_from") == "dates"
    assert category("rates[High|DBL|HB].board") == "board"
    assert category("rooms[DBL].max_adults") == "other"
    assert THRESHOLDS["cancellation"] > THRESHOLDS["price"] > THRESHOLDS["dates"] > THRESHOLDS["board"]


def test_red_flags_cap_the_score():
    ok = dict(has_evidence=True, evidence_found=True, value_in_quote=True, derived=False,
              validator_error=False, agrees=True)
    assert combine(0.97, **ok)[0] == 0.97
    assert combine(0.97, **{**ok, "evidence_found": False})[0] == 0.40
    assert combine(0.97, **{**ok, "validator_error": True})[0] == 0.30
    assert combine(0.97, **{**ok, "agrees": False})[0] == 0.35
    assert combine(0.97, **{**ok, "value_in_quote": False, "derived": True})[0] == 0.97


def test_notes_cover_sub_fields_by_prefix():
    idx = note_index([FieldNote(paths=["rates[Season High|DBL|HB]"], evidence="x", confidence=0.9),
                      FieldNote(paths=["rates[High|DBL|HB].price"], evidence="y", confidence=0.8)])
    assert find_note("rates[High|DBL|HB].price", idx).evidence == "y"
    assert find_note("rates[High|DBL|HB].board", idx).evidence == "x"
    assert find_note("rates[High|DBL|BB].board", idx) is None


def test_meta_end_to_end_on_gold():
    """Gold contract + one honest note per rate row: prices auto-accept, a fake quote doesn't."""
    g = gold(G1)
    src = load_source(ROOT / "data/inputs" / g.source_file).text
    notes = [FieldNote(paths=["rates[High|DBL|BB]"], evidence="Double Standard – BB 95 120 155 105", confidence=0.97),
             FieldNote(paths=["rates[Low|DBL|BB]"], evidence="Double Standard – BB 59 120", confidence=0.97)]
    a = LLMExtraction(intent=Intent.new_contract, contract=g.contract, field_notes=notes)
    meta = build_meta(src, a, a, validate(g.contract, None))
    assert meta["rates[High|DBL|BB].price"].status == "auto_accept"
    low = meta["rates[Low|DBL|BB].price"]
    assert low.status == "needs_review" and "quote not found in the source" in low.reasons
    assert meta["release_days"].reasons == ["no supporting quote"]
