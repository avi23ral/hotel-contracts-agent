"""Run the step 3 pipeline on every input and score it against gold.

Usage:
  python evals/run_eval.py [--model claude-opus-5-5] [--only 002] [--direct]
  (default: through the step 4 router; --direct skips intent detection, as in step 3)
  python evals/run_eval.py --rescore evals/results/<old run>.json   # old predictions, current scorer

Writes evals/results/<timestamp>_<model>.json, then calibration.py draws the
calibration chart from it.

Besides F1, it reports what the confidence score is FOR: how many fields a
human has to look at (review load) and how many wrong fields slip through
unreviewed (escapes), for the step 2 policy and the step 3 policy.
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.confidence import COST_WEIGHTS, THRESHOLDS, category  # noqa: E402
from contract_agent.extract import baseline_status, extract_file  # noqa: E402
from contract_agent.ingest import load_source  # noqa: E402
from contract_agent.router import route_doc  # noqa: E402
from contract_agent.schema import Intent  # noqa: E402

PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}   # $ per 1M in/out
ROUTE_INTENT = {"new_contract": Intent.new_contract, "ari": Intent.amendment,
                "promotion": Intent.amendment, "ignore": Intent.not_a_contract}


def routed(path: Path, model: str):
    r = route_doc(load_source(path), model=model)
    usage = {"model": model, "routes": r.routes, "intent_labels": [x.value for x in r.intent.labels],
             "input_tokens": sum(c["input_tokens"] for c in r.calls),
             "output_tokens": sum(c["output_tokens"] for c in r.calls),
             "usd": round(sum((c["input_tokens"] * PRICES[c["model"]][0] + c["output_tokens"]
                               * PRICES[c["model"]][1]) / 1e6 for c in r.calls), 4),
             "calls": r.calls}
    return r.result, ROUTE_INTENT[r.routes[0]], usage
from contract_agent.llm import EXTRACT_MODEL  # noqa: E402
from contract_agent.schema import ChangeSet, Contract, GoldLabel, Status  # noqa: E402
from contract_agent.scoring import score_fields  # noqa: E402

SECONDS_PER_FLAGGED_FIELD = 30      # Avi's assumption, see build plan comments
MANUAL_MINUTES_PER_CONTRACT = 180   # research: 2-4 hours per manual load


def golds(only: str):
    for gold_path in sorted((ROOT / "data/gold").glob("*.json")):
        if only in gold_path.stem:
            yield GoldLabel.model_validate_json(gold_path.read_text())


def rescore(path: Path):
    """Score saved predictions with the current scorer (isolates scorer fixes)."""
    old = {r["file"]: r for r in json.loads(path.read_text()) if "prediction" in r}
    f1s = []
    for gold in golds(""):
        pred = old[gold.source_file]["prediction"]
        if gold.contract:
            s = score_fields(Contract.model_validate(pred["contract"]), gold.contract)
        else:
            s = score_fields(ChangeSet.model_validate(pred["change_set"]), gold.change_set)
        # Fields added to the schema after that run can't be held against it.
        new = lambda p: p.endswith(".inventory")  # noqa: E731
        s.correct = [p for p in s.correct if not new(p)]
        s.missing = [p for p in s.missing if not new(p)]
        s.wrong = [w for w in s.wrong if not new(w[0])]
        f1s.append(s.f1)
        print(f"{gold.source_file:38} old F1={old[gold.source_file]['f1']:.3f}  rescored F1={s.f1:.3f}  "
              f"wrong={len(s.wrong)} missing={len(s.missing)} extra={len(s.extra)}")
        for p, pv, gv in s.wrong:
            print(f"    WRONG {p}: got {pv!r}, expected {gv!r}")
        for p in s.missing:
            print(f"    MISSING {p}")
    print(f"\nRESCORED MEAN F1 {sum(f1s) / len(f1s):.3f}")


def policy_summary(fields: list[dict], key: str, n_docs: int) -> dict:
    """Review load vs escapes for one auto-accept policy."""
    flagged = [f for f in fields if f[key] != "auto_accept"]
    accepted = [f for f in fields if f[key] == "auto_accept"]
    escapes = [f for f in accepted if f["outcome"] != "correct"]
    caught = [f for f in flagged if f["outcome"] != "correct"]
    n_wrong = sum(f["outcome"] != "correct" for f in fields)
    minutes = len(flagged) * SECONDS_PER_FLAGGED_FIELD / 60 / max(n_docs, 1)
    return {
        "fields": len(fields), "flagged": len(flagged),
        "review_load_pct": round(100 * len(flagged) / max(len(fields), 1), 1),
        "wrong_fields": n_wrong, "caught": len(caught), "escapes": len(escapes),
        "weighted_escapes": sum(COST_WEIGHTS[f["category"]] for f in escapes),
        "escape_paths": [f"{f['file'][:3]} {f['path']}" for f in escapes],
        "review_minutes_per_contract": round(minutes, 1),
        "time_saved_pct": round(100 * (1 - minutes / MANUAL_MINUTES_PER_CONTRACT), 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=EXTRACT_MODEL)
    ap.add_argument("--only", default="", help="substring filter on file name")
    ap.add_argument("--rescore", type=Path, help="re-score a saved results file and exit")
    ap.add_argument("--direct", action="store_true", help="skip the router (step 3 behaviour)")
    args = ap.parse_args()
    if args.rescore:
        return rescore(args.rescore)

    rows, fields = [], []
    for gold in golds(args.only):
        t0 = time.time()
        try:
            if args.direct:
                pred, usage = extract_file(ROOT / "data/inputs" / gold.source_file, model=args.model)
                pred_intent = pred.intent
            else:
                pred, pred_intent, usage = routed(ROOT / "data/inputs" / gold.source_file, args.model)
        except Exception as e:  # record failures, don't hide them
            rows.append({"file": gold.source_file, "error": repr(e)})
            print(f"{gold.source_file}: ERROR {e!r}")
            continue
        secs = time.time() - t0
        if pred is None:   # router said "not a contract": nothing extracted
            from contract_agent.schema import ExtractionResult
            pred = ExtractionResult(source_file=gold.source_file, intent=pred_intent)
        target_pred = pred.contract if gold.contract else pred.change_set
        target_gold = gold.contract or gold.change_set
        s = score_fields(target_pred, target_gold)
        correct, extra = set(s.correct), set(s.extra)
        for path, m in pred.field_meta.items():
            fields.append({
                "file": gold.source_file, "path": path, "category": m.category,
                "outcome": "correct" if path in correct else "extra" if path in extra else "wrong",
                "confidence": m.confidence, "model_confidence": m.model_confidence,
                "step3": m.status.value, "baseline": baseline_status(m.model_confidence).value
                if m.model_confidence is not None else "auto_accept",   # step 2: no note = nobody looked
                "evidence_found": m.evidence_found, "agrees": m.agrees, "reasons": m.reasons,
            })
        rows.append({
            "file": gold.source_file, "difficulty": gold.difficulty,
            "intent_ok": pred_intent == gold.intent, "pred_intent": pred_intent.value,
            "precision": round(s.precision, 3), "recall": round(s.recall, 3), "f1": round(s.f1, 3),
            "n_correct": len(s.correct), "wrong": s.wrong, "missing": s.missing, "extra": s.extra,
            "issues": [i.model_dump() for i in pred.issues],
            "seconds": round(secs, 1), **usage,
            "prediction": pred.model_dump(mode="json", exclude_none=True),
        })
        flagged = sum(m.status != Status.auto_accept for m in pred.field_meta.values())
        print(f"{gold.source_file:38} intent={'OK ' if pred_intent == gold.intent else 'BAD'} "
              f"routes={'+'.join(usage.get('routes', ['direct']))} "
              f"P={s.precision:.2f} R={s.recall:.2f} F1={s.f1:.2f} "
              f"wrong={len(s.wrong)} missing={len(s.missing)} extra={len(s.extra)} "
              f"flagged={flagged}/{len(pred.field_meta)} {secs:.0f}s")
        for path, pv, gv in s.wrong[:10]:
            print(f"    WRONG {path}: got {pv!r}, expected {gv!r}")
        for i in pred.issues:
            print(f"    {i.severity.upper():7} {i.rule}: {i.message}")

    ok = [r for r in rows if "error" not in r]
    summary = {}
    if ok:
        n = len(ok)
        summary = {
            "mean_f1": round(sum(r["f1"] for r in ok) / n, 3),
            "intent_acc": f"{sum(r['intent_ok'] for r in ok)}/{n}",
            "tokens_in": sum(r["input_tokens"] for r in ok), "tokens_out": sum(r["output_tokens"] for r in ok),
            "usd": round(sum(r.get("usd", 0) for r in ok), 3),
            "quote_not_found_pct": round(100 * sum(f["evidence_found"] is False for f in fields) / len(fields), 1),
            "no_evidence_pct": round(100 * sum(f["evidence_found"] is None for f in fields) / len(fields), 1),
            "missing_fields": sum(len(r["missing"]) for r in ok),
            "thresholds": THRESHOLDS,
            "policies": {"step2_model_conf_0.8": policy_summary(fields, "baseline", n),
                         "step3_combined_per_field": policy_summary(fields, "step3", n)},
        }
        print(f"\nMEAN F1 {summary['mean_f1']:.3f} | intent acc {summary['intent_acc']} | "
              f"tokens in/out {summary['tokens_in']}/{summary['tokens_out']} (2 passes) | ${summary['usd']}")
        print(f"quotes not found in source: {summary['quote_not_found_pct']}% of fields | "
              f"no quote: {summary['no_evidence_pct']}% | fields missed entirely: {summary['missing_fields']}")
        for name, p in summary["policies"].items():
            print(f"{name:28} review {p['flagged']}/{p['fields']} ({p['review_load_pct']}%), "
                  f"wrong {p['wrong_fields']}: caught {p['caught']}, escaped {p['escapes']} "
                  f"(weighted {p['weighted_escapes']}), ~{p['review_minutes_per_contract']} min/contract")
            for e in p["escape_paths"]:
                print(f"    ESCAPE {e}")
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}_{args.model}"
    out = ROOT / "evals/results" / f"{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows, "fields": fields}, indent=2, default=str))
    print(f"saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
