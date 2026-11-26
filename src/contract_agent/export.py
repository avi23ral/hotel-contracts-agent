"""Step 7: export the offer file from APPROVED contract versions only.

The export never reads the agent's raw output. It reads the latest approved
version of each hotel's contract, so an unreviewed field can't reach the
offer file. Rates and availability go out as separate files, because they
change at different speeds (a stop sale shouldn't mean re-sending every price).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .offers import OfferRow, to_offer_rows
from .review import ReviewStore


def offer_rows(store: ReviewStore, hotel_id: str) -> list[OfferRow]:
    c = store.latest(hotel_id)
    if c is None:
        raise ValueError(f"{hotel_id} has no approved contract version yet")
    return to_offer_rows(c, source_contract_id=f"{hotel_id}-v{store.latest_version(hotel_id)}")


def export_hotel(store: ReviewStore, hotel_id: str, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = offer_rows(store, hotel_id)
    c = store.latest(hotel_id)
    tag = f"{hotel_id}_v{store.latest_version(hotel_id)}"
    with (out_dir / f"offers_{tag}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(OfferRow.model_fields))
        w.writeheader()
        w.writerows(r.model_dump() for r in rows)
    (out_dir / f"offers_{tag}.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in rows))
    with (out_dir / f"stop_sales_{tag}.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hotel_id", "room_codes", "date_from", "date_to", "note"])
        for s in c.stop_sales:
            w.writerow([hotel_id, ";".join(s.room_codes) or "ALL", s.date_from, s.date_to, s.note or ""])
    pending = [i for i in store.items() if i["hotel_id"] == hotel_id and i["status"] == "pending"]
    return {"hotel_id": hotel_id, "version": store.latest_version(hotel_id), "rows": len(rows),
            "stop_sales": len(c.stop_sales), "offers": [o.type for o in c.special_offers],
            "files": [f"offers_{tag}.csv", f"offers_{tag}.jsonl", f"stop_sales_{tag}.csv"],
            "pending_items_not_included": [i["source_file"] for i in pending]}


def rows_as_json(rows: list[OfferRow]) -> str:
    return json.dumps([r.model_dump() for r in rows], indent=2)
