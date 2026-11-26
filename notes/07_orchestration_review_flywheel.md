# Concept note 7: orchestration, human review and the data flywheel

## The problem in one line
Steps 2 to 6 each produce a piece: an intent, a route, retrieved context, extracted fields, confidence and agent answers. On their own, none of these changes what a customer can book. Step 7 joins them into one flow that ends with an **offer file a human has signed off**, and turns every human fix into training data for the next round.

## The flow (orchestration)
```
message -> intent -> route -> retrieve (amendments) -> extract -> checks + confidence
        -> resolver agent (fields failing a check) -> REVIEW QUEUE -> approve
        -> new contract version -> offer file + stop-sale file -> correction log
```
`orchestrate.py` runs this end to end. It is a **workflow**: fixed steps in code, each tested on its own. The only part that decides its own steps is the step 6 resolver, and only for the fields that need it.

## The review queue: show the human only what matters
- Fields below their confidence bar (step 3), each with its source quote and the reason the score was lowered, such as "two extraction runs disagree" or "no supporting quote".
- **Missing fields**, a new check in this step. An offer without stay dates or a "combinable" answer can't be loaded, so it's queued even though no confidence score exists for it. This caught the one field the chat run missed: the new October offer's "can't combine with the early booking".
- Everything else is auto-accepted, with its evidence still one click away.

The reviewer accepts or corrects each queued field. **Approve stays locked until every queued field has a decision.**

## Versions, not edits
Approving never edits a contract in place. It writes a **new version**:
- a new contract becomes v1
- an amendment is applied on top of the latest version (`apply.py`)

The playbook rule "an offer change inherits every field it doesn't mention" is applied in code. Changes that can't be applied safely come back as "do by hand", for example a price change covering only part of a season. They are never half-applied.

The export reads **approved versions only**, so an unreviewed value can't reach the offer file. Every row carries its source, for example `H001-v2`. Rates and stop sales go out as separate files because they change at different speeds: a stop sale shouldn't mean re-sending every price.

## The data flywheel
Every correction is stored as "model said X, human said Y", together with the confidence, reasons and quote (`data/flywheel/corrections.jsonl`). That is a labelled example, for free, at exactly the spot where the model is weakest. Accepted fields are data too: they show which flags were false alarms. Over time this:
- grows the eval set where it matters
- shows which checks over-flag, which feeds back into thresholds (step 3's calibration said the score is under-confident)
- builds the fine-tuning or few-shot set if you ever need one

**More use means more corrections, then better evals, then fewer flags, then faster review.** That loop is the product's moat.

## Results: the whole loop with a simulated reviewer (free)
The saved runs went through the queue, with the gold labels playing the reviewer (`evals/run_review_eval.py`).

| | PDF contract | Email contract | WhatsApp amendment |
|---|---|---|---|
| Fields extracted | 212 | 111 | 25 |
| Sent to a human | 22 (10%) | 27 (24%) | 11 (44%) |
| Corrected by the human | 1 | 0 | 1 (the missing "combinable") |
| Field accuracy (F1), before → after review | 0.995 → 1.0 | 1.0 → 1.0 | 0.98 → 1.0 |
| Review time at 30 s per field | 11 min | 13.5 min | 5.5 min |

- **About 12 minutes of review per contract, against about 3 hours of manual keying** (the build plan's assumption).
- Offer rows exactly right: 108 of 108. The amended contract (stop sale, extended early booking, new October offer) exactly matches what the gold change set produces.
- **58 of the 60 queued fields were already right.** That's the under-confidence from step 3, now shown as review time. The accept decisions are the data to tune the bars with. It's the next lever, and it's free.
- The one live test run (an email with a stop sale and a "+4 EUR" rate change) cost $0.002 on Haiku and applied cleanly.

Caveat: three inputs, and a perfect simulated reviewer. Real reviewers make mistakes too, which is why the correction log keeps the evidence next to each change.

## The demo app
`app/demo_app.py` (Streamlit) shows every stage in order: message, intent, route, retrieval, extracted fields, review queue, approval and the offer file download.
- **Replay mode** (the default) uses saved runs. It needs no API key and costs nothing, so it's safe to share by link.
- **Live mode** runs on pasted or uploaded text, on Haiku with one pass. It is protected by a password and a daily spend cap. The real hard stop is a monthly limit on the API key.

## What it cost
About **$0.002**: one live test run. Everything else (the review eval, tests and screenshots) uses saved runs and makes no model calls.

## PM angle
- **Human in the loop is a product surface, not a fallback.** Its design decides whether the agent saves 90% of the work or 10%.
- **Gate the output, not the model.** Export reads approved versions only, which makes "no unreviewed price reaches a customer" a property of the system.
- **Measure the review queue's precision too.** 58 of 60 flags were right values, which is time to win back by tuning thresholds with the accept data.
- **The flywheel is the strategy.** Every hotel onboarded makes the next one cheaper to review.

## Interview soundbite
"The last step joined everything into one workflow that ends at a reviewed offer file. Reviewers only see low-confidence or missing fields, each with its source quote and why it was flagged, and approval is blocked until each one has a decision. Approving writes a new contract version, and the export reads approved versions only, so an unreviewed price can't go live. Every correction is logged as a labelled example: that's the data flywheel. On my test inputs, review took about 12 minutes per contract instead of 3 hours of keying, with every offer row right. The data also showed 58 of 60 flags were already correct, so the next win is tuning thresholds with that accept data. And there's a free demo link with replay mode, so anyone can click through it."
