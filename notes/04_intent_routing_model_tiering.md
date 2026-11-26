# Concept note 4: intent detection, routing and model tiering

## The problem in one line
The extractor is expensive (about $0.18 to $0.25 per message with two passes on the big model) and does one generic job. Most hotel mail isn't a new contract, and in our eval set nearly a third (14 of 44) aren't contracts at all. We need to know what a message is before we spend money on it, and to send each kind to the pipeline built for it.

## What we built
- **Intent detection** (`intent.py`): a small, cheap model (Haiku) reads the message and returns a *set* of labels, with one line of reasoning and a confidence.
  - `new_contract`: full terms for a hotel or season
  - `ari_update`: availability, rates and inventory (rate changes, stop sales and re-opens, closures, allotment, release and min-stay changes)
  - `promotion`: offers added, changed, extended or ended
  - `not_a_contract`: invoices, marketing, booking admin, complaints, our own requests and unagreed counter-offers
- **Multi-label, not one label.** The WhatsApp chat (003) holds a stop sale *and* two offer changes. A single-label classifier would have to drop one of them.
- **Router** (`router.py`): plain code, not the model, decides where each label goes. New contract goes to full extraction. ARI and promotion each get their own focused extraction, which only keeps its own change types. Both are then merged back into one change set for review. Not-a-contract skips extraction entirely.
- **Cascade (model tiering):** Haiku first. If its confidence is below 0.7, the big model (Opus) re-checks. Most messages never touch the expensive model.
- **Rules baseline:** keyword regexes, so we can show what the model is worth.

## Why ARI and promotions are separate paths
From the research: rate and stop-sale changes are most of the traffic after signing, and they're time-critical (a missed stop sale is an overbooking). Promotions are fewer, more structured, and belong in their own versioned layer, so an offer can be ended without touching base rates. Separate paths mean separate prompts, separate evals and separate owners. The eval set is weighted the same way: 20 messages with an ARI update against 10 with a promotion.

## Results (44 labelled messages, 12 deliberately hard, 2 runs)
| Classifier | Exact accuracy | $ per 1,000 msgs | Median latency | Costly misses |
|---|---|---|---|---|
| Keyword rules | 73% | 0 | 0 s | 4 ARI updates missed (Italian stop sale, informal rate cut...) |
| Haiku (small) | 96% | $0.15 | 1.4 s | 1: a stop sale hidden in an out-of-office reply |
| Opus (big) | 100% | $5.90 | 1.9 s | 0 |
| **Cascade** | **98%** | **$0.30–0.42** | **1.4 s** | **0** (Haiku was unsure on the out-of-office, Opus fixed it) |

The cascade's one remaining error is our own "we've loaded your stop sale" email, labelled as an ARI update. That's the cheap direction to be wrong: the extractor finds a stop sale we already have and a reviewer dismisses it. Missing a real stop sale is the expensive direction.

End to end on the three original inputs, the router chose the right paths (001 and 002 to new contract, 003 split into ARI plus promotion). F1 was 0.95, and the confidence step caught all 4 wrong fields while reviewing 17%. Running through the router cost $0.60 for the three inputs, against about $0.53 direct. The extra is 003's two focused paths, which is the price of keeping ARI and promotions apart.

## Eval lessons
- **The first eval was too easy.** On the first 32 messages both models scored 100%, which tells you nothing. I added 12 adversarial cases: an out-of-office carrying a real stop sale, a "Special news!" that is really a permanent rate change, a rate cut with a one-week booking window (really a promotion), an Italian stop sale, an invoice with a "save 10%" footer, and a kids club "closed" (a facility, not rooms). That is where the models separated.
- **Weight errors by direction, not just count.** Accuracy treats every miss the same. A missed stop sale (false negative on ARI) costs an overbooking; a false alarm costs a reviewer 30 seconds. The eval reports "missed ARI" separately for that reason.
- **Rules are a real baseline.** 73% for free is not nothing, and rules are perfectly explainable. They break on language, phrasing and context ("closed" for a kids club). The LLM's value is exactly those cases.

## Cost, in one line
Classifying a message costs about $0.00015. Extracting one costs about $0.20. Skipping extraction on one non-contract message saves over 1,000 times what classifying it cost.

## Interview soundbite
"I put a cheap classifier in front of the expensive extractor. It's multi-label, because one hotel WhatsApp can hold a stop sale and a new offer, and the router splits those into separate ARI and promotion pipelines. Haiku alone scored 96% on a hard eval set at $0.15 per thousand messages, but its one miss was a stop sale buried in an out-of-office, which is the costly kind. So I added a confidence-based cascade to Opus. That reached 98% with no costly misses, for about 40 cents per thousand messages instead of six dollars."
