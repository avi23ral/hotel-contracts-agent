"""Step 6: the resolver's tools.

Each tool is plain, deterministic Python over our own data (the property
master, the loaded contracts and the playbook). The model decides WHICH tool
to call and what to make of the answer; the tools never guess. That split
matters: every fact the agent uses comes from a tool result we can log and
check, not from the model's memory.

Tools return small dicts, which are sent back to the model as JSON.
"""
from __future__ import annotations

import ast
import calendar
import operator
import re
from datetime import date

from .rag import Store, tokens

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS["sept"] = 9

# The hotel's wording -> words our room names use. Deliberately small: the
# agent is there for the cases this list doesn't cover.
ROOM_SYNONYMS = {
    "seaview": ["sea", "view"], "sea-facing": ["sea", "view"], "facing": ["view"],
    "doubles": ["double"], "twin": ["double"], "twins": ["double"], "dbl": ["double"],
    "suites": ["suite"], "juniors": ["junior"], "families": ["family"], "std": ["standard"],
    "rooms": [], "room": [],
}

# Board wording -> code, from the playbook's board rule.
BOARD_PHRASES = [
    (r"\broom only\b|\bno meals\b|\bself.?catering\b", "RO"),
    (r"\ball.?inclusive\b|\ball.?in\b|\bai\b", "AI"),
    (r"\bfull board\b|\ball meals\b|\bfb\b", "FB"),
    (r"\bhalf board\b|\bdinner included\b|\bbreakfast and dinner\b|\bbreakfast & dinner\b|\bhb\b", "HB"),
    (r"\bbed and breakfast\b|\bbreakfast only\b|\bb&b\b|\bbb\b|\bbreakfast\b", "BB"),
]


class Tools:
    def __init__(self, store: Store | None = None):
        self.store = store or Store()

    # ---------- hotel facts ----------

    def _contract(self, hotel_id: str):
        c = self.store.contracts.get(hotel_id)
        if c is None:
            raise KeyError(f"unknown hotel_id {hotel_id!r}")
        return c

    def get_rooms(self, hotel_id: str) -> dict:
        c = self._contract(hotel_id)
        return {"hotel_id": hotel_id, "rooms": [
            {"code": r.code, "name": r.name, "inventory": r.inventory.value, "max_adults": r.max_adults,
             "max_children": r.max_children} for r in c.rooms],
            "boards_sold": sorted({r.board for r in c.rates}), "release_days": c.release_days}

    def lookup_room(self, hotel_id: str, wording: str) -> dict:
        """Score each room by how many of the wording's words its name contains."""
        c = self._contract(hotel_id)
        low = wording.lower()
        exact = [r for r in c.rooms if re.search(rf"\b{re.escape(r.name.lower())}s?\b", low)]
        if len(exact) == 1:     # the hotel used our exact room name: that wins outright
            r = exact[0]
            return {"wording": wording, "candidates": [{"code": r.code, "name": r.name, "matched_words": ["exact name"],
                                                        "score": 99}], "verdict": "one best match (exact name)",
                    "all_rooms": [f"{x.code} = {x.name}" for x in c.rooms]}
        words: set[str] = set()
        for t in re.findall(r"[a-z][a-z-]*", wording.lower()):
            words.update(ROOM_SYNONYMS.get(t, [t.rstrip("s") if len(t) > 4 and t.endswith("s") else t]))
        words -= {"the", "our", "all", "and", "with", "in", "on", "for"}
        cands = []
        for r in c.rooms:
            name = set(tokens(r.name)) | {r.code.lower()}
            hits = sorted(words & name)
            if hits:
                cands.append({"code": r.code, "name": r.name, "matched_words": hits, "score": len(hits)})
        cands.sort(key=lambda x: -x["score"])
        top = [x for x in cands if cands and x["score"] == cands[0]["score"]]
        verdict = ("no room matches" if not cands else
                   "one best match" if len(top) == 1 else f"{len(top)} rooms match equally")
        return {"wording": wording, "candidates": cands, "verdict": verdict,
                "all_rooms": [f"{r.code} = {r.name}" for r in c.rooms]}

    def lookup_board(self, hotel_id: str, wording: str) -> dict:
        c = self._contract(hotel_id)
        sold = sorted({r.board for r in c.rates})
        low = wording.lower()
        code = next((code for pat, code in BOARD_PHRASES if re.search(pat, low)), None)
        return {"wording": wording, "board_code": code, "boards_sold": sold,
                "sold_by_hotel": (code in sold) if code else None}

    def get_seasons(self, hotel_id: str) -> dict:
        c = self._contract(hotel_id)
        return {"contract_start": str(c.contract_start), "contract_end": str(c.contract_end),
                "seasons": [{"name": s.name, "from": str(s.date_from), "to": str(s.date_to),
                             "min_stay_nights": s.min_stay_nights} for s in c.seasons]}

    def get_rate(self, hotel_id: str, room_code: str, board: str, season: str | None = None,
                 date_from: str | None = None, date_to: str | None = None) -> dict:
        """The contract price for a room and board, by season name or by stay dates.
        Dates that cross seasons return one row per season."""
        c = self._contract(hotel_id)
        if season:
            names = [s.name for s in c.seasons if s.name.lower() == season.lower()]
        elif date_from:
            a, b = date.fromisoformat(date_from), date.fromisoformat(date_to or date_from)
            names = [s.name for s in c.seasons if s.date_from <= b and s.date_to >= a]
        else:
            return {"error": "give a season name or date_from"}
        rows = [{"season": r.season, "room_code": r.room_code, "board": r.board, "price": r.price}
                for r in c.rates if r.season in names and r.room_code == room_code.upper()
                and r.board == board.upper()]
        return {"currency": c.currency, "pricing_basis": c.pricing_basis, "seasons": names, "rates": rows,
                "note": "no matching rate" if not rows else
                        "dates cross more than one season" if len(rows) > 1 else "one rate"}

    # ---------- date maths ----------

    def resolve_date(self, hotel_id: str, day: str, month: str) -> dict:
        """A day and month without a year -> a full date, using the contract period.
        day can be a number, 'first' or 'last' (last handles leap years)."""
        c = self._contract(hotel_id)
        m = MONTHS.get(str(month).lower().strip(".")) or (int(month) if str(month).isdigit() else None)
        if not m:
            return {"error": f"unknown month {month!r}"}
        out = []
        for y in sorted({c.contract_start.year, c.contract_end.year}):
            last = calendar.monthrange(y, m)[1]
            d = {"first": 1, "last": last}.get(str(day).lower())
            d = d or int(re.sub(r"\D", "", str(day)) or 0)
            if 1 <= d <= last:
                iso = date(y, m, d)
                out.append({"date": str(iso), "inside_contract": c.contract_start <= iso <= c.contract_end})
        inside = [x for x in out if x["inside_contract"]]
        return {"contract_start": str(c.contract_start), "contract_end": str(c.contract_end),
                "candidates": out, "answer": inside[0]["date"] if len(inside) == 1 else None,
                "note": "inside the contract period" if len(inside) == 1 else
                        "outside the contract period: flag it" if not inside else "ambiguous year"}

    # ---------- arithmetic and rules ----------

    def calculate(self, expression: str) -> dict:
        """Safe arithmetic: numbers, + - * / and brackets. '%' is not allowed: write 0.9 or /100."""
        ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
               ast.USub: operator.neg}

        def ev(n):
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
                return n.value
            if isinstance(n, ast.BinOp) and type(n.op) in ops:
                return ops[type(n.op)](ev(n.left), ev(n.right))
            if isinstance(n, ast.UnaryOp) and type(n.op) in ops:
                return ops[type(n.op)](ev(n.operand))
            raise ValueError("only numbers and + - * / ( )")
        try:
            return {"expression": expression, "result": round(ev(ast.parse(expression, mode="eval").body), 4)}
        except Exception as e:  # noqa: BLE001 - the model gets the error and can retry
            return {"expression": expression, "error": str(e)}

    def search_playbook(self, query: str) -> dict:
        return {"rules": [c.text for c in self.store.rules(query, k=2)]}

    # ---------- dispatch ----------

    def call(self, name: str, args: dict) -> dict:
        fn = getattr(self, name, None)
        if name.startswith("_") or name == "call" or fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}


