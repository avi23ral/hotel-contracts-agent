"""Step 7: the human review queue, contract versions and the correction log.

A reviewer only sees what the agent is unsure about:
- fields whose confidence is below their bar (needs_review / missing)
- fields that should be there but aren't (an offer without stay dates, a
  stop sale without an end date)
Everything else is auto-accepted, with its evidence still one click away.

Every reviewer decision is stored. Corrections ("the model said X, the
human said Y") are the most valuable data the system produces: each one is
a labelled example for the next eval (the data flywheel).

Storage is one SQLite file, which is plenty for a demo and easy to inspect.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .apply import apply_changes
from .scoring import LIST_KEYS, flatten
from .schema import ChangeSet, Contract, ExtractionResult

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "var" / "review.db"


# ---------- reading and writing one field by its path ----------

def split_path(path: str) -> list[tuple[str, str | None]]:
    """'changes[offer_added|discount].offer.stay_from' -> [('changes','offer_added|discount'),('offer',None),('stay_from',None)].
    Dots inside [...] (e.g. child_policy[0.5]) are part of the key."""
    segs, buf, key, depth = [], "", None, 0
    for ch in path + ".":
        if ch == "[" and depth == 0:
            depth, key = 1, ""
        elif ch == "]" and depth == 1:
            depth = 0
        elif depth:
            key += ch
        elif ch == ".":
            segs.append((buf, key))
            buf, key = "", None
        else:
            buf += ch
    return segs


def _node(data: dict, path: str, create: bool = False) -> tuple[dict, str]:
    segs = split_path(path)
    node = data
    for name, key in segs[:-1]:
        if key is None:
            if node.get(name) is None and create:
                node[name] = {}
            node = node[name]
        else:
            node = next(x for x in node.get(name) or [] if LIST_KEYS[name](x) == key)
    name, key = segs[-1]
    if key is not None:
        raise KeyError(f"{path} points at a list item, not a field")
    return node, name


def get_path(data: dict, path: str) -> Any:
    try:
        node, name = _node(data, path)
        return node.get(name)
    except (KeyError, StopIteration, TypeError):
        return None


def set_path(data: dict, path: str, value: Any) -> None:
    node, name = _node(data, path, create=True)
    node[name] = value


def parse_value(text: str) -> Any:
    """What a reviewer types -> a JSON value: 155, true, ["HB"], or plain text."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# ---------- what goes in the queue ----------

class QueueField(BaseModel):
    path: str
    value: Any
    status: str                     # needs_review | missing
    category: str
    confidence: float | None
    threshold: float | None
    evidence: str | None
    reasons: list[str]
    decision: str | None = None     # accept | correct | (None = not reviewed yet)
    final_value: Any = None


def required_missing(pred: dict) -> list[tuple[str, str]]:
    """Fields an amendment must have that the model left out: (path, why)."""
    out = []
    for ch in (pred.get("change_set") or {}).get("changes", []):
        key = LIST_KEYS["changes"](ch)
        if ch["change_type"] in ("stop_sale", "open_sale"):
            for f in ("date_from", "date_to"):
                if ch.get(f) is None:
                    out.append((f"changes[{key}].{f}", "a stop sale needs its dates"))
        offer = ch.get("offer") or {}
        if ch["change_type"] in ("offer_added", "offer_changed"):
            need = ["stay_from", "stay_to", "combinable"]
            if offer.get("type") in ("discount", "early_booking"):
                need.insert(0, "discount_pct")
            for f in need:
                if offer.get(f) is None:
                    out.append((f"changes[{key}].offer.{f}", "an offer needs this to be bookable"))
    return out


def build_queue(pred: dict) -> list[QueueField]:
    res = ExtractionResult.model_validate(pred)
    body = pred.get("contract") or pred.get("change_set") or {}
    flat = flatten(res.contract or res.change_set)
    out = []
    for path, m in res.field_meta.items():
        if m.status.value == "auto_accept":
            continue
        value = flat.get(path, get_path(body, path))
        if path == "hotel_name":
            value = body.get("hotel_name") or (body.get("hotel") or {}).get("name")
        out.append(QueueField(path=path, value=value, status=m.status.value, category=m.category,
                              confidence=m.confidence, threshold=m.threshold, evidence=m.evidence,
                              reasons=m.reasons + m.flags))
    seen = {q.path for q in out}
    for path, why in required_missing(pred):
        if path not in seen:
            out.append(QueueField(path=path, value=None, status="missing", category="other", confidence=None,
                                  threshold=None, evidence=None, reasons=[why]))
    return out


# ---------- the store ----------

SCHEMA = """
create table if not exists items (
  id integer primary key, source_file text, channel text, kind text, hotel_id text,
  status text, created_at real, payload text);
create table if not exists decisions (
  item_id integer, path text, action text, model_value text, final_value text, category text,
  confidence real, reasons text, evidence text, seconds real, at real, primary key (item_id, path));
create table if not exists versions (
  hotel_id text, version integer, contract text, item_id integer, applied text, manual text, at real,
  primary key (hotel_id, version));
create table if not exists spend (day text primary key, usd real);
"""


