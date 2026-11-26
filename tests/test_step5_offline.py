"""Step 5 checks that need no API key: the store and retrieval."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.rag import BM25, Store, retrieve, tokens  # noqa: E402

STORE = Store()
CHAT = (ROOT / "data/inputs/003_sol_de_marina_chat.txt").read_text()


def test_store_has_every_hotel_and_valid_contracts():
    assert len(STORE.hotels) == len(STORE.contracts) == 9
    assert STORE.contracts["H001"].hotel.hotel_id == "H001"


def test_numbers_are_not_search_terms():
    assert tokens("[20/11/2026, 09:15] close 10 to 17 August") == ["close", "august"]


def test_bm25_prefers_the_matching_chunk():
    s = BM25(["early booking discount offer", "cancellation charges", "rooms and allotment"]).scores(
        "extend the early booking please")
    assert s[0] > s[1] and s[0] > s[2]


def test_chat_amendment_gets_hotel_and_offer_section():
    r = retrieve(STORE, CHAT)
    assert r.hotel_id == "H001"
    sections = [c.section for c in r.chunks]
    assert sections[0] == "identity" and "offers" in sections
    assert "<existing_contract" in r.as_context()


def test_decoy_hotel_and_abstain():
    assert retrieve(STORE, "Hola Tom, Marina Sol here. The 15% early booking is extended.").hotel_id == "H009"
    assert retrieve(STORE, "From: x <res@marinasol.example> close DSV 5-9 Aug").hotel_match == "email domain"
    nobody = retrieve(STORE, "Please stop sale Double Room 10-12 June. Thanks")
    assert nobody.hotel_id is None and nobody.chunks == []
