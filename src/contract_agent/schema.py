"""Offer schema v0.

Three layers:
1. Domain models (Contract, ChangeSet): pure values. Gold labels use these.
2. FieldMeta: confidence + evidence per field, keyed by a path such as
   "rates[3].price". Kept separate so gold labels stay simple and the
   eval can compare Contract to Contract.
3. ExtractionResult: what the agent returns = domain model + field_meta.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ---------- enums ----------

BoardCode = Literal["RO", "BB", "HB", "FB", "AI"]  # room only ... all inclusive


class Intent(str, Enum):
    new_contract = "new_contract"
    amendment = "amendment"          # changes to an existing contract
    not_a_contract = "not_a_contract"


class Status(str, Enum):
    auto_accept = "auto_accept"
    needs_review = "needs_review"
    missing = "missing"


# ---------- contract (new hotel) ----------

class Hotel(BaseModel):
    name: str
    destination: str
    country: str
    supplier_email: str | None = None


class Room(BaseModel):
    code: str                       # our code, e.g. DBL
    name: str                       # hotel's wording, e.g. "Double Sea View"
    min_adults: int = Field(ge=1)
    max_adults: int = Field(ge=1)
    max_children: int = Field(ge=0)
    max_occupancy: int = Field(ge=1)
    allotment: int | None = Field(default=None, ge=0, description="rooms per night")


class Season(BaseModel):
    name: str
    date_from: date
    date_to: date
    min_stay_nights: int | None = None

    @model_validator(mode="after")
    def _order(self):
        if self.date_from > self.date_to:
            raise ValueError(f"season {self.name}: date_from after date_to")
        return self


class Rate(BaseModel):
    season: str
    room_code: str
    board: BoardCode
    price: float = Field(gt=0)


class ChildBand(BaseModel):
    age_from: float
    age_to: float
    discount_pct: float | None = None   # e.g. 50 = half price
    fixed_price: float | None = None    # per night, same basis as rates
    note: str | None = None


class CancellationTier(BaseModel):
    days_from: int                      # days before arrival, inclusive
    days_to: int | None = None          # None = and beyond
    charge_pct: float


class SpecialOffer(BaseModel):
    type: Literal["early_booking", "free_nights", "long_stay", "discount"]
    discount_pct: float | None = None
    stay_nights: int | None = None      # "stay 7 ..."
    pay_nights: int | None = None       # "... pay 6"
    book_from: date | None = None
    book_by: date | None = None
    stay_from: date | None = None
    stay_to: date | None = None
    boards: list[BoardCode] = []        # empty = all
    combinable: bool | None = None


class LocalTax(BaseModel):
    included_in_rate: bool
    amount: float | None = None
    basis: Literal["per_person_per_night", "per_room_per_night"] | None = None


class Contract(BaseModel):
    hotel: Hotel
    contract_start: date
    contract_end: date
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    rate_type: Literal["net", "commissionable"]
    commission_pct: float | None = None
    pricing_basis: Literal["per_room_per_night", "per_person_per_night"]
    rooms: list[Room]
    seasons: list[Season]
    rates: list[Rate]
    child_policy: list[ChildBand] = []
    release_days: int | None = None
    cancellation: list[CancellationTier] = []
    special_offers: list[SpecialOffer] = []
    local_tax: LocalTax | None = None
    payment_terms: str | None = None

    @model_validator(mode="after")
    def _consistency(self):
        """Deterministic checks. These later feed the confidence score."""
        rooms = {r.code for r in self.rooms}
        seasons = {s.name for s in self.seasons}
        for r in self.rates:
            if r.room_code not in rooms:
                raise ValueError(f"rate references unknown room {r.room_code}")
            if r.season not in seasons:
                raise ValueError(f"rate references unknown season {r.season}")
        ordered = sorted(self.seasons, key=lambda s: s.date_from)
        for a, b in zip(ordered, ordered[1:]):
            if b.date_from <= a.date_to:
                raise ValueError(f"seasons overlap: {a.name} / {b.name}")
        for s in self.seasons:
            if s.date_from < self.contract_start or s.date_to > self.contract_end:
                raise ValueError(f"season {s.name} outside contract dates")
        return self


# ---------- change set (amendment to an existing contract) ----------

class Change(BaseModel):
    change_type: Literal[
        "stop_sale", "rate_change", "offer_added", "offer_changed",
        "allotment_change", "release_change",
    ]
    room_codes: list[str] = []          # empty = all rooms
    boards: list[BoardCode] = []        # empty = all boards
    date_from: date | None = None       # stay dates affected
    date_to: date | None = None
    new_price: float | None = None
    offer: SpecialOffer | None = None
    note: str | None = None


class ChangeSet(BaseModel):
    hotel_name: str
    changes: list[Change]


# ---------- what the agent returns ----------

class FieldMeta(BaseModel):
    confidence: float = Field(ge=0, le=1)
    evidence: str | None = None         # verbatim quote from the source
    status: Status


class ExtractionResult(BaseModel):
    source_file: str
    intent: Intent
    contract: Contract | None = None
    change_set: ChangeSet | None = None
    field_meta: dict[str, FieldMeta] = {}   # "rates[3].price" -> meta


# ---------- gold label (hand-written expected answer) ----------

class GoldLabel(BaseModel):
    source_file: str
    channel: Literal["pdf", "email", "chat"]
    difficulty: Literal["easy", "medium", "hard"]
    intent: Intent
    contract: Contract | None = None
    change_set: ChangeSet | None = None
    tests_what: list[str] = []          # what this example is designed to probe
