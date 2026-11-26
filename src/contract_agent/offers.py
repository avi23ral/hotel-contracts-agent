"""Flatten a Contract into static offer rows.

One row = hotel x room x board x adult count x season date band.
Children and special offers multiply this further, which is why real
offer files get enormous. v0 keeps children = 0 to stay readable.
"""
from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel

from .schema import Contract


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


def to_offer_rows(c: Contract) -> list[OfferRow]:
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
            ))
    return rows


def write_csv(rows: list[OfferRow], path: Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(OfferRow.model_fields))
        w.writeheader()
        for r in rows:
            w.writerow(r.model_dump())
