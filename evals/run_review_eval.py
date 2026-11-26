"""Step 7 eval: the whole loop with a simulated reviewer. Free: no model calls.

    python evals/run_review_eval.py

Takes the saved runs (data/demo/replays), puts each through the review queue,
and plays the reviewer using the gold labels: a queued field that matches
gold is accepted, anything else is corrected to the gold value. Then it
approves, applies the chat amendment to the contract, exports offer files,
and measures what a customer would see:

- fields right, before review (auto-accept everything) vs after review
- offer rows exactly right, before vs after
- review time (30 s per queued field, the build plan assumption)
- what still slipped through (wrong fields that were auto-accepted)
- the correction log (the data flywheel)
"""
from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from contract_agent.apply import apply_changes  # noqa: E402
from contract_agent.export import export_hotel  # noqa: E402
from contract_agent.offers import to_offer_rows  # noqa: E402
from contract_agent.orchestrate import load_replays  # noqa: E402
from contract_agent.review import ReviewStore, get_path  # noqa: E402
from contract_agent.schema import ChangeSet, Contract, GoldLabel  # noqa: E402
from contract_agent.scoring import _equal, flatten, score_fields  # noqa: E402

SECONDS_PER_FIELD = 30
MANUAL_MINUTES_PER_CONTRACT = 180      # build plan assumption: about 3 hours of keying per contract
GOLD = ROOT / "data" / "gold"
OUT = ROOT / "evals" / "results" / "review_eval.json"
EXPORT = ROOT / "data" / "demo" / "export"
FLYWHEEL = ROOT / "data" / "flywheel" / "corrections.jsonl"
ROW_KEY = ("room_code", "board", "adults", "date_from", "date_to")
ROW_VALUES = ("min_stay_nights", "price_per_night", "currency", "rate_type", "offer_tags")


def gold_for(source_file: str) -> GoldLabel:
    return GoldLabel.model_validate_json((GOLD / (Path(source_file).stem + ".json")).read_text())


def gold_value(gold_obj, path: str):
    flat = flatten(gold_obj)
    if path in flat:
        return flat[path]
    if path == "hotel_name":
        return getattr(gold_obj, "hotel_name", None)
    return get_path(gold_obj.model_dump(mode="json", exclude_none=True), path)


def rows_right(contract: Contract, gold_contract: Contract) -> dict:
    got = {tuple(str(getattr(r, k)) for k in ROW_KEY): r for r in to_offer_rows(contract)}
    want = {tuple(str(getattr(r, k)) for k in ROW_KEY): r for r in to_offer_rows(gold_contract)}
    ok = sum(1 for k, w in want.items() if k in got and all(
        _equal(getattr(got[k], v), getattr(w, v)) for v in ROW_VALUES))
    return {"gold_rows": len(want), "rows_right": ok, "extra_rows": len(set(got) - set(want)),
            "pct": round(100 * ok / max(len(want), 1), 1)}


def offers_state(c: Contract) -> list:
    return sorted([json.dumps(o.model_dump(mode="json", exclude_none=True), sort_keys=True) for o in c.special_offers]
                  + [json.dumps(s.model_dump(mode="json", exclude_none=True, exclude={"note"}), sort_keys=True)
                     for s in c.stop_sales])   # free-text notes aren't compared


