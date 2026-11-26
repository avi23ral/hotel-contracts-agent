"""Hotel contract agent: review screen and demo.

    streamlit run app/demo_app.py

It opens on a guided demo with one-click samples. These replay saved runs:
no sign-in, no API key, no cost, so the link is safe to share.
Live mode (your own text) is tucked away below the samples. It needs an API
key, and when DEMO_PASSWORD is set it also needs that password. A daily
spend cap (DEMO_DAILY_USD, default $1) applies to everyone.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Streamlit Community Cloud keeps keys in "secrets": copy them into the environment.
try:
    for k in ("HOTEL_AGENT_API_KEY", "DEMO_PASSWORD", "DEMO_DAILY_USD"):
        if k in st.secrets:
            os.environ.setdefault(k, str(st.secrets[k]))
except Exception:  # noqa: BLE001 - no secrets file locally is fine
    pass

from contract_agent.export import offer_rows  # noqa: E402
from contract_agent.ingest import SourceDoc, load_source  # noqa: E402
from contract_agent.llm import resolve_api_key  # noqa: E402
from contract_agent.orchestrate import load_replays, run_live  # noqa: E402
from contract_agent.rag import Store  # noqa: E402
from contract_agent.review import DEFAULT_DB, ReviewStore, parse_value, split_path  # noqa: E402

DAILY_CAP = float(os.getenv("DEMO_DAILY_USD", "1.0"))
RUN_ESTIMATE = 0.05          # refuse a live run if it could take today's spend over the cap
MAX_CHARS = 30_000
REPO = "https://github.com/avi23ral/hotel-contracts-agent"

SAMPLES = {
    "003_sol_de_marina_chat.txt": ("💬 WhatsApp chat", "A hotel closes its sea view rooms for a wedding, "
                                   "extends an offer and adds a new one, all in one chat.", "Quickest: 11 fields to check"),
    "002_aegean_breeze_email.eml": ("✉️ Email thread", "A full contract agreed over email, where the price "
                                    "changes halfway through the thread.", "27 fields to check"),
    "001_sol_de_marina_contract.pdf": ("📄 PDF contract", "A 3-page hotel contract: rooms, seasons, 36 prices, "
                                       "child policy, cancellation terms.", "22 fields to check"),
}
# Hints for the sample's missing fields, so a first-time visitor isn't stuck
HINTS = {"changes[offer_added|discount].offer.combinable":
         "The chat says “can't combine with the early booking”, so type false and save."}

st.set_page_config(page_title="Hotel contract agent: demo", page_icon="🏨", layout="wide",
                   initial_sidebar_state="collapsed")


@st.cache_resource
def master_store() -> Store:
    return Store()


@st.cache_resource
def spend_store() -> ReviewStore:
    return ReviewStore(DEFAULT_DB)       # shared across visitors: only the spend table is used


def session_store() -> ReviewStore:
    if "rs" not in st.session_state:     # each visitor gets their own review queue
        st.session_state.rs = ReviewStore(":memory:")
    return st.session_state.rs


def fmt(v) -> str:
    return "—" if v is None else json.dumps(v) if isinstance(v, (list, dict, bool)) else str(v)


def pretty(path: str) -> str:
    """'changes[stop_sale|2027-08-10].date_from' -> 'Stop sale (2027-08-10) › date from'."""
    out = []
    for name, key in split_path(path):
        if name == "changes" and key:
            kind, _, rest = key.partition("|")
            out.append(f"{kind.replace('_', ' ').capitalize()} ({rest.replace('_', ' ')})")
        elif key:
            out.append(f"{name.replace('_', ' ').capitalize()} ({key.replace('|', ' · ')})")
        else:
            out.append(name.replace("_", " "))
    return " › ".join(out)


def load_sample(name: str) -> None:
    replays = {p["source_file"]: p for p in load_replays()}
    st.session_state.item = session_store().add_item(replays[name])


def live_panel() -> None:
    has_key = bool(resolve_api_key())
    if not has_key:
        st.caption("Live mode is switched off on this copy of the demo. The samples above show real saved runs.")
        return
    need_pw = bool(os.getenv("DEMO_PASSWORD"))
    spent = spend_store().spent_today()
    st.caption(f"Runs the real agent (small model, one pass, about 1 cent a message). "
               f"Today: ${spent:.2f} of ${DAILY_CAP:.2f} budget.")
    pw = st.text_input("Demo password", type="password") if need_pw else ""
    up = st.file_uploader("Upload a PDF, .eml or .txt", type=["pdf", "eml", "txt"])
    pasted = st.text_area("…or paste an email or chat from a hotel", height=140)
    if st.button("Run the agent"):
        if need_pw and pw != os.getenv("DEMO_PASSWORD"):
            st.error("Wrong password.")
            return
        if spent + RUN_ESTIMATE > DAILY_CAP:
            st.error("Today's demo budget is used up. The samples still work.")
            return
        if up is not None:
            tmp = Path(tempfile.mkdtemp()) / Path(up.name).name
            tmp.write_bytes(up.getvalue())
            doc = load_source(tmp)
        else:
            doc = SourceDoc(path="pasted.txt", channel="email" if "subject:" in pasted.lower() else "chat", text=pasted)
        if not doc.text.strip():
            st.error("Nothing to read.")
        elif len(doc.text) > MAX_CHARS:
            st.error(f"Too long for the demo ({len(doc.text):,} characters, max {MAX_CHARS:,}).")
        else:
            with st.spinner("Reading, classifying, looking up the hotel, extracting, checking…"):
                payload = run_live(doc, store=master_store())
            spend_store().add_spend(payload["usd"])
            st.session_state.item = session_store().add_item(payload)
            st.rerun()


# ======================================================================

st.title("🏨 Hotel contract agent")
st.markdown("Hotels send contracts and changes as PDFs, emails and WhatsApp messages. This agent turns them into "
            "the offer records holidays are sold from, and shows a person **only the fields it isn't sure about**. "
            "All data is made up for the demo.")
tab_demo, tab_results, tab_how = st.tabs(["▶️ Try it", "📊 How well it works", "🧩 How it's built"])

with tab_results:
    st.markdown((ROOT / "docs" / "results.md").read_text())

with tab_how:
    st.markdown(f"""
