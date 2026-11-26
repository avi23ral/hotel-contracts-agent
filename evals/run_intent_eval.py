"""Step 4 eval: intent detection on 32 labelled messages.

Usage: python evals/run_intent_eval.py [--repeats 2]
Compares keyword rules, the small model (Haiku), the big model (Opus), and the
cascade (Haiku, escalating to Opus when Haiku's confidence < 0.7). The cascade
is computed from the two models' answers, so it costs no extra calls here.

Writes evals/results/intent_<timestamp>.json.
"""
import argparse
import json
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.ingest import load_source  # noqa: E402
from contract_agent.intent import ESCALATE_BELOW, Label, classify_llm, classify_rules  # noqa: E402
from contract_agent.llm import EXTRACT_MODEL, FAST_MODEL  # noqa: E402

# $ per 1M tokens (input, output), first-party API list prices
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}
LABELS = [lab.value for lab in Label]


def messages():
    for line in (ROOT / "data/intents/messages.jsonl").read_text().splitlines():
        m = json.loads(line)
        if "file" in m:
            m["text"] = load_source(ROOT / "data/inputs" / m["file"]).text
        yield m


def route(labels) -> str:
    return "+".join(sorted(labels))


def cost(call) -> float:
    pin, pout = PRICES[call["model"]]
    return (call["input_tokens"] * pin + call["output_tokens"] * pout) / 1e6


def summarise(name, preds, msgs):
    """preds: id -> {"labels": [...], "calls": [...]}"""
    exact = [route(preds[m["id"]]["labels"]) == route(m["labels"]) for m in msgs]
    per_label = {}
    for lab in LABELS:
        tp = sum(lab in preds[m["id"]]["labels"] and lab in m["labels"] for m in msgs)
        fp = sum(lab in preds[m["id"]]["labels"] and lab not in m["labels"] for m in msgs)
        fn = sum(lab not in preds[m["id"]]["labels"] and lab in m["labels"] for m in msgs)
        per_label[lab] = {"precision": round(tp / (tp + fp), 2) if tp + fp else None,
                          "recall": round(tp / (tp + fn), 2) if tp + fn else None}
    calls = [c for m in msgs for c in preds[m["id"]]["calls"]]
    secs = [sum(c["seconds"] for c in preds[m["id"]]["calls"]) for m in msgs]
    confusion = Counter((route(m["labels"]), route(preds[m["id"]]["labels"])) for m in msgs)
    # costly mistakes: an ARI update that never reaches the ARI path (missed stop sale = overbooking)
    missed_ari = [m["id"] for m in msgs if "ari_update" in m["labels"]
                  and "ari_update" not in preds[m["id"]]["labels"]
                  and "new_contract" not in preds[m["id"]]["labels"]]
    return {
        "classifier": name, "accuracy": round(sum(exact) / len(msgs), 3),
        "per_label": per_label, "missed_ari": missed_ari,
        "errors": [{"id": m["id"], "gold": m["labels"], "pred": preds[m["id"]]["labels"], "note": m["note"]}
                   for m, ok in zip(msgs, exact) if not ok],
        "escalated": sum(len(preds[m["id"]]["calls"]) > 1 for m in msgs),
        "usd_per_1000_msgs": round(1000 * sum(cost(c) for c in calls) / len(msgs), 3) if calls else 0.0,
        "p50_seconds": round(statistics.median(secs), 2) if calls else 0.0,
        "confusion": [{"gold": g, "pred": p, "n": n} for (g, p), n in sorted(confusion.items())],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1, help="LLM runs per message (checks stability)")
    args = ap.parse_args()
    msgs = list(messages())
    runs = []
    for rep in range(args.repeats):
        def run(model, effort):
            with ThreadPoolExecutor(max_workers=8) as pool:
                outs = list(pool.map(lambda m: classify_llm(m["text"], model=model, effort=effort), msgs))
            return {m["id"]: o for m, o in zip(msgs, outs)}
        small, big = run(FAST_MODEL, "low"), run(EXTRACT_MODEL, "medium")
        preds = {
            "rules": {m["id"]: {"labels": [x.value for x in classify_rules(m["text"]).labels], "calls": []}
                      for m in msgs},
            f"small ({FAST_MODEL})": {i: {"labels": [x.value for x in r.labels], "calls": [u]}
                                      for i, (r, u) in small.items()},
            f"big ({EXTRACT_MODEL})": {i: {"labels": [x.value for x in r.labels], "calls": [u]}
                                       for i, (r, u) in big.items()},
            "cascade (small, then big if unsure)": {
                i: ({"labels": [x.value for x in r.labels], "calls": [u]} if r.confidence >= ESCALATE_BELOW
                    else {"labels": [x.value for x in big[i][0].labels], "calls": [u, big[i][1]]})
                for i, (r, u) in small.items()},
        }
        summary = [summarise(n, p, msgs) for n, p in preds.items()]
        confidences = {i: {"small": r.confidence, "big": big[i][0].confidence,
                           "small_reasoning": r.reasoning} for i, (r, _) in small.items()}
        runs.append({"repeat": rep, "summary": summary, "confidences": confidences})
        print(f"\n=== run {rep + 1}/{args.repeats}: {len(msgs)} messages ===")
        print(f"{'classifier':40} {'acc':>5} {'esc':>4} {'$/1k msgs':>10} {'p50 s':>6}  missed ARI")
        for s in summary:
            print(f"{s['classifier']:40} {s['accuracy']:5.0%} {s['escalated']:4} "
                  f"{s['usd_per_1000_msgs']:10.3f} {s['p50_seconds']:6.2f}  {s['missed_ari']}")
            for e in s["errors"]:
                print(f"      {e['id']}: gold {e['gold']} got {e['pred']}  ({e['note']})")
    out = ROOT / "evals/results" / f"intent_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(runs, indent=2))
    print(f"\nsaved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
