"""Step 5: retrieval (the R in RAG).

An amendment only makes sense next to the contract it changes. "Extend the
early booking to end of Feb, same conditions" doesn't say which stay dates
or whether it's combinable: that lives in the existing contract. So before
extracting an amendment we fetch:

1. the HOTEL: which of our hotels sent this? (property master)
2. the CONTRACT SECTIONS of that hotel that the message is about
3. the PLAYBOOK RULES that apply (house conventions)

and pass them to the model as reference material.

Retrieval is BM25: classic keyword scoring (term frequency, rarer words
count more, long chunks don't win just by being long). It's free, fast,
runs locally and every score can be explained, which is why it's the first
thing to try before embeddings. Sender email domain is a deterministic
signal, so it is checked before any text scoring.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from .schema import Contract

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "data" / "store"
PLAYBOOK = ROOT / "data" / "playbook.md"

STOP = set("""a an and are as at be by for from has have i in is it its of on or our please pls the this
to was we will with you your all any can do if not so that them then there these they up us""".split())


def tokens(text: str) -> list[str]:
    """Lowercase words, minus stop words and bare numbers. Numbers are poor search
    terms here: chat timestamps ("09:15") would otherwise match child ages and prices."""
    return [t for t in re.findall(r"[a-z][a-z0-9]*", text.lower().replace("_", " ")) if t not in STOP]


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.docs = [tokens(d) for d in docs]
        self.k1, self.b = k1, b
        self.avg = sum(map(len, self.docs)) / max(len(self.docs), 1)
        df = Counter(t for d in self.docs for t in set(d))
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, query: str) -> list[float]:
        q = tokens(query)
        out = []
        for tf, d in zip(self.tf, self.docs):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / self.avg))
            out.append(s)
        return out


class Chunk(BaseModel):
    hotel_id: str | None
    section: str
    text: str


# ---------- turning a contract into readable chunks ----------

def contract_chunks(c: Contract) -> list[Chunk]:
    h = c.hotel
    hid = h.hotel_id
    rooms = {r.code: r.name for r in c.rooms}
    out = [Chunk(hotel_id=hid, section="identity", text=(
        f"{h.name} ({hid}, GIATA {h.giata_id}), {h.destination}, {h.country}. Contract "
        f"{c.contract_start} to {c.contract_end}, {c.rate_type} {c.currency} {c.pricing_basis}."))]
    out.append(Chunk(hotel_id=hid, section="rooms", text="Rooms, room types, inventory, allotment, release, "
                     "stop sale availability: " + "; ".join(
        f"{r.code} = {r.name}, {r.min_adults}-{r.max_adults} adults, max {r.max_children} children, "
        f"{r.inventory.value}" + (f", allotment {r.allotment}" if r.allotment is not None else "")
        for r in c.rooms) + (f". Release {c.release_days} days." if c.release_days is not None else ".")))
    out.append(Chunk(hotel_id=hid, section="seasons", text="Seasons, dates and minimum stay (min stay nights): " + "; ".join(
        f"{s.name} {s.date_from} to {s.date_to}" + (f" min {s.min_stay_nights} nights" if s.min_stay_nights else "")
        for s in c.seasons)))
    by_room: dict[str, list[str]] = {}
    for r in c.rates:
        by_room.setdefault(r.room_code, []).append(f"{r.season} {r.board} {r.price:g}")
    out.append(Chunk(hotel_id=hid, section="rates", text=f"Rates, prices, supplements per board "
                     f"({c.currency}, {c.pricing_basis}): " + " | ".join(
        f"{code} {rooms.get(code, '')}: " + ", ".join(v) for code, v in by_room.items())))
    if c.special_offers:
        out.append(Chunk(hotel_id=hid, section="offers", text="Special offers, promotions, discounts: " + "; ".join(
            _offer_text(o) for o in c.special_offers)))
    if c.cancellation:
        out.append(Chunk(hotel_id=hid, section="cancellation", text="Cancellation policy and charges: " + "; ".join(
            f"{t.days_from}" + (f"-{t.days_to}" if t.days_to is not None else "+") + f" days before arrival {t.charge_pct:g}%"
            for t in c.cancellation)))
    if c.child_policy:
        out.append(Chunk(hotel_id=hid, section="children", text="Child policy, kids ages: " + "; ".join(
            f"{b.age_from:g}-{b.age_to:g}: " + (f"{b.discount_pct:g}% off" if b.discount_pct is not None
                                               else f"{b.fixed_price:g} per night") for b in c.child_policy)))
    return out


def _offer_text(o) -> str:
    bits = [o.type.replace("_", " ") + " offer"]
    if o.discount_pct is not None:
        bits.append(f"{o.discount_pct:g}% discount")
    if o.stay_nights and o.pay_nights:
        bits.append(f"stay {o.stay_nights} pay {o.pay_nights}")
    if o.book_from:
        bits.append(f"book from {o.book_from}")
    if o.book_by:
        bits.append(f"book by {o.book_by}")
    if o.stay_from and o.stay_to:
        bits.append(f"stays {o.stay_from} to {o.stay_to}")
    bits.append("boards " + (", ".join(o.boards) if o.boards else "all"))
    if o.combinable is not None:
        bits.append("combinable" if o.combinable else "not combinable with other offers")
    return ", ".join(bits)


def playbook_chunks() -> list[Chunk]:
    parts = re.split(r"^## ", PLAYBOOK.read_text(), flags=re.M)[1:]
    return [Chunk(hotel_id=None, section="playbook:" + p.splitlines()[0].strip(), text="## " + p.strip())
            for p in parts]


# ---------- the store ----------

class Store:
    def __init__(self, root: Path = STORE):
        self.hotels = json.loads((root / "hotels.json").read_text())
        self.contracts = {h["hotel_id"]: Contract.model_validate_json((root / f"contract_{h['hotel_id']}.json").read_text())
                          for h in self.hotels}
        self.chunks = {hid: contract_chunks(c) for hid, c in self.contracts.items()}
        self.playbook = playbook_chunks()
        # hotel-level documents: everything that identifies a hotel
        self._hotel_docs = [" ".join([h["name"], *h["aliases"], h["destination"], h["country"],
                                      h["email_domain"], *h["contacts"],
                                      *(r.name for r in self.contracts[h["hotel_id"]].rooms)])
                            for h in self.hotels]
        self._hotel_bm25 = BM25(self._hotel_docs)
        self._play_bm25 = BM25([c.text for c in self.playbook])

    def find_hotel(self, text: str, bm25_only: bool = False) -> list[tuple[str, float, str]]:
        """Ranked (hotel_id, score, how). Email domain is exact, so it wins outright.
        bm25_only=True skips the exact checks (used by the eval to show what they add)."""
        low = text.lower()
        if bm25_only:
            scores = self._hotel_bm25.scores(text)
            return [(hid, round(s, 2), "bm25") for hid, s in
                    sorted(zip((h["hotel_id"] for h in self.hotels), scores), key=lambda x: -x[1])]
        for h in self.hotels:
            if "@" + h["email_domain"] in low or h["email_domain"] in low:
                return [(h["hotel_id"], 99.0, "email domain")]
        # full names are strong evidence: a phrase match beats bag-of-words overlap
        named = [h for h in self.hotels if any(n.lower() in low for n in [h["name"], *h["aliases"]])]
        if len(named) == 1:
            return [(named[0]["hotel_id"], 50.0, "name match")]
        scores = self._hotel_bm25.scores(text)
        ranked = sorted(zip((h["hotel_id"] for h in self.hotels), scores), key=lambda x: -x[1])
        return [(hid, round(s, 2), "bm25") for hid, s in ranked]

    def sections(self, hotel_id: str, text: str, k: int = 3) -> list[Chunk]:
        chunks = self.chunks[hotel_id]
        scores = BM25([c.text for c in chunks]).scores(text)
        ranked = [c for _, c in sorted(zip(scores, chunks), key=lambda x: -x[0]) if c.section != "identity"]
        return [chunks[0]] + ranked[:k]          # identity chunk always comes first

    def rules(self, text: str, k: int = 2) -> list[Chunk]:
        scores = self._play_bm25.scores(text)
        return [c for s, c in sorted(zip(scores, self.playbook), key=lambda x: -x[0])[:k] if s > 0]


class Retrieved(BaseModel):
    hotel_id: str | None
    hotel_match: str            # how we matched: email domain / name match / bm25 / none
    hotel_margin: float         # bm25 gap between best and second hotel (low = ambiguous)
    chunks: list[Chunk]
    rules: list[Chunk]

    def as_context(self) -> str:
        parts = [f"<existing_contract hotel_id=\"{self.hotel_id}\" matched_by=\"{self.hotel_match}\">"]
        parts += [f"[{c.section}] {c.text}" for c in self.chunks]
        parts.append("</existing_contract>")
        if self.rules:
            parts.append("<playbook>")
            parts += [c.text for c in self.rules]
            parts.append("</playbook>")
        return "\n".join(parts)


def retrieve(store: Store, text: str, k: int = 3, min_margin: float = 1.0, bm25_only: bool = False) -> Retrieved:
    ranked = store.find_hotel(text, bm25_only=bm25_only)
    best_id, best, how = ranked[0]
    margin = best - (ranked[1][1] if len(ranked) > 1 else 0.0)
    if how == "bm25" and (best <= 0 or margin < min_margin):
        return Retrieved(hotel_id=None, hotel_match="none (ambiguous)", hotel_margin=round(margin, 2),
                         chunks=[], rules=store.rules(text))
    return Retrieved(hotel_id=best_id, hotel_match=how, hotel_margin=round(margin, 2),
                     chunks=store.sections(best_id, text, k=k), rules=store.rules(text))