**The flow.** A plain, predictable workflow, with an AI agent only where the steps can't be known in advance.

```
message ─▶ 1 what is it? (small model)  ─▶ 2 route: new contract / rate & availability change / offer change / ignore
        ─▶ 3 look up the hotel's existing contract and house rules (retrieval)
        ─▶ 4 extract every field, with a source quote for each
        ─▶ 5 check: is the quote real? do the business rules pass? do two runs agree?  → confidence per field
        ─▶ 6 agent with tools for fields that fail a check (room names, price changes, dates)
        ─▶ 7 a person reviews only the unsure fields ─▶ new contract version ─▶ offer file
        ─▶ 8 every correction is saved as new test data
```

**Built with:** Python, the Claude API (a small model for routing and most extraction, a large one where it pays off),
Pydantic, SQLite and Streamlit. Code, evals and concept notes: [{REPO.split('/', 3)[3]}]({REPO}).
""")

with tab_demo:
    rs = session_store()
    item = st.session_state.get("item")

    if not item:
        st.subheader("Pick a message a hotel might send")
        st.caption("Each one replays a real run of the agent. Free, no sign-in, about 3 minutes.")
        cols = st.columns(3)
        for col, (name, (title, desc, effort)) in zip(cols, SAMPLES.items()):
            with col.container(border=True):
                st.markdown(f"### {title}")
                st.write(desc)
                st.caption(effort)
                if st.button("Try this one", key="s" + name, type="primary", use_container_width=True):
                    load_sample(name)
                    st.rerun()
        with st.expander("Or try your own text (live)"):
            live_panel()
        st.stop()

    p = rs.payload(item)
    top_l, top_r = st.columns([4, 1])
    if top_r.button("← Pick another sample", use_container_width=True):
        st.session_state.item = None
        st.rerun()
    top_l.subheader(SAMPLES.get(p["source_file"], (p["source_file"],))[0])

    # ---- step 1: what came in and what the agent understood
    st.markdown("#### ① What came in, and what the agent understood")
    hotel = master_store().contracts.get(p.get("hotel_id"))
    labels = {"new_contract": "a new contract", "ari_update": "a rate or availability change",
              "promotion": "an offer change", "not_a_contract": "not a contract"}
    st.markdown(
        f"- **What it is:** {' and '.join(labels.get(x, x) for x in p['intent']['labels']) or '—'}\n"
        f"- **Which hotel:** {hotel.hotel.name + ' (' + p['hotel_id'] + ')' if hotel else 'not matched: a person picks it'}"
        f", matched by {p.get('hotel_match')}\n"
        f"- **What it cost to process:** ${p.get('usd', 0):.4f}"
        + (" (a saved run, so free to replay)" if p.get("mode") == "replay" else ""))
    with st.expander("Show the original message"):
        st.text(p["source_text"][:20000])
    if p.get("retrieved") and p["retrieved"].get("chunks"):
        with st.expander("What it looked up first: the hotel's existing contract and our house rules"):
            for ch in p["retrieved"]["chunks"]:
                st.markdown(f"**{ch['section']}**: {ch['text']}")
            for ch in p["retrieved"].get("rules", []):
                st.markdown(ch["text"])

    if p["kind"] == "ignore" or not p.get("prediction"):
        st.success("Not a contract or a change, so there's nothing to extract. It would be filed for a person.")
        st.stop()

    # ---- step 2: everything it extracted
    meta = p["prediction"]["field_meta"]
    queue = rs.queue(item)
    n_auto = sum(m["status"] == "auto_accept" for m in meta.values())
    st.markdown("#### ② What it extracted")
    a, b, c = st.columns(3)
    a.metric("Fields extracted", len(meta))
    b.metric(f"Auto-accepted ({100 * n_auto / max(len(meta), 1):.0f}%)", n_auto)
    c.metric(f"Sent to you (about {max(1, round(len(queue) * 30 / 60))} min)", len(queue))
    st.caption("A field is auto-accepted only when its quote is really in the message, the business rules pass, "
               "and two independent runs agree. Prices and cancellation terms have the strictest bar.")
    with st.expander("See all fields, with confidence and source quotes"):
        df = pd.DataFrame([{"field": pretty(k), "status": m["status"].replace("_", " "),
                            "confidence": m["confidence"], "needs": m.get("threshold"),
                            "source quote": m.get("evidence"), "why lowered": "; ".join(m.get("reasons", []))}
                           for k, m in meta.items()])
        st.dataframe(df, use_container_width=True, hide_index=True)

    # ---- step 3: review
    st.markdown("#### ③ Your turn: check what the agent wasn't sure about")
    done = sum(f.decision is not None for f in queue)
    st.progress(done / max(len(queue), 1), text=f"{done} of {len(queue)} checked")
    status = rs.status(item)
    if status == "pending" and any(f.decision is None and f.value is not None for f in queue):
        if st.button("Accept all the agent's values (quick demo)"):
            for f in queue:
                if f.decision is None and f.value is not None:
                    rs.decide(item, f.path, "accept")
            st.rerun()
    for f in queue:
        with st.container(border=True):
            left, right = st.columns([3, 2])
            tag = "🟠 missing" if f.status == "missing" else f"🟡 confidence {f.confidence:.2f}, needs {f.threshold:.2f}"
            left.markdown(f"**{pretty(f.path)}** · {tag}")
            left.markdown(f"Agent's value: **{fmt(f.value)}**")
            if f.evidence:
                left.caption(f"Source: “{f.evidence}”")
            if f.reasons:
                left.caption("Why you're seeing it: " + "; ".join(f.reasons))
            if f.decision:
                right.success(f"{'Accepted' if f.decision == 'accept' else 'Corrected to'} {fmt(f.final_value)}")
            elif status == "pending":
                if f.path in HINTS:
                    right.info(HINTS[f.path])
                k = f"{item}:{f.path}"
                new = right.text_input("Correct value", key="v" + k, label_visibility="collapsed",
                                       placeholder="Type the right value, e.g. 155, false, 2027-08-17")
                b1, b2 = right.columns(2)
                if b1.button("Accept", key="a" + k, disabled=f.value is None):
                    rs.decide(item, f.path, "accept")
                    st.rerun()
                if b2.button("Save correction", key="c" + k, disabled=not new.strip()):
                    rs.decide(item, f.path, "correct", parse_value(new))
                    st.rerun()

    if status == "pending":
        left_ = rs.pending(item)
        if st.button("Approve and update the contract", type="primary", disabled=bool(left_)):
            hid = p.get("hotel_id")
            try:
                base = master_store().contracts.get(hid) if hid else None
                st.session_state[f"approved{item}"] = rs.approve(item, base=base)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        if left_:
            st.caption(f"{len(left_)} left to check before you can approve.")

    # ---- step 4: offer file
    if rs.status(item) == "approved":
        hid = p["hotel_id"]
        info = st.session_state.get(f"approved{item}") or {"version": rs.latest_version(hid), "applied": [], "manual": []}
        st.markdown(f"#### ④ The result: contract version {info['version']} and the offer file")
        if not st.session_state.get(f"celebrated{item}"):
            st.balloons()
            st.session_state[f"celebrated{item}"] = True
        for x in info["applied"]:
            st.markdown(f"✅ {x}")
        for x in info["manual"]:
            st.markdown(f"✋ needs doing by hand: {x}")
        rows = offer_rows(rs, hid)
        df = pd.DataFrame([r.model_dump() for r in rows])
        st.caption(f"{len(rows)} offer rows (room × board × guests × dates), built only from the approved version. "
                   "This is what holidays are priced from.")
        st.dataframe(df, use_container_width=True, hide_index=True, height=300)
        d1, d2 = st.columns(2)
        d1.download_button("Download the offer file (CSV)", df.to_csv(index=False),
                           f"offers_{hid}_v{info['version']}.csv", "text/csv")
        d2.download_button("Download as JSONL", "".join(r.model_dump_json() + "\n" for r in rows),
                           f"offers_{hid}_v{info['version']}.jsonl", "application/json")
        c = rs.latest(hid)
        if c.stop_sales:
            st.markdown("**Closed dates (sent as a separate availability file)**")
            st.dataframe(pd.DataFrame([s.model_dump(mode="json") for s in c.stop_sales]), hide_index=True)
        corr = rs.corrections()
        if corr:
            st.info(f"Your {len(corr)} correction{'s' if len(corr) != 1 else ''} {'were' if len(corr) != 1 else 'was'} "
                    "saved as new test data. That's how the agent's checks get better with use.")
        st.markdown("Next: pick another sample above, or open **📊 How well it works**.")