class ReviewStore:
    def __init__(self, path: Path | str = DEFAULT_DB):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # items
    def add_item(self, payload: dict) -> int:
        """payload: source_file, channel, kind (contract|amendment|ignore), hotel_id, prediction, ..."""
        status = "ignored" if payload.get("kind") == "ignore" else "pending"
        cur = self.db.execute(
            "insert into items (source_file, channel, kind, hotel_id, status, created_at, payload) values (?,?,?,?,?,?,?)",
            (payload["source_file"], payload.get("channel"), payload["kind"], payload.get("hotel_id"), status,
             time.time(), json.dumps(payload, default=str)))
        self.db.commit()
        return cur.lastrowid

    def items(self) -> list[dict]:
        rows = self.db.execute("select id, source_file, kind, hotel_id, status from items order by id").fetchall()
        return [dict(r) for r in rows]

    def payload(self, item_id: int) -> dict:
        return json.loads(self.db.execute("select payload from items where id=?", (item_id,)).fetchone()[0])

    def status(self, item_id: int) -> str:
        return self.db.execute("select status from items where id=?", (item_id,)).fetchone()[0]

    # review
    def queue(self, item_id: int) -> list[QueueField]:
        pred = self.payload(item_id).get("prediction")
        if not pred:
            return []
        q = build_queue(pred)
        done = {r["path"]: r for r in self.db.execute("select * from decisions where item_id=?", (item_id,))}
        for f in q:
            if f.path in done:
                f.decision, f.final_value = done[f.path]["action"], json.loads(done[f.path]["final_value"])
        return q

    def decide(self, item_id: int, path: str, action: str, value: Any = None, seconds: float | None = None) -> None:
        """action: accept (keep the model's value) or correct (use value)."""
        f = next(x for x in self.queue(item_id) if x.path == path)
        final = f.value if action == "accept" else value
        self.db.execute("insert or replace into decisions values (?,?,?,?,?,?,?,?,?,?,?)",
                        (item_id, path, action, json.dumps(f.value), json.dumps(final), f.category, f.confidence,
                         json.dumps(f.reasons), f.evidence, seconds, time.time()))
        self.db.commit()

    def pending(self, item_id: int) -> list[str]:
        return [f.path for f in self.queue(item_id) if f.decision is None]

    def reviewed_prediction(self, item_id: int) -> dict:
        """The model's output with every reviewer correction applied."""
        pred = json.loads(json.dumps(self.payload(item_id)["prediction"]))
        body = pred.get("contract") or pred.get("change_set")
        for r in self.db.execute("select path, action, final_value from decisions where item_id=?", (item_id,)):
            if r["action"] == "correct":
                if r["path"] == "hotel_name":
                    key = "hotel_name" if "hotel_name" in body else None
                    if key:
                        body["hotel_name"] = json.loads(r["final_value"])
                    else:
                        body["hotel"]["name"] = json.loads(r["final_value"])
                else:
                    set_path(body, r["path"], json.loads(r["final_value"]))
        return pred

    def approve(self, item_id: int, base: Contract | None = None) -> dict:
        """Write the reviewed result as a new contract version. Refuses while fields are unreviewed.
        base: the contract an amendment applies to when this hotel has no version yet."""
        left = self.pending(item_id)
        if left:
            raise ValueError(f"{len(left)} fields still need review: {left[:3]}")
        p = self.payload(item_id)
        hid = p.get("hotel_id")
        if not hid:
            raise ValueError("no hotel matched: set the hotel before approving")
        pred = self.reviewed_prediction(item_id)
        applied, manual = [], []
        if p["kind"] == "contract":
            new = Contract.model_validate(pred["contract"])
            new.hotel.hotel_id = hid
            applied = ["full contract loaded"]
        else:
            current = self.latest(hid) or base
            if current is None:
                raise ValueError(f"no existing contract for {hid} to apply the amendment to")
            new, applied, manual = apply_changes(current, ChangeSet.model_validate(pred["change_set"]))
        version = (self.db.execute("select max(version) from versions where hotel_id=?", (hid,)).fetchone()[0] or 0) + 1
        self.db.execute("insert into versions values (?,?,?,?,?,?,?)",
                        (hid, version, new.model_dump_json(), item_id, json.dumps(applied), json.dumps(manual),
                         time.time()))
        self.db.execute("update items set status='approved' where id=?", (item_id,))
        self.db.commit()
        return {"hotel_id": hid, "version": version, "applied": applied, "manual": manual}

    def latest(self, hotel_id: str) -> Contract | None:
        r = self.db.execute("select contract from versions where hotel_id=? order by version desc limit 1",
                            (hotel_id,)).fetchone()
        return Contract.model_validate_json(r[0]) if r else None

    def latest_version(self, hotel_id: str) -> int:
        return self.db.execute("select coalesce(max(version),0) from versions where hotel_id=?",
                               (hotel_id,)).fetchone()[0]

    def versions(self, hotel_id: str) -> list[dict]:
        rows = self.db.execute("select version, item_id, applied, manual, at from versions where hotel_id=? "
                               "order by version", (hotel_id,)).fetchall()
        return [{**dict(r), "applied": json.loads(r["applied"]), "manual": json.loads(r["manual"])} for r in rows]

    # the data flywheel
    def corrections(self) -> list[dict]:
        """Every field a human changed: a ready-made labelled example for the next eval."""
        rows = self.db.execute(
            "select d.*, i.source_file from decisions d join items i on i.id=d.item_id where d.action='correct'")
        return [{"source_file": r["source_file"], "path": r["path"], "category": r["category"],
                 "model_value": json.loads(r["model_value"]), "human_value": json.loads(r["final_value"]),
                 "confidence": r["confidence"], "reasons": json.loads(r["reasons"]), "evidence": r["evidence"]}
                for r in rows]

    def export_corrections(self, path: Path) -> int:
        rows = self.corrections()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows))
        return len(rows)

    # spend guard for the public demo
    def spent_today(self) -> float:
        r = self.db.execute("select usd from spend where day=?", (time.strftime("%Y-%m-%d"),)).fetchone()
        return r[0] if r else 0.0

    def add_spend(self, usd: float) -> None:
        self.db.execute("insert into spend values (?, ?) on conflict(day) do update set usd = usd + excluded.usd",
                        (time.strftime("%Y-%m-%d"), usd))
        self.db.commit()
