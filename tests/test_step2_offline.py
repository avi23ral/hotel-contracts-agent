"""Step 2 checks that need no API key."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.llm import resolve_api_key  # noqa: E402
from contract_agent.schema import GoldLabel  # noqa: E402
from contract_agent.scoring import score_fields  # noqa: E402

G1 = ROOT / "data/gold/001_sol_de_marina_contract.json"


def test_key_prefers_hotel_agent_var():
    assert resolve_api_key({"HOTEL_AGENT_API_KEY": "a", "ANTHROPIC_API_KEY": "b"}, {}) == "a"


def test_key_falls_back_to_anthropic_var_and_dotenv():
    assert resolve_api_key({"ANTHROPIC_API_KEY": "b"}, {}) == "b"
    assert resolve_api_key({}, {"HOTEL_AGENT_API_KEY": "c"}) == "c"
    assert resolve_api_key({}, {}) is None


def test_gold_vs_itself_is_perfect():
    g = GoldLabel.model_validate_json(G1.read_text())
    s = score_fields(g.contract, g.contract)
    assert s.f1 == 1.0 and len(s.correct) > 100


def test_order_does_not_matter_but_values_do():
    g = GoldLabel.model_validate_json(G1.read_text())
    d = json.loads(G1.read_text())["contract"]
    d["rates"].reverse()
    d["rates"][0]["price"] += 10           # one wrong price
    del d["release_days"]                  # one missing field
    from contract_agent.schema import Contract
    s = score_fields(Contract.model_validate(d), g.contract)
    assert len(s.wrong) == 1 and s.missing == ["release_days"]
