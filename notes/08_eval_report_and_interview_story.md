# 8. Eval report and interview story

## The product in one paragraph
A UK holiday company's contracting team receives hotel contracts and changes as PDFs, email threads and WhatsApp chats. Today someone reads each one and types rates, rooms, seasons, offers and policies into the system that produces bookable offers. Doing that by hand takes about 3 hours per contract. This agent reads the inputs, extracts every field with a source quote, checks itself, and shows a person **only the fields it isn't sure about**. It then writes the approved result as a new contract version and exports the offer file. **Goal: cut keying time without letting a wrong price reach a customer.**

## Results, step by step

| Step | Question | Result | Cost |
|---|---|---|---|
| 2 Baseline | Can one model call extract a contract? | F1 0.68 at first, but **0.95** once a bug in the scorer was fixed | about $0.20 per contract (large model) |
| 3 Confidence | Can we tell which fields are wrong? | Combined checks caught **5 of 6** wrong fields while sending about 16% of fields to review. The model's own confidence caught **2 of 6**. Calibration error about 0.09, with the score under-confident (the safe direction). | free (code checks) plus a second pass |
| 4 Intent and routing | Is this a contract, a rate or availability change, an offer change, or none? | Rules 73%, small model 96%, large model 100%, **cascade 98%** with no costly misses | $0.15 / $5.90 / **$0.30–0.42** per 1,000 messages |
| 5 Retrieval (RAG) | Can it use the existing contract? | Right hotel **100%** (31 cases), abstains on the look-alike hotel. Section recall@3 100%. Chat amendment: small model **0.90 → 0.98** with retrieval, beating the large model without it (0.86) | retrieval free; **$0.0035** per amendment |
| 6 Agent (ReAct) | Can it resolve ambiguous fields safely? | Rules 64%, one-shot 86% (**2 confident wrong**), **agent 96% (0 confident wrong)** | $0.87 per 1,000 fields |
| 7 Review loop | What does a person actually do? | About **12 min of review per contract** against about 180 min of keying. **108/108** offer rows right after review. 58 of 60 flags were already correct. | $0 (saved runs) |

Total API spend for steps 5 to 8 was about **5 cents**, because the cheap model, saved runs and free evals did most of the work.

## The trade-offs I made, and why
1. **Workflow first, agent only where needed.** The steps are fixed code: classify, route, retrieve, extract, check, review. The tool-using agent runs only on fields that fail a check, which keeps cost, latency and behaviour predictable.
2. **Confidence is built from checks code can verify, not taken from the model.**
   - Is the quote really in the source?
   - Do the business rules pass?
   - Do two runs agree?

   Red flags cap the score, so every low score has a reason a reviewer can read.
3. **Thresholds per field type, set by the cost of an error.**

   | Field type | Auto-accept at |
   |---|---|
   | Cancellation | 0.95 |
   | Price | 0.93 |
   | Dates | 0.90 |
   | Board | 0.80 |
   | Other | 0.75 |

   This is a product decision, shown to stakeholders as review load against escapes.
4. **Small model plus the right context beats a big model without it.** Retrieval made the cheap model beat the expensive one on amendments.
5. **Abstain rather than guess.** A look-alike hotel name or an ambiguous room goes to a person. Missing a stop sale or loading the wrong price is the costly failure. A flag costs 30 seconds.
6. **Gate the output.** The export reads approved versions only, so an unreviewed value can't reach the offer file.

## What the evals taught me
- **Check the ruler before the model.** Two of my biggest "model errors" were bugs in my own scorer and my own tool. Read the failures and traces before tuning prompts.
- **Easy evals tell you nothing.** Every model scored 100% on intent until I added 12 adversarial messages, such as a stop sale buried in an out-of-office reply or an Italian message.
- **Count errors by direction.** A confident wrong answer and a needless flag are not the same error. I report them separately everywhere.
- **Evaluate components separately.** Retrieval has its own free eval, so a wrong answer can be traced to "fetched the wrong page" or "misread the right page".

## Honest limitations
- The data is synthetic, and the sets are small: 3 documents, 44 intent messages, 31 retrieval cases, 28 ambiguous fields.
- Most results are single runs, so the percentages are indicative.
- The review in step 7 was simulated with gold labels.
- English only, no real supplier systems, and seasons are the only price unit, so a price change for part of a season goes to a person.

## What I'd do next, in order
1. **Tune thresholds using the review data.** 58 of 60 flags were already right. The accept decisions say where the bars are too strict, which is the fastest win on review time.
2. **Grow the eval set from real corrections.** That's the data flywheel, plus more adversarial cases, especially for cancellation terms.
3. **Shadow mode with a real team.** Run beside the people keying, compare, and measure time saved and errors caught before switching anything off.
4. **Compare embeddings against BM25** for retrieval on paraphrased and non-English messages, using the existing retrieval eval.
5. **Use an LLM judge for free-text fields** such as payment terms and notes, with a written rubric I spot-check.
6. **Integrate** with the property master, using GIATA IDs, and with the offer pipeline. Add an ARI feed for stop sales.

## The 2-minute interview story
"Contracting teams at holiday companies still type hotel contracts into their systems by hand, about 3 hours per contract. Every typo is a wrong price. I built an agent to do the reading, with one rule: **never let it be confidently wrong**.

I started with evals, not the agent: a schema, synthetic contracts in three formats, and gold labels. The baseline single model call scored 0.68, but most of that gap was a bug in my scorer. Fixed, it was 0.95. Accuracy wasn't the problem. Knowing *which* 5% was wrong was the problem.

So every field carries a source quote, and its confidence comes from checks code can verify: is the quote real, do the business rules pass, do two runs agree. Thresholds are set per field by the cost of an error, strictest for cancellation terms. That caught 5 of 6 wrong fields while sending about 16% of fields to a person.

Then the system around it. A cheap classifier routes messages: new contracts, rate and availability changes, and offers each get their own path, and non-contracts are skipped. Amendments retrieve the hotel's existing contract, which let the small model beat the large one at a fiftieth of the cost. A small tool-using agent handles only genuinely ambiguous fields, like 'the sea-facing doubles', and flags rather than guesses.

The result: a person checks about 12 minutes per contract instead of keying for 3 hours, every offer row was right on my test set, and every correction becomes new eval data. There's a live demo link if you'd like to click through it."

## Questions to expect, with short answers
- **Why not one big agent?** It's harder to test, cost and latency are unpredictable, and it fails in ways that are hard to explain. I used an agent only where the steps genuinely depend on lookups.
- **How did you pick thresholds?** By the cost of an error per field type, then checked on the review-load against escapes curve. The next step is tuning them with real accept and correct data.
- **How do you know the confidence means anything?** A calibration chart. The score is under-confident, which costs review time, not wrong prices. Its real value is ranking: the lowest bucket holds most of the errors.
- **What would you measure in production?** Review minutes per contract, escapes found after go-live (wrong fields that slipped through), the share of messages handled with no edits, cost per contract, and the time from an email arriving to the offer going live.
- **Biggest risk?** A confidently wrong cancellation or price term. That's why those fields have the strictest bars, and the export only reads approved versions.
- **Why synthetic data?** No access to real contracts. I'd validate on a small real sample in shadow mode before trusting any number here.
- **Build or buy?** The hard part isn't the model, it's the evals, the business rules and the review workflow. Those are specific to the business, which argues for owning them, whichever model provider you use.
