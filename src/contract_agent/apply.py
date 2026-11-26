"""Step 7: apply an approved change set to the hotel's current contract.

The contract is the master record; every approved amendment produces a NEW
version, never an edit in place. Each change either applies cleanly or is
reported back as something a human must do by hand. We never half-apply a
change silently.
"""
from __future__ import annotations

from .schema import ChangeSet, Contract, StopSale


def apply_changes(contract: Contract, cs: ChangeSet) -> tuple[Contract, list[str], list[str]]:
    """Returns (new contract, what was applied, what could not be applied)."""
    c = contract.model_copy(deep=True)
    done, manual = [], []
    rooms = [r.code for r in c.rooms]
    for ch in cs.changes:
        what = ch.change_type
        target = ch.room_codes or rooms
        if what == "stop_sale" and ch.date_from:
            c.stop_sales.append(StopSale(room_codes=ch.room_codes, date_from=ch.date_from,
                                         date_to=ch.date_to or ch.date_from, note=ch.note))
            done.append(f"stop sale {','.join(ch.room_codes) or 'all rooms'} {ch.date_from} to {ch.date_to}")
        elif what == "open_sale" and ch.date_from:
            before = len(c.stop_sales)
            c.stop_sales = [s for s in c.stop_sales if not (
                set(s.room_codes) == set(ch.room_codes) and s.date_from >= ch.date_from
                and s.date_to <= (ch.date_to or ch.date_from))]
            (done if len(c.stop_sales) < before else manual).append(
                f"re-open {','.join(ch.room_codes) or 'all rooms'} {ch.date_from} to {ch.date_to}")
        elif what == "rate_change" and ch.new_price is not None and ch.date_from:
            # only whole seasons can be repriced in this rate model; part-seasons need a human
            seasons = [s.name for s in c.seasons if s.date_from >= ch.date_from and s.date_to <= (ch.date_to or ch.date_from)]
            partial = [s.name for s in c.seasons if s.date_from <= (ch.date_to or ch.date_from)
                       and s.date_to >= ch.date_from and s.name not in seasons]
            hit = [r for r in c.rates if r.season in seasons and r.room_code in target
                   and (not ch.boards or r.board in ch.boards)]
            for r in hit:
                r.price = ch.new_price
            if hit and not partial:
                done.append(f"rate {','.join(target)} {','.join(ch.boards) or 'all boards'} {seasons} -> {ch.new_price:g}")
            else:
                manual.append(f"rate change {ch.date_from} to {ch.date_to} covers part of a season: price by hand")
        elif what == "allotment_change" and ch.note:
            manual.append(f"allotment change: {ch.note}")
        elif what == "release_change":
            manual.append(f"release change: {ch.note or ''}".strip())
        elif what == "min_stay_change":
            manual.append(f"min stay change: {ch.note or ''}".strip())
        elif what == "offer_added" and ch.offer:
            c.special_offers.append(ch.offer)
            done.append(f"offer added: {ch.offer.type}")
        elif what == "offer_changed" and ch.offer:
            same = [o for o in c.special_offers if o.type == ch.offer.type]
            if len(same) == 1:
                # playbook: an offer change inherits every field it doesn't mention
                o = same[0]
                for k, v in ch.offer.model_dump(exclude_none=True, exclude_defaults=True).items():
                    setattr(o, k, getattr(ch.offer, k))
                done.append(f"offer changed: {o.type}")
            else:
                manual.append(f"offer change: {len(same)} existing {ch.offer.type} offers, pick one by hand")
        elif what == "offer_ended" and ch.offer:
            before = len(c.special_offers)
            c.special_offers = [o for o in c.special_offers if o.type != ch.offer.type]
            (done if len(c.special_offers) < before else manual).append(f"offer ended: {ch.offer.type}")
        else:
            manual.append(f"{what}: not enough detail to apply")
    return Contract.model_validate(c.model_dump()), done, manual
