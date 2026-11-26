"""Hotel contract agent: review screen and demo.

    streamlit run app/demo_app.py

Replay mode (default) shows saved runs: no API key, no cost, safe to share.
Live mode runs the agent on pasted or uploaded text. It needs an API key, and
when DEMO_PASSWORD is set it also needs that password. A daily spend cap
(DEMO_DAILY_USD, default $1) applies to everyone.
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
from contract_agent.review import DEFAULT_DB, ReviewStore, parse_value  # noqa: E402

DAILY_CAP = float(os.getenv("DEMO_DAILY_USD", "1.0"))
RUN_ESTIMATE = 0.05          # refuse a live run if it could take today's spend over the cap
MAX_CHARS = 30_000

st.set_page_config(page_title="Hotel contract agent", page_icon="🏨", layout="wide")


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


# ---------------- sidebar: pick an input ----------------

st.sidebar.title("🏨 Hotel contract agent")
st.sidebar.caption("Turns hotel contracts, emails and chats into checked offer records. Synthetic data only.")
mode = st.sidebar.radio("Mode", ["Replay a sample (free)", "Run live on your own text"])

if mode.startswith("Replay"):
    replays = {p["source_file"]: p for p in load_replays()}
    labels = {"001_sol_de_marina_contract.pdf": "PDF contract: Hotel Sol de Marina",
              "002_aegean_breeze_email.eml": "Email thread: Aegean Breeze (rate changed mid-thread)",
              "003_sol_de_marina_chat.txt": "WhatsApp chat: stop sale + two offer changes"}
    pick = st.sidebar.selectbox("Sample input", list(replays), format_func=lambda f: labels.get(f, f))
    if st.sidebar.button("Load sample", type="primary") or st.session_state.get("loaded") is None:
        rs = session_store()
        st.session_state.item = rs.add_item(replays[pick])
        st.session_state.loaded = pick
else:
    has_key = bool(resolve_api_key())
    need_pw = bool(os.getenv("DEMO_PASSWORD"))
    spent = spend_store().spent_today()
    st.sidebar.caption(f"Spend today: ${spent:.3f} of ${DAILY_CAP:.2f} daily cap. Haiku, one pass: about 1 cent a message.")
    if not has_key:
        st.sidebar.warning("Live mode is off: no API key is configured.")
    pw = st.sidebar.text_input("Demo password", type="password") if need_pw else ""
    up = st.sidebar.file_uploader("Upload a PDF, .eml or .txt", type=["pdf", "eml", "txt"])
    pasted = st.sidebar.text_area("…or paste an email or chat", height=160)
    if st.sidebar.button("Run the agent", type="primary", disabled=not has_key):
        if need_pw and pw != os.getenv("DEMO_PASSWORD"):
            st.sidebar.error("Wrong password.")
        elif spent + RUN_ESTIMATE > DAILY_CAP:
            st.sidebar.error("Today's demo budget is used up. Replay mode still works.")
        else:
            if up is not None:
                tmp = Path(tempfile.mkdtemp()) / Path(up.name).name
                tmp.write_bytes(up.getvalue())
                doc = load_source(tmp)
            else:
                doc = SourceDoc(path="pasted.txt", channel="email" if "subject:" in pasted.lower() else "chat",
                                text=pasted)
            if not doc.text.strip():
                st.sidebar.error("Nothing to read.")
            elif len(doc.text) > MAX_CHARS:
                st.sidebar.error(f"Too long for the demo ({len(doc.text):,} characters, max {MAX_CHARS:,}).")
            else:
                with st.spinner("Classifying, retrieving, extracting, checking…"):
                    payload = run_live(doc, store=master_store())
                spend_store().add_spend(payload["usd"])
                st.session_state.item = session_store().add_item(payload)
                st.session_state.loaded = doc.path

rs = session_store()
item = st.session_state.get("item")
if not item:
    st.info("Pick a sample or run on your own text from the sidebar.")
    st.stop()
p = rs.payload(item)

# ---------------- 1. what came in ----------------

st.header(f"{p['source_file']}")
hotel = master_store().contracts.get(p.get("hotel_id"))
st.markdown(
    f"**Intent:** {', '.join(p['intent']['labels']) or '—'} &nbsp;→&nbsp; **Route:** {' + '.join(p['routes'])} "
    f"&nbsp;→&nbsp; **Hotel:** {hotel.hotel.name + ' (' + p['hotel_id'] + ')' if hotel else 'not matched'}, "
    f"by {p.get('hotel_match')} &nbsp;·&nbsp; **Cost:** ${p.get('usd', 0):.4f}"
    + (" (saved run, free to replay)" if p.get("mode") == "replay" else " (live run)"))
with st.expander("The original message", expanded=False):
    st.text(p["source_text"][:20000])
if p.get("retrieved") and p["retrieved"].get("chunks"):
    with st.expander("What it looked up: the existing contract and house rules (RAG)"):
        for ch in p["retrieved"]["chunks"]:
            st.markdown(f"**{ch['section']}**: {ch['text']}")
        for ch in p["retrieved"].get("rules", []):
            st.markdown(ch["text"])

if p["kind"] == "ignore" or not p.get("prediction"):
    st.success("Not a contract or a change: nothing to extract. It would be filed for a human, at no extraction cost.")
    st.stop()

# ---------------- 2. everything it extracted ----------------

meta = p["prediction"]["field_meta"]
queue = rs.queue(item)
n_auto = sum(m["status"] == "auto_accept" for m in meta.values())
st.subheader("1 · What it extracted")
a, b, c = st.columns(3)
a.metric("Fields extracted", len(meta))
b.metric(f"Auto-accepted ({100 * n_auto / max(len(meta), 1):.0f}%)", n_auto)
c.metric(f"Sent to you (about {max(1, round(len(queue) * 30 / 60))} min)", len(queue))
with st.expander("All fields, with confidence and source quotes"):
    df = pd.DataFrame([{"field": k, "status": m["status"], "confidence": m["confidence"], "bar": m.get("threshold"),
                        "evidence": m.get("evidence"), "why lowered": "; ".join(m.get("reasons", []))}
                       for k, m in meta.items()])
    st.dataframe(df, use_container_width=True, hide_index=True)
issues = p["prediction"].get("issues", [])
if issues:
    with st.expander(f"Rule checks that fired ({len(issues)})"):
        for i in issues:
            st.markdown(f"- **{i['severity']}** `{i['rule']}`: {i['message']}")

# ---------------- 3. review ----------------

st.subheader("2 · Review: only what the agent is unsure about")
done = sum(f.decision is not None for f in queue)
st.progress(done / max(len(queue), 1), text=f"{done} of {len(queue)} reviewed")
status = rs.status(item)
for f in queue:
    with st.container(border=True):
        left, right = st.columns([3, 2])
        tag = "🟠 missing" if f.status == "missing" else f"🟡 {f.confidence:.2f} (needs {f.threshold:.2f})"
        left.markdown(f"**`{f.path}`** · {f.category} · {tag}")
        left.markdown(f"Agent's value: **{fmt(f.value)}**")
        if f.evidence:
            left.caption(f"Source: “{f.evidence}”")
        if f.reasons:
            left.caption("Why it's here: " + "; ".join(f.reasons))
        if f.decision:
            right.success(f"{'Accepted' if f.decision == 'accept' else 'Corrected to'} {fmt(f.final_value)}")
        if status == "pending" and f.decision is None:
            k = f"{item}:{f.path}"
            new = right.text_input("Correct value", key="v" + k, placeholder="e.g. 155, false, [\"HB\"], 2027-08-17",
                                   label_visibility="collapsed")
            b1, b2 = right.columns(2)
            if b1.button("Accept", key="a" + k, disabled=f.value is None):
                rs.decide(item, f.path, "accept")
                st.rerun()
            if b2.button("Save correction", key="c" + k, disabled=not new.strip()):
                rs.decide(item, f.path, "correct", parse_value(new))
                st.rerun()

if status == "pending":
    left_ = rs.pending(item)
    if st.button("Approve and create a new contract version", type="primary", disabled=bool(left_)):
        hid = p.get("hotel_id")
        try:
            base = master_store().contracts.get(hid) if hid else None
            st.session_state[f"approved{item}"] = rs.approve(item, base=base)
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    if left_:
        st.caption(f"{len(left_)} fields left before you can approve.")

# ---------------- 4. offer file ----------------

if rs.status(item) == "approved":
    hid = p["hotel_id"]
    info = st.session_state.get(f"approved{item}") or {"version": rs.latest_version(hid), "applied": [], "manual": []}
    st.subheader(f"3 · Offer file: {hid}, contract version {info['version']}")
    for x in info["applied"]:
        st.markdown(f"✅ {x}")
    for x in info["manual"]:
        st.markdown(f"✋ needs doing by hand: {x}")
    rows = offer_rows(rs, hid)
    df = pd.DataFrame([r.model_dump() for r in rows])
    st.caption(f"{len(rows)} offer rows, built from the approved version only.")
    st.dataframe(df, use_container_width=True, hide_index=True, height=300)
    d1, d2 = st.columns(2)
    d1.download_button("Download CSV", df.to_csv(index=False), f"offers_{hid}_v{info['version']}.csv", "text/csv")
    d2.download_button("Download JSONL", "".join(r.model_dump_json() + "\n" for r in rows),
                       f"offers_{hid}_v{info['version']}.jsonl", "application/json")
    c = rs.latest(hid)
    if c.stop_sales:
        st.markdown("**Stop sales (availability file)**")
        st.dataframe(pd.DataFrame([s.model_dump(mode="json") for s in c.stop_sales]), hide_index=True)

corr = rs.corrections()
if corr:
    with st.expander(f"Correction log: {len(corr)} new labelled example{'s' if len(corr) != 1 else ''} for the next eval"):
        st.dataframe(pd.DataFrame(corr)[["path", "model_value", "human_value", "reasons"]], hide_index=True)
