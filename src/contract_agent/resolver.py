"""Step 6: a ReAct resolver for ambiguous fields.

Most of the pipeline is a fixed workflow: classify, route, retrieve, extract,
validate. That's right when the steps are known in advance. Some fields
aren't like that. "The sea-facing doubles": which room code? "+6 on HB in
High": six on top of which price? "Close till end of Feb": which year, and
is it the 28th or the 29th? The steps to answer depend on what each lookup
returns, so here, and only here, we let the model choose its own steps.

ReAct = REason + ACT, in a loop:
    think (one line) -> call a tool -> read the result -> think -> ...
until it submits an answer or flags the field for a human.

Three ways to resolve a field, compared in the eval:
- rules_only: deterministic tool calls in a fixed order (free)
- one_shot:   one model call with the hotel's contract pasted in (step 5 style)
- react:      the model calls tools step by step (this step)
"""
from __future__ import annotations

import json
import re
import time
from typing import Literal

from pydantic import BaseModel

from .llm import FAST_MODEL, get_client
from .rag import Store
from .tools import TOOL_SPECS, Tools

Field = Literal["room", "board", "dates", "new_price", "release_days"]

FIELD_HELP = {
    "room": "the room code this refers to",
    "board": "the board code (RO, BB, HB, FB, AI)",
    "dates": "the stay dates as YYYY-MM-DD..YYYY-MM-DD",
    "new_price": "the new contract price per night as a number, after applying any change",
    "release_days": "the new release period in days",
}


class Question(BaseModel):
    hotel_id: str
    field: Field
    message: str


class Resolution(BaseModel):
    status: Literal["resolved", "flag"]
    value: str | None = None
    reason: str = ""
    tool_calls: int = 0
    trace: list[str] = []
    calls: list[dict] = []          # one entry per model call: tokens, seconds
    seconds: float = 0.0


def ask(q: Question) -> str:
    return (f"Hotel: {q.hotel_id}\nMessage from the hotel: \"{q.message}\"\n"
            f"Field to resolve: {q.field} = {FIELD_HELP[q.field]}.")


# ---------- ReAct ----------

SYSTEM = """You resolve ONE field from a hotel's message against our own records, using tools.

Work in short ReAct steps: write one line saying what you need next, then call a tool.
Rules:
- Every fact about the hotel (rooms, boards, seasons, prices, dates) must come from a tool, not memory.
- Use calculate for every sum, and resolve_date for any date without a year.
- Check the playbook when a house rule might apply.
- Flag (status "flag") instead of guessing when two answers fit equally, the thing doesn't exist at this
  hotel, the date is outside the contract, or a house rule says to flag. A wrong answer costs far more
  than a flag.
- Finish by calling submit exactly once. Keep it short: most fields need 2 to 4 tool calls."""

MAX_STEPS = 8


def _usage(r, model: str, t0: float) -> dict:
    return {"model": model, "input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens,
            "seconds": round(time.time() - t0, 2)}


def react(q: Question, model: str = FAST_MODEL, tools: Tools | None = None, client=None,
          max_steps: int = MAX_STEPS) -> Resolution:
    tools = tools or Tools()
    client = client or get_client()
    messages = [{"role": "user", "content": ask(q)}]
    trace, calls, n_tools = [], [], 0
    t_start = time.time()
    for _ in range(max_steps):
        t0 = time.time()
        r = client.messages.create(model=model, max_tokens=1024, system=SYSTEM, tools=TOOL_SPECS,
                                   messages=messages, output_config={"effort": "low"})
        calls.append(_usage(r, model, t0))
        messages.append({"role": "assistant", "content": r.content})
        results = []
        for b in r.content:
            if b.type == "text" and b.text.strip():
                trace.append("think: " + b.text.strip())
            elif b.type == "tool_use":
                if b.name == "submit":
                    a = b.input
                    trace.append(f"submit: {a.get('status')} {a.get('value')} ({a.get('reason')})")
                    return Resolution(status=a.get("status", "flag"), value=_clean(a.get("value")),
                                      reason=a.get("reason", ""), tool_calls=n_tools, trace=trace,
                                      calls=calls, seconds=round(time.time() - t_start, 2))
                n_tools += 1
                out = tools.call(b.name, b.input)
                trace.append(f"act: {b.name}({json.dumps(b.input)}) -> {json.dumps(out)[:300]}")
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out)})
        if results:
            messages.append({"role": "user", "content": results})
        else:   # answered in prose without submitting: one nudge
            messages.append({"role": "user", "content": "Call submit to finish."})
    trace.append("stop: step limit reached, flagged")
    return Resolution(status="flag", reason="step limit reached", tool_calls=n_tools, trace=trace, calls=calls,
                      seconds=round(time.time() - t_start, 2))


