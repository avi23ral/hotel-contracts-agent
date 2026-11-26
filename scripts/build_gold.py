"""Write the hand-labelled gold answers for the 3 synthetic inputs.

Written as Python (not raw JSON) so derived values are explicit:
e.g. All Inclusive = HB + supplement. Reading this file is the fastest way
to see what "correct" means for each input.

Labelling conventions (these will later live in the RAG playbook):
- "Free" child = discount_pct 100.
- Room codes are OUR codes (DBL, DSV, FAM, STD, JSU), not the hotel's names.
- Year is inferred from the contract period when the source omits it.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from contract_agent.schema import GoldLabel  # noqa: E402

GOLD = Path(__file__).resolve().parents[1] / "data" / "gold"
SEASONS = ["Low", "Mid", "High", "Shoulder"]


def rates(table: dict[tuple[str, str], list[float]], seasons: list[str]):
    return [{"season": s, "room_code": room, "board": board, "price": p}
            for (room, board), prices in table.items() for s, p in zip(seasons, prices)]


# ---------- 001: clean-ish PDF, new contract ----------
bb_hb = {
    ("DBL", "BB"): [95, 120, 155, 105], ("DBL", "HB"): [125, 150, 185, 135],
    ("DSV", "BB"): [110, 135, 170, 120], ("DSV", "HB"): [140, 165, 200, 150],
    ("FAM", "BB"): [150, 185, 240, 165], ("FAM", "HB"): [200, 235, 290, 215],
}
ai_supp = {"DBL": 70, "DSV": 70, "FAM": 110}   # footnote: AI = HB + supplement
for room, supp in ai_supp.items():
    bb_hb[(room, "AI")] = [p + supp for p in bb_hb[(room, "HB")]]

g001 = {
    "source_file": "001_sol_de_marina_contract.pdf", "channel": "pdf", "difficulty": "easy",
    "intent": "new_contract",
    "tests_what": ["rate table reading", "derived AI rates from footnote", "per-season min stay",
                   "two-page layout", "year inference for seasons"],
    "contract": {
        "hotel": {"name": "Hotel Sol de Marina", "destination": "Alcúdia, Mallorca", "country": "Spain",
                  "supplier_email": "contratos@soldemarina.example"},
        "contract_start": "2027-05-01", "contract_end": "2027-10-31",
        "currency": "EUR", "rate_type": "net", "commission_pct": None,
        "pricing_basis": "per_room_per_night",
        "rooms": [
            {"code": "DBL", "name": "Double Standard", "min_adults": 1, "max_adults": 2, "max_children": 1,
             "max_occupancy": 3, "allotment": 10},
            {"code": "DSV", "name": "Double Sea View", "min_adults": 1, "max_adults": 2, "max_children": 1,
             "max_occupancy": 3, "allotment": 4},
            {"code": "FAM", "name": "Family Room", "min_adults": 2, "max_adults": 4, "max_children": 2,
             "max_occupancy": 4, "allotment": 6},
        ],
        "seasons": [
            {"name": "Low", "date_from": "2027-05-01", "date_to": "2027-06-14", "min_stay_nights": 3},
            {"name": "Mid", "date_from": "2027-06-15", "date_to": "2027-07-14", "min_stay_nights": 3},
            {"name": "High", "date_from": "2027-07-15", "date_to": "2027-08-31", "min_stay_nights": 5},
            {"name": "Shoulder", "date_from": "2027-09-01", "date_to": "2027-10-31", "min_stay_nights": 3},
        ],
        "rates": rates(bb_hb, SEASONS),
        "child_policy": [
            {"age_from": 0, "age_to": 1.99, "discount_pct": 100},
            {"age_from": 2, "age_to": 11.99, "fixed_price": 25},
            {"age_from": 12, "age_to": 17.99, "fixed_price": 45},
        ],
        "release_days": 14,
        "cancellation": [
            {"days_from": 31, "days_to": None, "charge_pct": 0},
            {"days_from": 15, "days_to": 30, "charge_pct": 50},
            {"days_from": 0, "days_to": 14, "charge_pct": 100},
        ],
        "special_offers": [
            {"type": "early_booking", "discount_pct": 10, "book_by": "2027-01-31",
             "stay_from": "2027-05-01", "stay_to": "2027-10-31", "combinable": False},
        ],
        "local_tax": {"included_in_rate": False, "amount": 2.20, "basis": "per_person_per_night"},
        "payment_terms": "Bank transfer within 30 days of guest departure",
    },
}

# ---------- 002: email negotiation thread, new contract ----------
# Final = first proposal, overridden by the latest email (Season B HB + release).
hb = {("STD", "HB"): [62, 84, 66], ("JSU", "HB"): [92, 112, 96]}
for room in ("STD", "JSU"):
    hb[(room, "AI")] = [p + 25 for p in hb[(room, "HB")]]

g002 = {
    "source_file": "002_aegean_breeze_email.eml", "channel": "email", "difficulty": "medium",
    "intent": "new_contract",
    "tests_what": ["latest-message-wins in a thread", "ignore buyer's counter-offer (80/110)",
                   "per person pricing", "commissionable rate", "derived AI rates",
                   "dates without year"],
    "contract": {
        "hotel": {"name": "Aegean Breeze Resort", "destination": "Kos", "country": "Greece",
                  "supplier_email": "sales@aegeanbreeze.example"},
        "contract_start": "2027-05-15", "contract_end": "2027-10-15",
        "currency": "EUR", "rate_type": "commissionable", "commission_pct": 18,
        "pricing_basis": "per_person_per_night",
        "rooms": [
            {"code": "STD", "name": "Standard Double", "min_adults": 2, "max_adults": 3, "max_children": 1,
             "max_occupancy": 3, "allotment": 8},
            {"code": "JSU", "name": "Junior Suite", "min_adults": 2, "max_adults": 3, "max_children": 2,
             "max_occupancy": 4, "allotment": 3},
        ],
        "seasons": [
            {"name": "A", "date_from": "2027-05-15", "date_to": "2027-06-30", "min_stay_nights": 3},
            {"name": "B", "date_from": "2027-07-01", "date_to": "2027-08-31", "min_stay_nights": 5},
            {"name": "C", "date_from": "2027-09-01", "date_to": "2027-10-15", "min_stay_nights": 3},
        ],
        "rates": rates(hb, ["A", "B", "C"]),
        "child_policy": [
            {"age_from": 0, "age_to": 5.99, "discount_pct": 100},
            {"age_from": 6, "age_to": 13.99, "discount_pct": 50},
        ],
        "release_days": 10,
        "cancellation": [
            {"days_from": 21, "days_to": None, "charge_pct": 0},
            {"days_from": 8, "days_to": 20, "charge_pct": 30},
            {"days_from": 0, "days_to": 7, "charge_pct": 100},
        ],
        "special_offers": [
            {"type": "free_nights", "stay_nights": 7, "pay_nights": 6,
             "stay_from": "2027-05-15", "stay_to": "2027-06-30", "combinable": False},
            {"type": "free_nights", "stay_nights": 7, "pay_nights": 6,
             "stay_from": "2027-09-01", "stay_to": "2027-10-15", "combinable": False},
        ],
        "local_tax": {"included_in_rate": False, "amount": 4.00, "basis": "per_room_per_night"},
        "payment_terms": "50% deposit 30 days before arrival, balance 7 days after check-out",
    },
}

# ---------- 003: WhatsApp chat, amendment to contract 001 ----------
g003 = {
    "source_file": "003_sol_de_marina_chat.txt", "channel": "chat", "difficulty": "hard",
    "intent": "amendment",
    "tests_what": ["intent = amendment, not new contract", "three changes in one chat",
                   "clarifying Q&A narrows scope (sea view only)", "needs existing contract (RAG) to map rooms",
                   "ignore small talk"],
    "change_set": {
        "hotel_name": "Hotel Sol de Marina",
        "changes": [
            {"change_type": "stop_sale", "room_codes": ["DSV"],
             "date_from": "2027-08-10", "date_to": "2027-08-17", "note": "wedding group"},
            {"change_type": "offer_changed",
             "offer": {"type": "early_booking", "discount_pct": 10, "book_by": "2027-02-28",
                       "stay_from": "2027-05-01", "stay_to": "2027-10-31", "combinable": False},
             "note": "book-by extended from 31/01 to 28/02, same conditions"},
            {"change_type": "offer_added",
             "offer": {"type": "discount", "discount_pct": 15, "book_from": "2027-09-01",
                       "stay_from": "2027-10-01", "stay_to": "2027-10-31",
                       "boards": ["HB"], "combinable": False},
             "note": "book_from is ambiguous: 1 Sept 2027 assumed (offer is for Oct 2027 stays)"},
        ],
    },
}

GOLD.mkdir(parents=True, exist_ok=True)
for g in (g001, g002, g003):
    label = GoldLabel.model_validate(g)  # fails loudly if the label breaks the schema
    path = GOLD / (Path(label.source_file).stem + ".json")
    path.write_text(json.dumps(label.model_dump(mode="json", exclude_none=True), indent=2, ensure_ascii=False))
    print(f"wrote {path.name}")
