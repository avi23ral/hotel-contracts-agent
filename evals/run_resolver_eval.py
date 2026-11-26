"""Step 6 eval: resolving ambiguous fields three ways.

    python evals/run_resolver_eval.py                 # rules only (free)
    python evals/run_resolver_eval.py --llm           # + one-shot and ReAct on Haiku (costs ~2-5 cents)
    python evals/run_resolver_eval.py --llm --only r21

Outcomes per case:
  correct / correct_flag  - right value, or rightly sent to a human
  over_flag               - flagged although the answer was findable (costs review time)
  wrong / missed_flag     - a confident wrong value (the expensive kind)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from contract_agent.llm import FAST_MODEL  # noqa: E402
from contract_agent.rag import Store  # noqa: E402
from contract_agent.resolver import Question, one_shot, outcome, react, rules_only  # noqa: E402
from contract_agent.tools import Tools  # noqa: E402

CASES = ROOT / "data/resolver/cases.jsonl"
OUT = ROOT / "evals/results"
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}   # $ per 1M in/out


def usd(calls) -> float:
    return sum((c["input_tokens"] * PRICES[c["model"]][0] + c["output_tokens"] * PRICES[c["model"]][1]) / 1e6
               for c in calls)


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    c = Counter(r["outcome"] for r in rows)
    calls = [x for r in rows for x in r["calls"]]
    return {"cases": n, "accuracy": round((c["correct"] + c["correct_flag"]) / n, 3),
            "dangerous": c["wrong"] + c["missed_flag"], "over_flag": c["over_flag"], "outcomes": dict(c),
            "model_calls": len(calls), "tool_calls": sum(r["tool_calls"] for r in rows),
            "usd": round(usd(calls), 5), "usd_per_1000_fields": round(usd(calls) / n * 1000, 2),
            "median_seconds": sorted(r["seconds"] for r in rows)[n // 2]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="also run one-shot and ReAct (costs money)")
    ap.add_argument("--model", default=FAST_MODEL)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()

    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    store = Store()
    tools = Tools(store)
    methods = {"rules_only": lambda q: rules_only(q, tools)}
    if args.llm:
        methods["one_shot"] = lambda q: one_shot(q, args.model, store)
        methods["react"] = lambda q: react(q, args.model, tools)

    report = {"model": args.model if args.llm else None, "summary": {}, "rows": {}}
    for name, fn in methods.items():
        qs = [Question(hotel_id=c["hotel_id"], field=c["field"], message=c["message"]) for c in cases]
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(fn, qs))
        rows = []
        for c, res in zip(cases, results):
            rows.append({"id": c["id"], "field": c["field"], "expected": c["expected"], "status": res.status,
                         "value": res.value, "outcome": outcome(c["expected"], res), "reason": res.reason,
                         "tool_calls": res.tool_calls, "calls": res.calls, "seconds": res.seconds,
                         "trace": res.trace})
        report["rows"][name] = rows
        report["summary"][name] = summarise(rows)

    print(json.dumps(report["summary"], indent=2))
    for name, rows in report["rows"].items():
        bad = [f"{r['id']} {r['outcome']}: expected {r['expected']}, got {r['status']} {r['value']}"
               for r in rows if r["outcome"] not in ("correct", "correct_flag")]
        print(f"\n{name}: {len(bad)} not right\n  " + "\n  ".join(bad))
    if not args.only:
        path = OUT / ("resolver_eval.json" if args.llm else "resolver_eval_rules.json")
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\nsaved {path.relative_to(ROOT)}")
    total = sum(s["usd"] for s in report["summary"].values())
    print(f"total API cost this run: ${total:.4f}")


if __name__ == "__main__":
    main()