def main():
    replays = {p["source_file"]: p for p in load_replays()}
    order = ["001_sol_de_marina_contract.pdf", "002_aegean_breeze_email.eml", "003_sol_de_marina_chat.txt"]
    report = {"per_input": [], "assumptions": {"seconds_per_queued_field": SECONDS_PER_FIELD}}
    with tempfile.TemporaryDirectory() as tmp:
        store = ReviewStore(Path(tmp) / "review.db")
        gold_contract_h001 = gold_for(order[0]).contract
        for name in order:
            p = replays[name]
            gold = gold_for(name)
            gold_obj = gold.contract or gold.change_set
            item = store.add_item(p)
            queue = store.queue(item)
            corrected = 0
            for f in queue:
                want = gold_value(gold_obj, f.path)
                if f.value is not None and want is not None and _equal(f.value, want):
                    store.decide(item, f.path, "accept", seconds=SECONDS_PER_FIELD)
                else:
                    store.decide(item, f.path, "correct", want, seconds=SECONDS_PER_FIELD)
                    corrected += 1
            pred_obj = (Contract if gold.contract else ChangeSet).model_validate(
                p["prediction"]["contract"] if gold.contract else p["prediction"]["change_set"])
            after = store.reviewed_prediction(item)
            after_obj = (Contract if gold.contract else ChangeSet).model_validate(
                after["contract"] if gold.contract else after["change_set"])
            before_s, after_s = score_fields(pred_obj, gold_obj), score_fields(after_obj, gold_obj)
            approved = store.approve(item)
            row = {"input": name, "kind": p["kind"], "hotel_id": p["hotel_id"],
                   "fields": len(p["prediction"]["field_meta"]), "queued": len(queue),
                   "accepted_as_is": len(queue) - corrected, "corrected": corrected,
                   "review_minutes": round(len(queue) * SECONDS_PER_FIELD / 60, 1),
                   "field_f1_before": round(before_s.f1, 3), "field_f1_after": round(after_s.f1, 3),
                   "still_wrong_after_review": [w[0] for w in after_s.wrong] + after_s.missing,
                   "version": approved}
            if gold.contract:
                row["offer_rows_before"] = rows_right(pred_obj, gold.contract)
                row["offer_rows_after"] = rows_right(store.latest(p["hotel_id"]), gold.contract)
            else:
                # what the contract should look like after the amendment, built from gold
                want, _, _ = apply_changes(gold_contract_h001, gold.change_set)
                got = store.latest(p["hotel_id"])
                row["offers_and_stop_sales_match_gold"] = offers_state(got) == offers_state(want)
                row["applied"], row["manual"] = approved["applied"], approved["manual"]
            report["per_input"].append(row)
        for hid in ("H001", "H002"):
            report.setdefault("exports", []).append(export_hotel(store, hid, EXPORT))
        n = store.export_corrections(FLYWHEEL)
        report["corrections_logged"] = n
        report["corrections_by_category"] = {}
        for c in store.corrections():
            report["corrections_by_category"][c["category"]] = report["corrections_by_category"].get(c["category"], 0) + 1
    contracts = [r for r in report["per_input"] if r["kind"] == "contract"]
    report["summary"] = {
        "queued_fields": sum(r["queued"] for r in report["per_input"]),
        "total_fields": sum(r["fields"] for r in report["per_input"]),
        "review_minutes_per_contract": round(sum(r["review_minutes"] for r in contracts) / len(contracts), 1),
        "manual_minutes_per_contract": MANUAL_MINUTES_PER_CONTRACT,
        "offer_rows_right_before": f'{sum(r["offer_rows_before"]["rows_right"] for r in contracts)}/'
                                   f'{sum(r["offer_rows_before"]["gold_rows"] for r in contracts)}',
        "offer_rows_right_after": f'{sum(r["offer_rows_after"]["rows_right"] for r in contracts)}/'
                                  f'{sum(r["offer_rows_after"]["gold_rows"] for r in contracts)}',
        "still_wrong_after_review": sum(len(r["still_wrong_after_review"]) for r in report["per_input"]),
        "usd": 0.0,
    }
    OUT.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report["summary"], indent=2))
    for r in report["per_input"]:
        print(f"{r['input']}: queued {r['queued']}/{r['fields']}, corrected {r['corrected']}, "
              f"F1 {r['field_f1_before']} -> {r['field_f1_after']}, still wrong {r['still_wrong_after_review']}")
        if "offer_rows_before" in r:
            print(f"   offer rows right: {r['offer_rows_before']['pct']}% -> {r['offer_rows_after']['pct']}%")
        else:
            print(f"   applied {r['applied']} | manual {r['manual']} | matches gold: {r['offers_and_stop_sales_match_gold']}")
    print(f"corrections logged to {FLYWHEEL.relative_to(ROOT)}: {report['corrections_logged']}")
    print(f"saved {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
