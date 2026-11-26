"""Offer schema v1 (step 3).

v1 changes, from the research in the build plan:
- Default commercial model is net, per room per night. Per person and
  commissionable are still supported, because some hotels send them and the
  agent has to spot and convert them.
- Each room has an inventory model: free sale (the default for an OTA-style
  operator), allotment, or on request. Release days and allotment counts only
  make sense for allotment rooms.
- Hotels carry our internal hotel_id (system of record) and an optional
  GIATA-style id for cross-supplier mapping. The agent never invents either:
  they come from the property master lookup in step 6.
- Rates can carry a single-use price, because hotels quote single terms.
- Cross-field checks (overlapping seasons, unknown rooms...) moved out of the
  schema into validators.py. A broken rule now lowers the confidence of the
  fields involved instead of throwing away the whole extraction.

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

from pydantic import BaseModel, Field

# ---------- enums ----------

BoardCode = Literal["RO", "BB", "HB", "FB", "AI"]  # room only ... all inclusive


class Intent(str, Enum):
    new_contract = "new_contract"
    amendment = "amendment"          # changes to an existing contract
    not_a_contract = "not_a_contract"


class InventoryModel(str, Enum):
    free_sale = "free_sale"      # sell until the hotel sends a stop sale
    allotment = "allotment"      # guaranteed rooms per night, returned at release
    on_request = "on_request"    # each booking needs the hotel's confirmation


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
    hotel_id: str | None = None         # our internal code (system of record), from the property master
    giata_id: str | None = None         # shared cross-supplier mapping id, not the booking key


class Room(BaseModel):
    code: str                       # our code, e.g. DBL
    name: str                       # hotel's wording, e.g. "Double Sea View"
    min_adults: int = Field(ge=1)
    max_adults: int = Field(ge=1)
    max_children: int = Field(ge=0)
    max_occupancy: int = Field(ge=1)
    inventory: InventoryModel = InventoryModel.free_sale
    allotment: int | None = Field(default=None, ge=0, description="rooms per night, allotment rooms only")


class Season(BaseModel):
    name: str
    date_from: date
    date_to: date
    min_stay_nights: int | None = None


class Rate(BaseModel):
    season: str
    room_code: str
    board: BoardCode
    price: float = Field(gt=0)
    single_use_price: float | None = Field(default=None, gt=0,
                                           description="room price when one adult stays; None = not stated")


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


class StopSale(BaseModel):
    """Dates a room can't be sold (availability layer, kept apart from rates)."""
    room_codes: list[str] = []          # empty = all rooms
    date_from: date
    date_to: date
    note: str | None = None


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
    stop_sales: list[StopSale] = []       # step 7: closed dates applied from approved amendments
    # Cross-field checks live in validators.py (soft flags, not exceptions).


# ---------- change set (amendment to an existing contract) ----------

class Change(BaseModel):
    change_type: Literal[
        # ARI (availability, rates, inventory): most post-contract traffic
        "stop_sale", "open_sale", "rate_change", "allotment_change", "release_change",
        "min_stay_change",
        # promotions: their own versioned layer
        "offer_added", "offer_changed", "offer_ended",
    ]
    room_codes: list[str] = []          # empty = all rooms
    boards: list[BoardCode] = []        # empty = all boards
    date_from: date | None = None       # stay dates affected
    date_to: date | None = None
    new_price: float | None = None
    offer: SpecialOffer | None = None
    note: str | None = None


ARI_CHANGES = {"stop_sale", "open_sale", "rate_change", "allotment_change", "release_change",
               "min_stay_change"}
PROMO_CHANGES = {"offer_added", "offer_changed", "offer_ended"}


class ChangeSet(BaseModel):
    hotel_name: str
    hotel_id: str | None = None         # set from the property master after retrieval, never by the model
    changes: list[Change]


# ---------- what the agent returns ----------

class FieldMeta(BaseModel):
    """Everything the reviewer needs to trust or check one field."""
    confidence: float = Field(ge=0, le=1)   # combined score (see confidence.py)
    evidence: str | None = None             # verbatim quote from the source
    status: Status
    category: str = "other"                 # cancellation | price | dates | board | other
    threshold: float | None = None          # bar this field had to clear to be auto-accepted
    model_confidence: float | None = None   # the model's own rating, before our checks
    evidence_found: bool | None = None      # quote really appears in the source text
    value_in_quote: bool | None = None      # the value itself appears in the quote (None = not checkable)
    derived: bool = False                   # value was computed, e.g. AI = HB + supplement
    agrees: bool | None = None              # second extraction pass gave the same value
    flags: list[str] = []                   # validator rules this field broke
    reasons: list[str] = []                 # plain-English reasons the score was lowered


class Issue(BaseModel):
    """A deterministic validator finding."""
    rule: str
    severity: Literal["error", "warning"]   # error lowers confidence; warning is shown to the reviewer
    paths: list[str]                        # field paths (or prefixes) the rule points at
    message: str


class ExtractionResult(BaseModel):
    source_file: str
    intent: Intent
    contract: Contract | None = None
    change_set: ChangeSet | None = None
    field_meta: dict[str, FieldMeta] = {}   # "rates[High|DBL|HB].price" -> meta
    issues: list[Issue] = []


# ---------- gold label (hand-written expected answer) ----------

class GoldLabel(BaseModel):
    source_file: str
    channel: Literal["pdf", "email", "chat"]
    difficulty: Literal["easy", "medium", "hard"]
    intent: Intent
    contract: Contract | None = None
    change_set: ChangeSet | None = None
    tests_what: list[str] = []          # what this example is designed to probe
