# Concept note 3: evidence, validators, confidence and calibration

## The problem in one line
The step 2 baseline was already accurate (F1 0.95 once the scorer was fixed), but it couldn't tell you WHICH fields were wrong. Its policy (auto-accept when the model says 0.8+) sent only 2-4% of fields to a human and let 4 of the 6 wrong fields through across three eval runs. An agent that's right 98% of the time but can't point at the 2% still needs a human to check 100%.

## What we built
For every field the agent returns, four independent signals:

1. **Evidence quote, checked in code** (`evidence.py`). The model must quote the source. We check the quote is really in the text (exact after normalising spaces and dashes, or a close fuzzy match). Numbers are never fuzzy: "21 days" can't pass for "14 days". Then we check the value is in its quote: a price of 155 needs a "155" in the quote, unless the model says the value was derived (AI = HB + 70).
2. **Validator rules** (`validators.py`). About 45 plain Python rules: seasons don't overlap or leave gaps, cancellation charges rise as arrival gets closer, tiers join up, HB costs more than BB, a price isn't 10x its neighbours, allotment rooms have a room count, offers can't be booked after the stay ends. A broken rule points at the exact fields involved.
3. **Self-consistency.** We run the extraction twice and compare. If two runs of the same model on the same text disagree, at least one of them is guessing.
4. **The model's own rating.** Still useful, just not on its own.

`confidence.py` combines them. The score starts at the model's rating, and each red flag **caps** it: no quote 0.5, fake quote 0.4, broken rule 0.3, runs disagree 0.35. Caps instead of a weighted average keep it explainable: the reviewer sees exactly which check pulled a field down ("quote not found in the source").

## Per-field thresholds = cost of a mistake
| Field type | Auto-accept at | Cost weight | Why |
|---|---|---|---|
| Cancellation | 0.95 | 5 | Decides if an error is a refund or an unrecoverable supplier penalty |
| Price | 0.93 | 4 | Fans out into every offer row for that room and season |
| Dates | 0.90 | 3 | Catastrophic only when they make a booking impossible |
| Board | 0.80 | 2 | Commercially important, less financially dangerous |
| Everything else | 0.75 | 1 | |

The threshold is a **product decision, not a model setting**. The eval turns it into a trade-off you can show a commercial stakeholder: review load against escapes (wrong fields that go live without review).

## Calibration
A score is **calibrated** if 0.9-confidence fields are right about 90% of the time. The reliability diagram (`evals/results/calibration.png`) groups fields by confidence and plots how many were actually right. ECE (expected calibration error) is the average gap between the two.

Why it matters: thresholds only mean something if the score is calibrated. If "0.95" fields are only right 80% of the time, a 0.95 bar for cancellation gives you a false sense of safety.

What our chart shows (3 runs, 1,040 fields, 6 wrong):
- Both scores are **under-confident**, not over-confident. Fields rated about 0.83 were right 100% of the time. That is the safe direction to be wrong in: it costs review time, not wrong prices.
- ECE is about 0.09 for both, so the combined score is no better *calibrated*. Its value is **ranking**: the lowest group (11 fields) holds most of the errors and is only 73% right. That's what a threshold needs.
- The trade-off curve has a clear knee. Below about 6% review, errors start slipping through fast. Our chosen bars review about 16% and catch 5 of 6 errors. The one that slipped is payment-terms wording ("By bank transfer..." vs "Bank transfer..."), which is cosmetic. Raising every bar by 0.05 would review 41% and catch nothing extra.

Honest caveat: 3 inputs give about 350 fields and only a handful of errors per run, so the curve is noisy. The fix is more gold examples (step 7's review queue turns every human correction into one).

## Eval lesson: check the scorer before the model
The first run scored 0.68. Most of that gap was the **scorer**, not the model: "Season A" vs "A" turned 57 fields in the email contract into both a miss and a hallucination, and an offer change keyed by its dates broke when the dates weren't in the message. Re-scoring the old predictions with the fixed scorer gives 0.95. Always read your failures before tuning prompts. Otherwise you optimise the model against a bug in your ruler.

## Research folded in
- Net, per room per night is the default. Per person or commissionable now raises a warning, so a reviewer checks the conversion.
- Each room has an inventory model (free sale by default, allotment, on request). Allotment counts only make sense on allotment rooms, and a validator enforces that.
- `hotel_id` (ours, the system of record) and `giata_id` (cross-supplier mapping) are on the hotel. The agent never invents them. Step 6's property lookup fills them in.
- A single-use price per rate, with a warning when rooms allow one adult but no single terms are stated.
- Offer rows are described as a cached, hotel-only view. The contract is the master record, and each row now carries its weakest field's confidence.

## Interview soundbite
"The model's own confidence was useless for routing: it auto-accepted 5 of 6 errors. I built a combined score from things code can verify: is the quote real, do business rules pass, do two runs agree. Then I set per-field thresholds by cost of error, strictest on cancellation terms. Across three eval runs it caught 5 of 6 wrong fields, against 2 of 6 for the model's own confidence, while sending about 16% of fields to a human. That's roughly 9 minutes of review per contract instead of 3 hours of keying. The calibration chart showed the score was under-confident, so the next lever is more gold data, not looser thresholds."
