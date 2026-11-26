"""Step 5 retrieval eval. No model calls, so it's free and instant.

Usage: python evals/run_retrieval_eval.py [--k 3]
For each message: did we pick the right hotel (or correctly refuse to guess),
and are the contract sections it needs in the top k?
Separating retrieval from generation tells you which half broke.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.ingest import load_source  # noqa: E402
from contract_agent.rag import Store, retrieve  # noqa: E402


def load_cases():
    intents = {}
    for line in (ROOT / "data/intents/messages.jsonl").read_text().splitlines():
        m = json.loads(line)
        intents[m["id"]] = m.get("text") or load_source(ROOT / "data/inputs" / m["file"]).text
    for line in (ROOT / "data/rag/retrieval_eval.jsonl").read_text().splitlines():
        c = json.loads(line)
        c["text"] = c.get("text") or intents[c["intent_id"]]
        yield c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--bm25-only", action="store_true", help="hotel by text score only (ablation)")
    args = ap.parse_args()
    store = Store()
    rows = []
    for c in load_cases():
        r = retrieve(store, c["text"], k=args.k, bm25_only=args.bm25_only)
        got = [ch.section for ch in r.chunks if ch.section != "identity"]
        hotel_ok = r.hotel_id == c["hotel_id"]
        sec_ok = None if not c["sections"] or not hotel_ok else all(s in got for s in c["sections"])
        rows.append({"id": c["id"], "gold_hotel": c["hotel_id"], "got_hotel": r.hotel_id, "how": r.hotel_match,
                     "hotel_ok": hotel_ok, "wrong_hotel": r.hotel_id is not None and not hotel_ok,
                     "gold_sections": c["sections"], "got_sections": got, "sections_ok": sec_ok,
                     "rules": [x.section for x in r.rules]})
    n = len(rows)
    sec = [r for r in rows if r["sections_ok"] is not None]
    summary = {
        "cases": n, "k": args.k, "bm25_only": args.bm25_only,
        "hotel_accuracy": round(sum(r["hotel_ok"] for r in rows) / n, 3),
        "wrong_hotel": sum(r["wrong_hotel"] for r in rows),
        "abstained": sum(r["got_hotel"] is None for r in rows),
        "section_recall_at_k": round(sum(r["sections_ok"] for r in sec) / len(sec), 3) if sec else None,
        "matched_by": {h: sum(r["how"] == h for r in rows) for h in sorted({r["how"] for r in rows})},
    }
    for r in rows:
        if not r["hotel_ok"] or r["sections_ok"] is False:
            print(f"MISS {r['id']}: hotel {r['got_hotel']} ({r['how']}) vs {r['gold_hotel']}; "
                  f"sections {r['got_sections']} vs {r['gold_sections']}")
    print(json.dumps(summary, indent=2))
    out = ROOT / "evals/results" / f"retrieval_k{args.k}{'_bm25only' if args.bm25_only else ''}.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(f"saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