def _clean(v) -> str | None:
    if v is None:
        return None
    v = str(v).strip()
    return None if v.lower() in ("", "null", "none") else v


# ---------- one-shot baseline: the contract pasted in, no tools ----------

class Answer(BaseModel):
    reasoning: str
    status: Literal["resolved", "flag"]
    value: str | None


ONE_SHOT = """You resolve ONE field from a hotel's message against the hotel's existing contract and our
house rules, both given below. Flag (status "flag") instead of guessing when two answers fit equally, the
thing doesn't exist at this hotel, the date is outside the contract, or a house rule says to flag.
Value formats: room code, board code, a price as a number, release days, or dates as YYYY-MM-DD..YYYY-MM-DD."""


def one_shot(q: Question, model: str = FAST_MODEL, store: Store | None = None, client=None) -> Resolution:
    store = store or Store()
    client = client or get_client()
    chunks = store.chunks[q.hotel_id]
    context = "<existing_contract>\n" + "\n".join(f"[{c.section}] {c.text}" for c in chunks) + \
        "\n</existing_contract>\n<playbook>\n" + "\n".join(c.text for c in store.playbook) + "\n</playbook>"
    t0 = time.time()
    r = client.messages.parse(model=model, max_tokens=1024, system=ONE_SHOT, output_format=Answer,
                              messages=[{"role": "user", "content": context + "\n\n" + ask(q)}],
                              output_config={"effort": "low"})
    a = r.parsed_output
    return Resolution(status=a.status, value=_clean(a.value), reason=a.reasoning, calls=[_usage(r, model, t0)],
                      seconds=round(time.time() - t0, 2))


# ---------- rules-only baseline: fixed tool calls, no model ----------

def rules_only(q: Question, tools: Tools | None = None) -> Resolution:
    """What a plain workflow can do with the same tools: room and board lookups
    when they give one clear answer. Anything needing judgement is flagged."""
    tools = tools or Tools()
    if q.field == "room":
        out = tools.lookup_room(q.hotel_id, q.message)
        if out["verdict"].startswith("one best match"):
            return Resolution(status="resolved", value=out["candidates"][0]["code"], reason=out["verdict"],
                              tool_calls=1)
        return Resolution(status="flag", reason=out["verdict"], tool_calls=1)
    if q.field == "board":
        out = tools.lookup_board(q.hotel_id, q.message)
        if out["board_code"] and out["sold_by_hotel"]:
            return Resolution(status="resolved", value=out["board_code"], reason="phrase match", tool_calls=1)
        return Resolution(status="flag", reason="no board match, or board not sold", tool_calls=1)
    return Resolution(status="flag", reason="needs judgement: sent to a human")


# ---------- scoring ----------

def outcome(expected: str, got: Resolution) -> str:
    """correct | correct_flag | wrong | missed_flag | over_flag.
    wrong and missed_flag are the dangerous ones: a confident wrong value."""
    if expected == "flag":
        return "correct_flag" if got.status == "flag" else "missed_flag"
    if got.status == "flag":
        return "over_flag"
    return "correct" if same(expected, got.value) else "wrong"


def same(expected: str, value: str | None) -> bool:
    if value is None:
        return False
    try:
        return abs(float(expected) - float(re.sub(r"[^\d.\-]", "", value))) < 0.01
    except ValueError:
        pass
    norm = lambda s: re.sub(r"\s|\bto\b", "", s.upper()).replace("/", "..")  # noqa: E731
    return norm(expected) == norm(value)


