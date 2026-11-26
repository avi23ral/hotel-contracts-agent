"""Calibration chart and threshold trade-off, from one or more eval runs.

Usage: python evals/calibration.py [results/*.json ...]   (default: every step 3 run)
Writes evals/results/calibration.png and prints the numbers behind it.

Left panel (reliability diagram): fields are grouped by confidence. For each
group, the dot shows what share of fields were actually right. A perfectly
calibrated score sits on the diagonal: 0.9-confidence fields are right 90%
of the time. Dots below the line = overconfident.

Right panel (the product trade-off): move every per-field threshold up or
down together. Each point is one setting: how many fields a human reviews
vs how many wrong fields slip through unreviewed.
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from contract_agent.confidence import THRESHOLDS  # noqa: E402

BINS = [0, 0.7, 0.8, 0.9, 0.95, 1.0001]
BLUE, ORANGE = "#257de0", "#f47230"
INK, INK2, GRID = "#191919", "#666666", "#e6e6e6"


def load(paths):
    fields = []
    for p in paths:
        d = json.loads(Path(p).read_text())
        if isinstance(d, dict) and "fields" in d:
            fields += d["fields"]
    return fields


def reliability(fields, key):
    out = []
    for lo, hi in zip(BINS, BINS[1:]):
        fs = [f for f in fields if f[key] is not None and lo <= f[key] < hi]
        if fs:
            out.append({"bin": f"{lo:.2f}-{min(hi, 1):.2f}", "n": len(fs),
                        "mean_conf": sum(f[key] for f in fs) / len(fs),
                        "accuracy": sum(f["outcome"] == "correct" for f in fs) / len(fs)})
    return out


def ece(rows, n):
    """Expected calibration error: average gap between confidence and accuracy."""
    return sum(r["n"] / n * abs(r["mean_conf"] - r["accuracy"]) for r in rows)


def sweep(fields, shifts):
    pts = []
    for s in shifts:
        flagged = escapes = 0
        for f in fields:
            bar = THRESHOLDS[f["category"]] + s
            if f["confidence"] >= bar:
                escapes += f["outcome"] != "correct"
            else:
                flagged += 1
        pts.append({"shift": round(s, 2), "review_pct": 100 * flagged / len(fields), "escapes": escapes})
    return pts


def main():
    paths = sys.argv[1:] or [p for p in sorted((ROOT / "evals/results").glob("*.json"))]
    fields = load(paths)
    runs = sum(1 for p in paths if "fields" in json.loads(Path(p).read_text()))
    n_wrong = sum(f["outcome"] != "correct" for f in fields)
    model_rows = reliability([f for f in fields if f["model_confidence"] is not None], "model_confidence")
    comb_rows = reliability(fields, "confidence")
    n_model = sum(r["n"] for r in model_rows)
    print(f"{len(fields)} fields from {runs} runs, {n_wrong} wrong")
    for name, rows, n in (("model's own rating", model_rows, n_model), ("combined score", comb_rows, len(fields))):
        print(f"\n{name}: ECE {ece(rows, n):.3f}")
        for r in rows:
            print(f"  {r['bin']:10} n={r['n']:4}  mean conf {r['mean_conf']:.2f}  right {r['accuracy']:.0%}")
    pts = sweep(fields, [x / 100 for x in range(-30, 6, 5)])
    print("\nthreshold shift  review%  escapes")
    for p in pts:
        print(f"  {p['shift']:+.2f}          {p['review_pct']:5.1f}   {p['escapes']}")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "text.color": INK,
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
                         "axes.edgecolor": GRID})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5), dpi=200)
    fig.patch.set_facecolor("white")

    ax1.plot([0, 1], [0, 1], color=INK2, lw=1, ls=(0, (4, 3)))
    ax1.text(0.62, 0.57, "perfect calibration", color=INK2, fontsize=8.5, rotation=33,
             rotation_mode="anchor")
    for rows, color, label in ((model_rows, ORANGE, f"Model's own rating (ECE {ece(model_rows, n_model):.2f})"),
                               (comb_rows, BLUE, f"Combined score (ECE {ece(comb_rows, len(fields)):.2f})")):
        xs, ys = [r["mean_conf"] for r in rows], [r["accuracy"] for r in rows]
        ax1.plot(xs, ys, color=color, lw=2, zorder=2)
        ax1.scatter(xs, ys, s=[18 + r["n"] ** 0.8 for r in rows], color=color, edgecolor="white",
                    linewidth=1.5, zorder=3, label=label)
    ax1.set(xlim=(0.5, 1.0), ylim=(0.5, 1.03), xlabel="Confidence (mean of each group; dot size = fields)",
            ylabel="Share of fields actually right")
    ax1.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    low = comb_rows[0]
    ax1.annotate(f"lowest group: {low['n']} fields,\n{low['accuracy']:.0%} right", (low["mean_conf"], low["accuracy"]),
                 textcoords="offset points", xytext=(10, -22), fontsize=8.5, color=INK2)
    ax1.set_title("Both scores are under-confident;\nthe combined one isolates the errors", loc="left",
                  fontsize=11.5, fontweight="bold")
    ax1.legend(frameon=False, fontsize=8.5, loc="lower right")

    xs, ys = [p["review_pct"] for p in pts], [p["escapes"] for p in pts]
    ax2.plot(xs, ys, color=BLUE, lw=2, zorder=2)
    ax2.scatter(xs, ys, s=40, color=BLUE, edgecolor="white", linewidth=1.5, zorder=3)
    for p in pts:
        if p["shift"] in (-0.3, -0.2, -0.1, 0.0, 0.05):
            label = "chosen bars" if p["shift"] == 0 else f"bars {p['shift']:+.2f}"
            ax2.annotate(label, (p["review_pct"], p["escapes"]), textcoords="offset points",
                         xytext=(6, 6), fontsize=8.5, color=INK if p["shift"] == 0 else INK2,
                         fontweight="bold" if p["shift"] == 0 else "normal")
    ax2.set(xlabel="Fields sent to a human (%)", ylabel=f"Wrong fields auto-accepted (of {n_wrong})")
    ax2.set_ylim(bottom=-0.5)
    ax2.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(100, decimals=0))
    chosen = next(p for p in pts if p["shift"] == 0)
    ax2.set_title(f"Chosen bars: {chosen['review_pct']:.0f}% of fields reviewed,\n"
                  f"{n_wrong - chosen['escapes']} of {n_wrong} errors caught", loc="left",
                  fontsize=11.5, fontweight="bold")

    for ax in (ax1, ax2):
        ax.grid(color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    fig.text(0.01, 0.005, f"{len(fields)} extracted fields, {runs} eval runs x 3 synthetic inputs. "
             "Small sample: read the shape, not the decimals.", fontsize=8, color=INK2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    out = ROOT / "evals/results/calibration.png"
    fig.savefig(out, facecolor="white")
    print(f"\nsaved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
