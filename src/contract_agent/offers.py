"""Flatten a Contract into hotel-only offer rows.

These rows are a cached, derived view, NOT the master record. The master
record is the Contract (the hotel's contracted rates and availability). A
dynamic packager prices it live alongside flights. We keep the rows because
they are what a reviewer and a test can check line by line, and because one
wrong contract field fans out into many wrong rows.

One row = hotel x room x board x adult count x season date band.
Children and special offers multiply this further, which is why real
offer files get enormous. v0 keeps children = 0 to stay readable.
"""
from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel

from .schema import Contract, FieldMeta


class OfferRow(BaseModel):
    hotel: str
    room_code: str
    board: str
    adults: int
    children: int
    date_from: str
    date_to: str
    min_stay_nights: int | None
    price_per_night: float      # total for the room, whatever the contract basis
    currency: str
    rate_type: str
    offer_tags: str
    hotel_id: str | None = None
    source_contract_id: str | None = None
    lowest_field_confidence: float | None = None   # weakest field this row depends on


def _lowest(meta: dict[str, FieldMeta] | None, prefixes: list[str]) -> float | None:
    if not meta:
        return None
    hits = [m.confidence for p, m in meta.items()
            if "[" not in p.split(".")[0] or any(p == x or p.startswith(x + ".") for x in prefixes)]
    return min(hits) if hits else None


def to_offer_rows(c: Contract, field_meta: dict[str, FieldMeta] | None = None,
                  source_contract_id: str | None = None) -> list[OfferRow]:
    seasons = {s.name: s for s in c.seasons}
    rooms = {r.code: r for r in c.rooms}
    tags = ";".join(sorted({o.type for o in c.special_offers}))
    rows: list[OfferRow] = []
    for rate in c.rates:
        room, season = rooms[rate.room_code], seasons[rate.season]
        for adults in range(room.min_adults, room.max_adults + 1):
            per_room = rate.price if c.pricing_basis == "per_room_per_night" else rate.price * adults
            rows.append(OfferRow(
                hotel=c.hotel.name, room_code=room.code, board=rate.board,
                adults=adults, children=0,
                date_from=season.date_from.isoformat(), date_to=season.date_to.isoformat(),
                min_stay_nights=season.min_stay_nights,
                price_per_night=round(per_room, 2), currency=c.currency,
                rate_type=c.rate_type, offer_tags=tags,
                hotel_id=c.hotel.hotel_id, source_contract_id=source_contract_id,
                lowest_field_confidence=_lowest(field_meta, [
                    f"rates[{rate.season}|{room.code}|{rate.board}]", f"rooms[{room.code}]",
                    f"seasons[{season.name}]"]),
            ))
    return rows


def write_csv(rows: list[OfferRow], path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(OfferRow.model_fields))
        w.writeheader()
        for r in rows:
            w.writerow(r.model_dump())