# ---------- hooking it into the pipeline ----------
# The agent is NOT run on every field. Plain code checks the extracted change
# set against the existing contract and only calls the agent for fields that
# fail a check. That keeps the workflow predictable and the cost bounded.

MAX_PER_MESSAGE = 5          # cost cap: at most this many agent runs per message
RESOLVED_CONF, FLAGGED_CONF = 0.9, 0.3


class Trigger(BaseModel):
    index: int               # which change in the change set
    path: str                # field path for field_meta
    field: Field
    why: str


def _key(ch) -> str:
    from .scoring import _change_key
    return f"changes[{_change_key(ch.model_dump(mode='json'))}]"


def find_triggers(change_set, contract) -> list[Trigger]:
    rooms = {r.code for r in contract.rooms}
    boards = {r.board for r in contract.rates}
    out = []
    for i, ch in enumerate(change_set.changes):
        k = _key(ch)
        bad_rooms = [c for c in ch.room_codes if c not in rooms]
        if bad_rooms:
            out.append(Trigger(index=i, path=f"{k}.room_codes", field="room", why=f"unknown room {bad_rooms}"))
        bad_boards = [b for b in ch.boards if b not in boards]
        if bad_boards:
            out.append(Trigger(index=i, path=f"{k}.boards", field="board", why=f"board not sold {bad_boards}"))
        for leaf in ("date_from", "date_to"):
            d = getattr(ch, leaf)
            if d and not (contract.contract_start <= d <= contract.contract_end):
                out.append(Trigger(index=i, path=f"{k}.{leaf}", field="dates", why=f"{leaf} outside contract"))
                break
        if ch.change_type == "rate_change" and ch.new_price is None:
            out.append(Trigger(index=i, path=f"{k}.new_price", field="new_price", why="no new price stated"))
    return out


def _quote(meta: dict, path: str, fallback: str) -> str:
    prefix = path.split("].")[0] + "]"
    quotes = [m.evidence for p, m in meta.items() if p.startswith(prefix) and m.evidence]
    return max(quotes, key=len) if quotes else fallback[:600]


def resolve_change_set(result, hotel_id: str, store: Store, doc_text: str, resolver=None) -> list[dict]:
    """Run the agent on the fields that failed a check, and write its answer back.
    Returns one log entry per agent run (for cost and review)."""
    resolver = resolver or (lambda q: react(q, tools=Tools(store)))
    cs, contract, log = result.change_set, store.contracts[hotel_id], []
    for t in find_triggers(cs, contract)[:MAX_PER_MESSAGE]:
        ch = cs.changes[t.index]
        q = Question(hotel_id=hotel_id, field=t.field, message=_quote(result.field_meta, t.path, doc_text))
        res = resolver(q)
        ok = res.status == "resolved" and res.value is not None and _apply(ch, t.field, res.value)
        from .confidence import THRESHOLDS, category
        from .schema import FieldMeta, Status
        cat = category(t.path)
        conf = RESOLVED_CONF if ok else FLAGGED_CONF
        result.field_meta[t.path] = FieldMeta(
            confidence=conf, status=Status.auto_accept if conf >= THRESHOLDS[cat] else Status.needs_review,
            evidence=q.message, category=cat, threshold=THRESHOLDS[cat], derived=ok,
            reasons=[f"resolver: {res.reason}" if ok else f"resolver flagged: {res.reason}"])
        log.append({"path": t.path, "why": t.why, "status": "resolved" if ok else "flag", "value": res.value,
                    "trace": res.trace, "calls": res.calls})
    return log


def _apply(ch, field: str, value: str) -> bool:
    try:
        if field == "room":
            ch.room_codes = [value.upper()]
        elif field == "board":
            ch.boards = [value.upper()]
        elif field == "dates":
            a, _, b = value.partition("..")
            from datetime import date as _d
            ch.date_from, ch.date_to = _d.fromisoformat(a.strip()), _d.fromisoformat((b or a).strip())
        elif field == "new_price":
            ch.new_price = float(re.sub(r"[^\d.\-]", "", value))
        else:
            return False
        return True
    except ValueError:
        return False
