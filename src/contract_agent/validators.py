"""Step 3: deterministic validator rules.

Plain Python checks that need no model. Each rule returns Issues that point
at field paths (same path format as scoring.flatten). An "error" caps the
confidence of every field it points at, so the field goes to a human. A
"warning" is shown to the reviewer but does not change any score.

Why rules and not just the model's confidence: a model can be 0.97 sure of
two seasons that overlap. The rule can't be talked out of it.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from .schema import ChangeSet, Contract, InventoryModel, Issue
from .scoring import LIST_KEYS

BOARD_ORDER = {"RO": 0, "BB": 1, "HB": 2, "FB": 3, "AI": 4}
ADULT_AGE = 18


def _k(field: str, item) -> str:
    return f"{field}[{LIST_KEYS[field](item.model_dump(mode='json', exclude_none=True))}]"


def _err(rule, paths, msg):
    return Issue(rule=rule, severity="error", paths=list(paths), message=msg)


def _warn(rule, paths, msg):
    return Issue(rule=rule, severity="warning", paths=list(paths), message=msg)


def validate_contract(c: Contract) -> list[Issue]:
    out: list[Issue] = []
    rooms = {r.code: r for r in c.rooms}
    seasons = {s.name: s for s in c.seasons}

    # --- contract period and commercials
    if c.contract_start > c.contract_end:
        out.append(_err("contract_dates_order", ["contract_start", "contract_end"],
                         "contract starts after it ends"))
    if c.rate_type == "commissionable" and c.commission_pct is None:
        out.append(_err("commission_missing", ["rate_type"], "commissionable rate but no commission %"))
    if c.rate_type == "net" and c.commission_pct:
        out.append(_err("commission_on_net", ["rate_type", "commission_pct"], "net rate with a commission %"))
    if c.commission_pct is not None and not 0 < c.commission_pct < 50:
        out.append(_err("commission_range", ["commission_pct"], f"commission {c.commission_pct}% looks wrong"))
    if c.pricing_basis == "per_person_per_night" or c.rate_type == "commissionable":
        out.append(_warn("non_default_terms", ["pricing_basis", "rate_type"],
                         "not our default (net, per room per night): check the conversion in the offer rows"))

    # --- rooms and inventory
    for r in c.rooms:
        p = _k("rooms", r)
        if not r.min_adults <= r.max_adults <= r.max_occupancy:
            out.append(_err("occupancy_order", [f"{p}.min_adults", f"{p}.max_adults", f"{p}.max_occupancy"],
                             f"{r.code}: adults {r.min_adults}-{r.max_adults} don't fit max occupancy {r.max_occupancy}"))
        if r.max_children > r.max_occupancy - r.min_adults:
            out.append(_err("children_fit", [f"{p}.max_children", f"{p}.max_occupancy"],
                             f"{r.code}: {r.max_children} children can't fit with {r.min_adults} adults"))
        if r.inventory == InventoryModel.allotment and r.allotment is None:
            out.append(_err("allotment_count_missing", [f"{p}.inventory"],
                             f"{r.code}: allotment room with no rooms-per-night count"))
        if r.inventory != InventoryModel.allotment and r.allotment is not None:
            out.append(_err("allotment_on_free_sale", [f"{p}.inventory", f"{p}.allotment"],
                             f"{r.code}: allotment count on a {r.inventory.value} room"))
    if any(r.inventory == InventoryModel.allotment for r in c.rooms) and c.release_days is None:
        out.append(_warn("release_missing", ["release_days"], "allotment rooms but no release period stated"))
    if c.release_days is not None and not 0 <= c.release_days <= 60:
        out.append(_err("release_range", ["release_days"], f"release {c.release_days} days looks wrong"))

    # --- seasons
    for s in c.seasons:
        p = _k("seasons", s)
        if s.date_from > s.date_to:
            out.append(_err("season_dates_order", [f"{p}.date_from", f"{p}.date_to"], f"{s.name}: ends before it starts"))
        if s.date_from < c.contract_start or s.date_to > c.contract_end:
            out.append(_err("season_outside_contract", [f"{p}.date_from", f"{p}.date_to"],
                             f"{s.name}: outside the contract period"))
        if s.min_stay_nights is not None and not 1 <= s.min_stay_nights <= 21:
            out.append(_err("min_stay_range", [f"{p}.min_stay_nights"], f"{s.name}: min stay {s.min_stay_nights}"))
    ordered = sorted(c.seasons, key=lambda s: s.date_from)
    for a, b in zip(ordered, ordered[1:]):
        pa, pb = _k("seasons", a), _k("seasons", b)
        if b.date_from <= a.date_to:
            out.append(_err("season_overlap", [f"{pa}.date_to", f"{pb}.date_from"], f"{a.name} and {b.name} overlap"))
        elif (b.date_from - a.date_to).days > 1:
            out.append(_err("season_gap", [f"{pa}.date_to", f"{pb}.date_from"],
                             f"gap between {a.name} and {b.name}: no price for those nights"))
    if ordered and (ordered[0].date_from > c.contract_start or ordered[-1].date_to < c.contract_end):
        out.append(_warn("season_coverage", ["contract_start", "contract_end"],
                         "seasons don't cover the whole contract period"))

    # --- rates
    grid = defaultdict(dict)   # (season, room) -> board -> price
    for r in c.rates:
        p = _k("rates", r)
        if r.room_code not in rooms:
            out.append(_err("rate_unknown_room", [p], f"rate for unknown room {r.room_code}"))
        if r.season not in seasons:
            out.append(_err("rate_unknown_season", [p], f"rate for unknown season {r.season}"))
        if r.single_use_price is not None and r.single_use_price > r.price:
            out.append(_err("single_above_double", [f"{p}.single_use_price"], f"{p}: single use dearer than the room"))
        grid[(r.season, r.room_code)][r.board] = (r.price, p)
    for (season, room), by_board in grid.items():
        ranked = sorted(by_board.items(), key=lambda kv: BOARD_ORDER[kv[0]])
        for (b1, (p1, path1)), (b2, (p2, path2)) in zip(ranked, ranked[1:]):
            if p2 <= p1:
                out.append(_err("board_price_order", [f"{path1}.price", f"{path2}.price"],
                                 f"{room} {season}: {b2} ({p2:g}) not dearer than {b1} ({p1:g})"))
    boards = {r.board for r in c.rates}
    for s in c.seasons:
        for code in rooms:
            for b in boards:
                if b not in grid.get((s.name, code), {}):
                    out.append(_warn("rate_grid_hole", [f"rates"], f"no {b} rate for {code} in {s.name}"))
    for (season, room), by_board in grid.items():
        for b, (price, path) in by_board.items():
            peers = [v[0] for (s2, r2), bb in grid.items() if r2 == room for b2, v in bb.items() if b2 == b]
            mid = sorted(peers)[len(peers) // 2]
            if price > 3 * mid or price < mid / 3:
                out.append(_err("price_outlier", [f"{path}.price"], f"{path} = {price:g}, far from its peers ({mid:g})"))

    # --- children
    bands = sorted(c.child_policy, key=lambda b: b.age_from)
    for b in bands:
        p = _k("child_policy", b)
        if b.age_from > b.age_to or b.age_to >= ADULT_AGE:
            out.append(_err("child_age_range", [f"{p}.age_from", f"{p}.age_to"], f"child band {b.age_from}-{b.age_to}"))
        if b.discount_pct is not None and not 0 <= b.discount_pct <= 100:
            out.append(_err("child_discount_range", [f"{p}.discount_pct"], f"child discount {b.discount_pct}%"))
        if b.discount_pct is None and b.fixed_price is None:
            out.append(_err("child_price_missing", [p], f"child band {b.age_from}-{b.age_to} has no price"))
    for a, b in zip(bands, bands[1:]):
        if b.age_from <= a.age_to:
            out.append(_err("child_band_overlap", [_k("child_policy", a), _k("child_policy", b)], "child bands overlap"))

    # --- cancellation (costliest field to get wrong, so the strictest checks)
    tiers = sorted(c.cancellation, key=lambda t: t.days_from, reverse=True)   # furthest from arrival first
    paths = [_k("cancellation", t) for t in tiers]
    for t, p in zip(tiers, paths):
        if not 0 <= t.charge_pct <= 100:
            out.append(_err("cancel_pct_range", [f"{p}.charge_pct"], f"charge {t.charge_pct}%"))
        if t.days_to is not None and t.days_to < t.days_from:
            out.append(_err("cancel_tier_order", [p], f"tier {t.days_from}-{t.days_to} is backwards"))
    for (a, pa), (b, pb) in zip(zip(tiers, paths), list(zip(tiers, paths))[1:]):
        if b.charge_pct < a.charge_pct:
            out.append(_err("cancel_charge_falls", [f"{pa}.charge_pct", f"{pb}.charge_pct"],
                             "charge gets lower closer to arrival"))
        if b.days_to is not None and b.days_to != a.days_from - 1:
            out.append(_err("cancel_tier_gap", [pa, pb], f"tiers {a.days_from}+ and {b.days_from}-{b.days_to} don't join up"))
    if tiers and tiers[-1].days_from != 0:
        out.append(_err("cancel_no_arrival_tier", [paths[-1]], "no tier covers the last days before arrival"))
    if tiers and tiers[0].days_to is not None:
        out.append(_err("cancel_no_open_tier", [paths[0]], "no tier covers bookings cancelled far in advance"))
    if not tiers:
        out.append(_warn("cancel_missing", ["cancellation"], "no cancellation terms: must be confirmed with the hotel"))

    # --- offers
    for o in c.special_offers:
        out += _offer_rules(o, _k("special_offers", o), c.contract_start, c.contract_end)

    # --- research-driven reminders (warnings)
    if any(r.min_adults == 1 for r in c.rooms) and not any(r.single_use_price for r in c.rates):
        out.append(_warn("single_terms_missing", ["rates"], "rooms allow 1 adult but no single-use price is stated"))
    return out


def _offer_rules(o, p: str, start: date | None = None, end: date | None = None) -> list[Issue]:
    out = []
    if o.discount_pct is not None and not 0 < o.discount_pct < 100:
        out.append(_err("offer_discount_range", [f"{p}.discount_pct"], f"offer discount {o.discount_pct}%"))
    if o.stay_from and o.stay_to and o.stay_from > o.stay_to:
        out.append(_err("offer_stay_order", [f"{p}.stay_from", f"{p}.stay_to"], "offer stay window is backwards"))
    if o.book_from and o.book_by and o.book_from > o.book_by:
        out.append(_err("offer_book_order", [f"{p}.book_from", f"{p}.book_by"], "offer booking window is backwards"))
    if o.book_by and o.stay_to and o.book_by > o.stay_to:
        out.append(_err("offer_book_after_stay", [f"{p}.book_by"], "book-by date is after the last stay date"))
    if o.book_from and o.stay_to and o.book_from > o.stay_to:
        out.append(_err("offer_book_after_stay", [f"{p}.book_from"], "booking opens after the last stay date"))
    if o.type == "free_nights" and not (o.stay_nights and o.pay_nights and o.pay_nights < o.stay_nights):
        out.append(_err("free_nights_shape", [f"{p}.stay_nights", f"{p}.pay_nights"], "stay X pay Y needs Y < X"))
    if start and end and o.stay_from and o.stay_to and (o.stay_from < start or o.stay_to > end):
        out.append(_err("offer_outside_contract", [f"{p}.stay_from", f"{p}.stay_to"], "offer stays outside the contract"))
    return out


def validate_change_set(cs: ChangeSet) -> list[Issue]:
    out: list[Issue] = []
    for ch in cs.changes:
        p = _k("changes", ch)
        if ch.date_from and ch.date_to and ch.date_from > ch.date_to:
            out.append(_err("change_dates_order", [f"{p}.date_from", f"{p}.date_to"], "change dates are backwards"))
        if ch.change_type == "stop_sale" and not (ch.date_from and ch.date_to):
            out.append(_err("stop_sale_dates", [p], "stop sale without both dates"))
        if ch.change_type == "rate_change" and ch.new_price is None:
            out.append(_err("rate_change_price", [p], "rate change without a new price"))
        if ch.change_type in ("offer_added", "offer_changed"):
            if ch.offer is None:
                out.append(_err("offer_missing", [p], "offer change without the offer"))
            else:
                out += _offer_rules(ch.offer, f"{p}.offer")
                if not (ch.offer.stay_from and ch.offer.stay_to):
                    out.append(_warn("offer_needs_contract", [f"{p}.offer"],
                                     "stay window not in the message: needs the existing contract (step 5, RAG)"))
    return out


def validate(contract: Contract | None, change_set: ChangeSet | None) -> list[Issue]:
    return (validate_contract(contract) if contract else []) + \
           (validate_change_set(change_set) if change_set else [])
