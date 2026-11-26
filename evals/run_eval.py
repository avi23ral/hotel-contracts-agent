"""Run the extractor on every input and score it against gold.

Usage:  python evals/run_eval.py [--model claude-opus-5-5] [--only 002]
Writes evals/results/<timestamp>_<model>.json so runs can be compared over time.
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.extract import extract_file  # noqa: E402
from contract_agent.llm import EXTRACT_MODEL  # noqa: E402
from contract_agent.schema import GoldLabel  # noqa: E402
from contract_agent.scoring import score_fields  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=EXTRACT_MODEL)
    ap.add_argument("--only", default="", help="substring filter on file name")
    args = ap.parse_args()

    rows = []
    for gold_path in sorted((ROOT / "data/gold").glob("*.json")):
        if args.only not in gold_path.stem:
            continue
        gold = GoldLabel.model_validate_json(gold_path.read_text())
        t0 = time.time()
        try:
            pred, usage = extract_file(ROOT / "data/inputs" / gold.source_file, model=args.model)
        except Exception as e:  # record failures, don't hide them
            rows.append({"file": gold.source_file, "error": repr(e)})
            print(f"{gold.source_file}: ERROR {e!r}")
            continue
        secs = time.time() - t0
        target_pred = pred.contract if gold.contract else pred.change_set
        target_gold = gold.contract or gold.change_set
        s = score_fields(target_pred, target_gold)
        rows.append({
            "file": gold.source_file, "difficulty": gold.difficulty,
            "intent_ok": pred.intent == gold.intent, "pred_intent": pred.intent.value,
            "precision": round(s.precision, 3), "recall": round(s.recall, 3), "f1": round(s.f1, 3),
            "n_correct": len(s.correct), "wrong": s.wrong, "missing": s.missing, "extra": s.extra,
            "seconds": round(secs, 1), **usage,
            "prediction": pred.model_dump(mode="json", exclude_none=True),
        })
        print(f"{gold.source_file:38} intent={'OK ' if pred.intent == gold.intent else 'BAD'} "
              f"P={s.precision:.2f} R={s.recall:.2f} F1={s.f1:.2f} "
              f"wrong={len(s.wrong)} missing={len(s.missing)} extra={len(s.extra)} {secs:.0f}s")
        for path, pv, gv in s.wrong[:10]:
            print(f"    WRONG {path}: got {pv!r}, expected {gv!r}")

    ok = [r for r in rows if "error" not in r]
    if ok:
        print(f"\nMEAN F1 {sum(r['f1'] for r in ok) / len(ok):.3f} | intent acc "
              f"{sum(r['intent_ok'] for r in ok)}/{len(ok)} | tokens in/out "
              f"{sum(r['input_tokens'] for r in ok)}/{sum(r['output_tokens'] for r in ok)}")
    out = ROOT / "evals/results" / f"{datetime.now():%Y%m%d_%H%M%S}_{args.model}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, default=str))
    print(f"saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
