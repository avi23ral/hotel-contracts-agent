"""Step 6 checks that need no API key: the tools, scoring and the pipeline hook."""
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.rag import Store  # noqa: E402
from contract_agent.resolver import (Question, Resolution, find_triggers, outcome, react,  # noqa: E402
                                     resolve_change_set, rules_only, same)
from contract_agent.schema import Change, ChangeSet, ExtractionResult, Intent  # noqa: E402
from contract_agent.tools import Tools  # noqa: E402

STORE = Store()
T = Tools(STORE)


def test_room_lookup_flags_two_equal_matches():
    assert T.lookup_room("H001", "sea-facing doubles")["candidates"][0]["code"] == "DSV"
    assert T.lookup_room("H001", "doubles")["verdict"] == "2 rooms match equally"
    assert T.lookup_room("H003", "the bungalows")["verdict"] == "no room matches"


def test_exact_room_name_wins():
    # the first eval run's only miss: "Double Room" tied with "Double Sea View"
    out = T.lookup_room("H004", "all-inclusive Double Room in August")
    assert out["candidates"][0]["code"] == "DBL" and out["verdict"].startswith("one best match")


def test_board_lookup_knows_what_the_hotel_sells():
    assert T.lookup_board("H001", "dinner included")["board_code"] == "HB"
    assert T.lookup_board("H005", "breakfast only")["sold_by_hotel"] is False


def test_dates_use_the_contract_year_and_leap_years():
    assert T.resolve_date("H007", "last", "Feb")["answer"] == "2028-02-29"
    assert T.resolve_date("H007", "28", "Dec")["answer"] == "2027-12-28"
    assert T.resolve_date("H001", "3", "Nov")["answer"] is None


def test_rate_across_seasons_returns_both():
    out = T.get_rate("H001", "FAM", "BB", date_from="2027-07-01", date_to="2027-07-31")
    assert [r["price"] for r in out["rates"]] == [185.0, 240.0]


def test_calculate_is_safe():
    assert T.calculate("198 * 0.9")["result"] == 178.2
    assert "error" in T.calculate("__import__('os')")


def test_outcomes_and_value_matching():
    assert same("178.2", "EUR 178.20") and same("2028-02-02..2028-02-05", "2028-02-02 .. 2028-02-05")
    assert outcome("flag", Resolution(status="resolved", value="BB")) == "missed_flag"
    assert outcome("DSV", Resolution(status="flag")) == "over_flag"


def test_rules_baseline_never_answers_prices():
    q = Question(hotel_id="H001", field="new_price", message="+6 on HB sea view doubles in High")
    assert rules_only(q, T).status == "flag"


def test_eval_cases_are_valid():
    cases = [json.loads(x) for x in (ROOT / "data/resolver/cases.jsonl").read_text().splitlines() if x]
    assert len(cases) == 28 and {c["hotel_id"] for c in cases} <= set(STORE.contracts)
    for c in cases:
        Question(hotel_id=c["hotel_id"], field=c["field"], message=c["message"])


class FakeClient:
    """Plays back a scripted ReAct run, so the loop is tested without an API call."""
    def __init__(self, turns):
        self.turns, self.messages = list(turns), self

    def create(self, **kw):
        content = self.turns.pop(0)
        return SimpleNamespace(content=content, usage=SimpleNamespace(input_tokens=100, output_tokens=10))


def _tool(name, args, id_="t1"):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=id_)


def test_react_loop_calls_tools_then_submits():
    fake = FakeClient([
        [SimpleNamespace(type="text", text="Need the High HB price for DSV."),
         _tool("get_rate", {"hotel_id": "H001", "room_code": "DSV", "board": "HB", "season": "High"})],
        [_tool("calculate", {"expression": "200 + 6"}, "t2")],
        [_tool("submit", {"status": "resolved", "value": "206", "reason": "200 + 6"}, "t3")],
    ])
    r = react(Question(hotel_id="H001", field="new_price", message="+6"), tools=T, client=fake)
    assert (r.status, r.value, r.tool_calls, len(r.calls)) == ("resolved", "206", 2, 3)
    assert any('"price": 200.0' in step for step in r.trace)


def test_react_flags_at_step_limit():
    fake = FakeClient([[_tool("get_rooms", {"hotel_id": "H001"}, f"t{i}")] for i in range(3)])
    r = react(Question(hotel_id="H001", field="room", message="x"), tools=T, client=fake, max_steps=3)
    assert r.status == "flag" and "step limit" in r.reason


def test_pipeline_hook_only_runs_on_failed_checks():
    cs = ChangeSet(hotel_name="Hotel Sol de Marina", changes=[
        Change(change_type="stop_sale", room_codes=["DSV"], date_from=date(2027, 8, 10), date_to=date(2027, 8, 12)),
        Change(change_type="rate_change", room_codes=["SEA"], boards=["HB"], date_from=date(2027, 7, 20),
               date_to=date(2027, 8, 31)),
    ])
    triggers = find_triggers(cs, STORE.contracts["H001"])
    assert [(t.index, t.field) for t in triggers] == [(1, "room"), (1, "new_price")]

    answers = {"room": Resolution(status="resolved", value="DSV", reason="sea view double"),
               "new_price": Resolution(status="flag", reason="no delta stated")}
    res = ExtractionResult(source_file="x", intent=Intent.amendment, change_set=cs)
    log = resolve_change_set(res, "H001", STORE, "doc text", resolver=lambda q: answers[q.field])
    assert cs.changes[1].room_codes == ["DSV"] and cs.changes[1].new_price is None
    assert [e["status"] for e in log] == ["resolved", "flag"]
    price_meta = next(m for p, m in res.field_meta.items() if p.endswith("new_price"))
    assert price_meta.status.value == "needs_review"
