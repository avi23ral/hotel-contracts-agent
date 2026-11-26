"""Step 7 checks that need no API key: review queue, approval, versions, export, flywheel."""
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.apply import apply_changes  # noqa: E402
from contract_agent.export import export_hotel, offer_rows  # noqa: E402
from contract_agent.orchestrate import load_replays  # noqa: E402
from contract_agent.review import ReviewStore, get_path, parse_value, set_path, split_path  # noqa: E402
from contract_agent.schema import Change, ChangeSet, GoldLabel, SpecialOffer  # noqa: E402

REPLAYS = {p["source_file"][:3]: p for p in load_replays()}
GOLD001 = GoldLabel.model_validate_json((ROOT / "data/gold/001_sol_de_marina_contract.json").read_text()).contract


def test_paths_read_and_write_inside_keyed_lists():
    assert split_path("child_policy[0.5].discount_pct") == [("child_policy", "0.5"), ("discount_pct", None)]
    data = {"changes": [{"change_type": "offer_added", "offer": {"type": "discount"}}]}
    set_path(data, "changes[offer_added|discount].offer.combinable", False)
    assert get_path(data, "changes[offer_added|discount].offer.combinable") is False
    assert parse_value("155") == 155 and parse_value('["HB"]') == ["HB"] and parse_value("Bank transfer") == "Bank transfer"


def test_queue_holds_flagged_and_missing_fields_only():
    rs = ReviewStore(":memory:")
    item = rs.add_item(REPLAYS["003"])
    q = rs.queue(item)
    assert len(q) < len(REPLAYS["003"]["prediction"]["field_meta"])
    assert any(f.status == "missing" and f.path.endswith("offer.combinable") for f in q)


def test_cannot_approve_until_every_queued_field_is_reviewed():
    rs = ReviewStore(":memory:")
    item = rs.add_item(REPLAYS["001"])
    with pytest.raises(ValueError, match="still need review"):
        rs.approve(item)


def _review_all(rs, item, fix=None):
    for f in rs.queue(item):
        if fix and f.path in fix:
            rs.decide(item, f.path, "correct", fix[f.path])
        else:
            rs.decide(item, f.path, "accept")


def test_contract_then_amendment_makes_versions_and_logs_corrections(tmp_path):
    rs = ReviewStore(":memory:")
    a = rs.add_item(REPLAYS["001"])
    _review_all(rs, a, {"payment_terms": "Bank transfer within 30 days of guest departure"})
    assert rs.approve(a)["version"] == 1
    b = rs.add_item(REPLAYS["003"])
    for f in rs.queue(b):
        if f.value is None:
            rs.decide(b, f.path, "correct", False)
        else:
            rs.decide(b, f.path, "accept")
    v = rs.approve(b)
    assert v["version"] == 2 and not v["manual"]
    c = rs.latest("H001")
    assert c.payment_terms.startswith("Bank transfer")
    assert [s.room_codes for s in c.stop_sales] == [["DSV"]]
    eb = next(o for o in c.special_offers if o.type == "early_booking")
    assert str(eb.book_by) == "2027-02-28" and eb.combinable is False
    assert next(o for o in c.special_offers if o.type == "discount").combinable is False
    assert len(rs.corrections()) == 2
    out = export_hotel(rs, "H001", tmp_path)
    assert out["version"] == 2 and out["stop_sales"] == 1
    assert all(r.source_contract_id == "H001-v2" for r in offer_rows(rs, "H001"))


def test_export_refuses_without_an_approved_version():
    rs = ReviewStore(":memory:")
    rs.add_item(REPLAYS["002"])
    with pytest.raises(ValueError, match="no approved contract"):
        offer_rows(rs, "H002")


def test_apply_reprices_whole_seasons_and_hands_back_part_seasons():
    cs = ChangeSet(hotel_name="x", changes=[
        Change(change_type="rate_change", room_codes=["DBL"], boards=["BB"], date_from=date(2027, 7, 15),
               date_to=date(2027, 8, 31), new_price=160),
        Change(change_type="rate_change", room_codes=["DBL"], boards=["HB"], date_from=date(2027, 8, 1),
               date_to=date(2027, 8, 10), new_price=190),
        Change(change_type="offer_ended", offer=SpecialOffer(type="early_booking")),
    ])
    new, done, manual = apply_changes(GOLD001, cs)
    assert next(r.price for r in new.rates if (r.season, r.room_code, r.board) == ("High", "DBL", "BB")) == 160
    assert next(r.price for r in new.rates if (r.season, r.room_code, r.board) == ("High", "DBL", "HB")) == 185
    assert len(manual) == 1 and "part of a season" in manual[0]
    assert not new.special_offers and GOLD001.special_offers      # original untouched: versions, not edits


def test_spend_guard_adds_up():
    rs = ReviewStore(":memory:")
    rs.add_spend(0.01)
    rs.add_spend(0.02)
    assert abs(rs.spent_today() - 0.03) < 1e-9


def test_demo_app_lands_on_samples_and_runs_one_end_to_end():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "app/demo_app.py"), default_timeout=60).run()
    assert not at.exception
    assert any("Pick a message" in s.value for s in at.subheader)       # landing page, no login
    at.button(key="s003_sol_de_marina_chat.txt").click().run()
    assert not at.exception and any("Your turn" in m.value for m in at.markdown)
    next(b for b in at.button if "Accept all" in b.label).click().run()
    at.text_input[-1].input("false").run()
    next(b for b in at.button if b.label == "Save correction" and not b.disabled).click().run()
    next(b for b in at.button if b.label.startswith("Approve")).click().run()
    assert not at.exception and any("contract version 1" in m.value for m in at.markdown)
