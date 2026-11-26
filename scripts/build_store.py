"""Build the step 5 knowledge store: a property master and the contracts we
already have loaded, one JSON file per hotel.

Two of them are the gold contracts from step 1 (001 and 002). The other seven
are small synthetic contracts for the hotels that appear in the intent eval
messages, plus one decoy ("Hotel Marina Sol") whose name is deliberately close
to "Hotel Sol de Marina", to test that retrieval doesn't confuse them.
All hotels, ids, people and prices are fictional.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.schema import Contract, GoldLabel  # noqa: E402

STORE = ROOT / "data" / "store"

# property master: our internal id (system of record) + a fake GIATA-style id
HOTELS = [
    {"hotel_id": "H001", "giata_id": "G-100231", "name": "Hotel Sol de Marina", "aliases": ["Sol de Marina"],
     "destination": "Alcúdia, Mallorca", "country": "Spain", "email_domain": "soldemarina.example",
     "contacts": ["Marta Ferrer", "M. Ferrer"]},
    {"hotel_id": "H002", "giata_id": "G-200877", "name": "Aegean Breeze Resort", "aliases": ["Aegean Breeze"],
     "destination": "Kos", "country": "Greece", "email_domain": "aegeanbreeze.example",
     "contacts": ["Eleni Papadaki", "Kostas"]},
    {"hotel_id": "H003", "giata_id": "G-200912", "name": "Villa Thalassa", "aliases": [],
     "destination": "Chania, Crete", "country": "Greece", "email_domain": "villathalassa.example",
     "contacts": ["Nikos"]},
    {"hotel_id": "H004", "giata_id": "G-100877", "name": "Hotel Mirador", "aliases": ["Mirador"],
     "destination": "Puerto de la Cruz, Tenerife", "country": "Spain", "email_domain": "hotelmirador.example",
     "contacts": ["Ana"]},
    {"hotel_id": "H005", "giata_id": "G-300114", "name": "Costa Azul Beach", "aliases": ["Costa Azul"],
     "destination": "Albufeira, Algarve", "country": "Portugal", "email_domain": "costaazulbeach.example",
     "contacts": []},
    {"hotel_id": "H006", "giata_id": "G-400556", "name": "Hotel Riviera Blu", "aliases": ["Riviera Blu"],
     "destination": "Sorrento", "country": "Italy", "email_domain": "rivierablu.example",
     "contacts": ["Paolo", "Giulia"]},
    {"hotel_id": "H007", "giata_id": "G-500301", "name": "Alpenhof Lodge", "aliases": ["Alpenhof"],
     "destination": "Mayrhofen, Tyrol", "country": "Austria", "email_domain": "alpenhof.example",
     "contacts": []},
    {"hotel_id": "H008", "giata_id": "G-200455", "name": "Hotel Olivia", "aliases": ["Olivia"],
     "destination": "Corfu", "country": "Greece", "email_domain": "olivia.example",
     "contacts": ["Irene"]},
    {"hotel_id": "H009", "giata_id": "G-100990", "name": "Hotel Marina Sol", "aliases": ["Marina Sol"],
     "destination": "Benidorm", "country": "Spain", "email_domain": "marinasol.example",
     "contacts": ["Jordi Puig"]},
]

SUMMER = [("Low", "2027-05-01", "2027-06-14", 3), ("Mid", "2027-06-15", "2027-07-14", 3),
          ("High", "2027-07-15", "2027-08-31", 5), ("Shoulder", "2027-09-01", "2027-10-31", 3)]
CANCEL = [{"days_from": 31, "charge_pct": 0}, {"days_from": 15, "days_to": 30, "charge_pct": 50},
          {"days_from": 0, "days_to": 14, "charge_pct": 100}]


def simple(h, rooms, base, boards=("BB", "HB"), seasons=SUMMER, offers=(), inventory="free_sale",
           release=None, start="2027-05-01", end="2027-10-31", cancel=CANCEL):
    """A compact contract: rate = base[room] x season factor, + board step."""
    factor = [1.0, 1.25, 1.6, 1.1, 1.0][:len(seasons)]
    step = {"RO": 0, "BB": 0, "HB": 30, "FB": 50, "AI": 70}
    rates = [{"season": s[0], "room_code": code, "board": b, "price": round(base[code] * f + step[b])}
             for s, f in zip(seasons, factor) for code in base for b in boards]
    return {
        "hotel": {"name": h["name"], "destination": h["destination"], "country": h["country"],
                  "supplier_email": f"contracts@{h['email_domain']}", "hotel_id": h["hotel_id"],
                  "giata_id": h["giata_id"]},
        "contract_start": start, "contract_end": end, "currency": "EUR", "rate_type": "net",
        "pricing_basis": "per_room_per_night",
        "rooms": [{"code": c, "name": n, "min_adults": 1, "max_adults": mx, "max_children": ch,
                   "max_occupancy": mx + ch, "inventory": inventory,
                   **({"allotment": 4} if inventory == "allotment" else {})} for c, n, mx, ch in rooms],
        "seasons": [{"name": n, "date_from": a, "date_to": b, "min_stay_nights": m} for n, a, b, m in seasons],
        "rates": rates, "release_days": release, "cancellation": cancel, "special_offers": list(offers),
        "child_policy": [{"age_from": 0, "age_to": 1.99, "discount_pct": 100},
                         {"age_from": 2, "age_to": 11.99, "discount_pct": 50}],
    }


def main():
    STORE.mkdir(parents=True, exist_ok=True)
    (STORE / "hotels.json").write_text(json.dumps(HOTELS, indent=2, ensure_ascii=False))
    by_id = {h["hotel_id"]: h for h in HOTELS}
    contracts = {}
    for hid, gold in (("H001", "001_sol_de_marina_contract"), ("H002", "002_aegean_breeze_email")):
        c = GoldLabel.model_validate_json((ROOT / "data/gold" / f"{gold}.json").read_text()).contract
        c.hotel.hotel_id, c.hotel.giata_id = hid, by_id[hid]["giata_id"]
        contracts[hid] = c.model_dump(mode="json", exclude_none=True)
    contracts["H003"] = simple(by_id["H003"], [("DBL", "Double Room", 2, 1), ("SUI", "Garden Suite", 3, 1)],
                               {"DBL": 90, "SUI": 150})
    contracts["H004"] = simple(by_id["H004"], [("DBL", "Double Room", 2, 1), ("DSV", "Double Sea View", 2, 1)],
                               {"DBL": 80, "DSV": 95}, boards=("BB", "HB", "AI"))
    contracts["H005"] = simple(by_id["H005"], [("STD", "Standard Room", 2, 1), ("JSU", "Junior Suite", 2, 2),
                                               ("FAM", "Family Room", 2, 2)],
                               {"STD": 85, "JSU": 140, "FAM": 160}, boards=("HB", "AI"))
    contracts["H006"] = simple(by_id["H006"], [("DBL", "Double Room", 2, 0), ("DSV", "Sea View Room", 2, 1)],
                               {"DBL": 120, "DSV": 150}, inventory="allotment", release=14)
    contracts["H007"] = simple(by_id["H007"], [("DBL", "Double Room", 2, 1), ("JSU", "Junior Suite", 2, 2)],
                               {"DBL": 150, "JSU": 210}, boards=("HB",),
                               seasons=[("Xmas", "2027-12-15", "2028-01-06", 7), ("Peak", "2028-01-07", "2028-02-29", 7),
                                        ("Mid", "2028-03-01", "2028-03-25", 4), ("Low", "2028-03-26", "2028-04-10", 3)],
                               start="2027-12-15", end="2028-04-10",
                               cancel=[{"days_from": 30, "charge_pct": 0},
                                       {"days_from": 8, "days_to": 29, "charge_pct": 50},
                                       {"days_from": 0, "days_to": 7, "charge_pct": 100}])
    contracts["H008"] = simple(by_id["H008"], [("DBL", "Double Room", 2, 1), ("FAM", "Family Room", 2, 2)],
                               {"DBL": 75, "FAM": 120}, inventory="allotment", release=14,
                               offers=[{"type": "early_booking", "discount_pct": 12, "book_by": "2027-02-28",
                                        "stay_from": "2027-05-01", "stay_to": "2027-10-31", "combinable": False}])
    contracts["H009"] = simple(by_id["H009"], [("DBL", "Double Room", 2, 1), ("DSV", "Double Sea View", 2, 1)],
                               {"DBL": 70, "DSV": 85},
                               offers=[{"type": "early_booking", "discount_pct": 15, "book_by": "2027-01-31",
                                        "stay_from": "2027-05-01", "stay_to": "2027-10-31", "combinable": True}])
    for hid, c in contracts.items():
        Contract.model_validate(c)   # fails loudly if a contract breaks the schema
        (STORE / f"contract_{hid}.json").write_text(json.dumps(c, indent=2, ensure_ascii=False))
    print(f"wrote hotels.json and {len(contracts)} contracts to {STORE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