H = {"type": "string", "description": "Our hotel id, e.g. H001"}
TOOL_SPECS = [
    {"name": "get_rooms", "description": "List the hotel's room codes and names, inventory model "
     "(free_sale / allotment), the boards it sells and its release days.",
     "input_schema": {"type": "object", "properties": {"hotel_id": H}, "required": ["hotel_id"]}},
    {"name": "lookup_room", "description": "Match the hotel's room wording to our room codes. Returns every "
     "candidate with the words it matched, and says if two rooms match equally.",
     "input_schema": {"type": "object", "properties": {"hotel_id": H, "wording": {"type": "string"}},
                      "required": ["hotel_id", "wording"]}},
    {"name": "lookup_board", "description": "Map board wording ('dinner included', 'all-in') to a board code "
     "and say whether this hotel sells that board.",
     "input_schema": {"type": "object", "properties": {"hotel_id": H, "wording": {"type": "string"}},
                      "required": ["hotel_id", "wording"]}},
    {"name": "get_seasons", "description": "The contract period and each season's name and dates.",
     "input_schema": {"type": "object", "properties": {"hotel_id": H}, "required": ["hotel_id"]}},
    {"name": "get_rate", "description": "The current contract price for one room code and board, by season "
     "name or by stay dates (ISO). Dates crossing seasons return several rows.",
     "input_schema": {"type": "object", "properties": {
         "hotel_id": H, "room_code": {"type": "string"}, "board": {"type": "string"},
         "season": {"type": "string"}, "date_from": {"type": "string"}, "date_to": {"type": "string"}},
         "required": ["hotel_id", "room_code", "board"]}},
    {"name": "resolve_date", "description": "Turn a day and month with no year into a full date using the "
     "contract period, and say if it is inside the contract. day: a number, 'first' or 'last'.",
     "input_schema": {"type": "object", "properties": {"hotel_id": H, "day": {"type": "string"},
                                                       "month": {"type": "string"}},
                      "required": ["hotel_id", "day", "month"]}},
    {"name": "calculate", "description": "Exact arithmetic, e.g. '185 + 6' or '198 * 0.9'. Use it for every sum.",
     "input_schema": {"type": "object", "properties": {"expression": {"type": "string"}},
                      "required": ["expression"]}},
    {"name": "search_playbook", "description": "Search our contracting house rules.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "submit", "description": "Finish. status 'resolved' with the value, or 'flag' with the reason "
     "when the answer is ambiguous, missing or breaks a house rule.",
     "input_schema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["resolved", "flag"]},
         "value": {"type": ["string", "null"], "description": "room code, board code, price as a number, "
                   "release days, or dates as YYYY-MM-DD..YYYY-MM-DD. null when flagged."},
         "reason": {"type": "string", "description": "one sentence"}},
         "required": ["status", "value", "reason"]}},
]
