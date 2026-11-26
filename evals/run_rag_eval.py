"""Step 5 end-to-end check: the chat amendment (003), with and without RAG.

Usage: python evals/run_rag_eval.py [--model claude-haiku-5-5] [--passes 1]
Cheap by design: small model, one extraction pass, one input. It routes the
message (intent on Haiku), extracts each path with and without the retrieved
contract, scores both against gold and prints what it cost.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.ingest import load_source  # noqa: E402
from contract_agent.llm import FAST_MODEL  # noqa: E402
from contract_agent.rag import Store  # noqa: E402
from contract_agent.router import route_doc  # noqa: E402
from contract_agent.schema import GoldLabel, Status  # noqa: E402
from contract_agent.scoring import score_fields  # noqa: E402

PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}   # $ per 1M in/out


def usd(calls):
    return sum((c["input_tokens"] * PRICES[c["model"]][0] + c["output_tokens"] * PRICES[c["model"]][1]) / 1e6
               for c in calls)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=FAST_MODEL)
    ap.add_argument("--passes", type=int, default=1)
    args = ap.parse_args()
    gold = GoldLabel.model_validate_json((ROOT / "data/gold/003_sol_de_marina_chat.json").read_text())
    doc = load_source(ROOT / "data/inputs" / gold.source_file)
    store = Store()
    out, total = [], 0.0
    for label, st in (("without RAG", None), ("with RAG", store)):
        r = route_doc(doc, model=args.model, store=st, passes=args.passes)
        s = score_fields(r.result.change_set if r.result else None, gold.change_set)
        cost = usd(r.calls)
        total += cost
        meta = r.result.field_meta if r.result else {}
        flagged = sum(m.status != Status.auto_accept for m in meta.values())
        print(f"\n{label}: routes={r.routes} F1={s.f1:.2f} P={s.precision:.2f} R={s.recall:.2f} "
              f"wrong={len(s.wrong)} missing={len(s.missing)} extra={len(s.extra)} "
              f"flagged={flagged}/{len(meta)} ${cost:.4f} {r.seconds}s")
        if r.retrieved:
            print(f"    retrieved hotel {r.retrieved.hotel_id} by {r.retrieved.hotel_match}; sections "
                  f"{[c.section for c in r.retrieved.chunks]}; rules {[c.section for c in r.retrieved.rules]}")
        for p, pv, gv in s.wrong:
            print(f"    WRONG {p}: got {pv!r}, expected {gv!r}  (status {meta[p].status.value if p in meta else '?'})")
        for p in s.missing:
            print(f"    MISSING {p}")
        for p in s.extra:
            print(f"    EXTRA {p}  (status {meta[p].status.value if p in meta else '?'})")
        out.append({"condition": label, "routes": r.routes, "f1": round(s.f1, 3), "wrong": s.wrong,
                    "missing": s.missing, "extra": s.extra, "flagged": flagged, "fields": len(meta),
                    "usd": round(cost, 5), "calls": r.calls,
                    "retrieved": r.retrieved.model_dump() if r.retrieved else None,
                    "prediction": r.result.model_dump(mode="json", exclude_none=True) if r.result else None})
    print(f"\nTOTAL COST ${total:.4f}")
    path = ROOT / "evals/results" / f"rag_{datetime.now():%Y%m%d_%H%M%S}_{args.model}.json"
    path.write_text(json.dumps({"model": args.model, "passes": args.passes, "usd": round(total, 5), "runs": out},
                               indent=2, default=str))
    print(f"saved {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
