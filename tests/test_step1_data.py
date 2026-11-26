"""Step 1 checks: gold labels are valid and grounded in the inputs."""
import json
import sys
from pathlib import Path

import pdfplumber
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.offers import to_offer_rows  # noqa: E402
from contract_agent.schema import Contract, GoldLabel  # noqa: E402

GOLD = sorted((ROOT / "data" / "gold").glob("*.json"))


def source_text(name: str) -> str:
    p = ROOT / "data" / "inputs" / name
    if p.suffix == ".pdf":
        with pdfplumber.open(p) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    return p.read_text()


@pytest.mark.parametrize("path", GOLD, ids=lambda p: p.stem)
def test_gold_is_valid_schema(path):
    GoldLabel.model_validate_json(path.read_text())


def test_every_input_has_gold():
    inputs = {p.stem for p in (ROOT / "data" / "inputs").iterdir()}
    assert inputs == {p.stem for p in GOLD}


@pytest.mark.parametrize("path", GOLD, ids=lambda p: p.stem)
def test_hotel_name_appears_in_source(path):
    g = GoldLabel.model_validate_json(path.read_text())
    name = g.contract.hotel.name if g.contract else g.change_set.hotel_name
    assert name.split()[-1].lower() in source_text(g.source_file).lower()


def test_pdf_printed_prices_appear_in_text():
    """Grounding check: every BB/HB price in gold must be visible in the PDF.
    AI prices are derived, so they are deliberately NOT in the text."""
    g = GoldLabel.model_validate_json((ROOT / "data/gold/001_sol_de_marina_contract.json").read_text())
    text = source_text(g.source_file)
    for r in g.contract.rates:
        if r.board != "AI":
            assert f"{r.price:g}" in text, r


def test_schema_rejects_overlapping_seasons():
    g = json.loads((ROOT / "data/gold/001_sol_de_marina_contract.json").read_text())
    g["contract"]["seasons"][1]["date_from"] = "2027-06-10"   # overlaps Low
    with pytest.raises(ValueError, match="overlap"):
        Contract.model_validate(g["contract"])


def test_offer_rows():
    g = GoldLabel.model_validate_json((ROOT / "data/gold/001_sol_de_marina_contract.json").read_text())
    rows = to_offer_rows(g.contract)
    # 3 rooms x 3 boards x 4 seasons = 36 rates; adults: DBL 2, DSV 2, FAM 3 options
    assert len(rows) == 4 * 3 * (2 + 2 + 3)
    g2 = GoldLabel.model_validate_json((ROOT / "data/gold/002_aegean_breeze_email.json").read_text())
    std_b_hb_2ad = next(r for r in to_offer_rows(g2.contract)
                        if r.room_code == "STD" and r.board == "HB" and r.adults == 2
                        and r.date_from == "2027-07-01")
    assert std_b_hb_2ad.price_per_night == 168  # 84 pp x 2 adults
