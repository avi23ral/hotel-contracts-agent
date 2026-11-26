# Concept note 5: RAG and retrieval evals

## The problem in one line
"Extend the early booking to end of Feb, same conditions" is meaningless on its own. Which hotel? Which stay dates? Is it combinable? The answers live in the contract we already loaded. Without it, every model, big or small, leaves those fields empty or guesses.

## What RAG means here
**Retrieval-augmented generation:** before the model extracts, we *retrieve* the relevant parts of our own data and put them in the prompt as reference material. The model then *generates* its answer with that context. It doesn't "learn" anything; it just gets to read the right page first.

We retrieve three things (`rag.py`):
1. **The hotel.** The sender's email domain is checked first (exact and deterministic), then the hotel's name, and only then text similarity. If nothing matches clearly, we **refuse to guess** and send it to a human. The wrong hotel would corrupt every field.
2. **The contract sections** that message is about: rooms, rates, seasons, offers, cancellation or children. Each loaded contract is split into these chunks. The top 3 go in the prompt, plus the hotel's identity chunk.
3. **Playbook rules:** house conventions such as "an offer change inherits every field it doesn't mention" or "match room names to the hotel's own codes".

## Why BM25 and not embeddings (yet)
BM25 is classic keyword search. Words that appear in the query score points, rare words score more, and long chunks don't win just by being long. It is free, runs locally, needs no model call, and every score can be explained. That makes it the right first baseline. Embeddings (meaning-based search) would handle paraphrase and other languages better. That's the obvious next comparison, and the retrieval eval below is what would decide it.

One practical fix: **numbers are not search terms.** At first, chat timestamps like "09:15" matched child ages and prices, and the children section outranked the offers section. Dropping bare numbers fixed it. Adding a few plain words to each chunk's heading ("special offers, promotions, discounts") also helped. That's called document expansion.

## Retrieval eval (free: no model calls)
31 messages, each labelled with the right hotel and the contract sections it needs. They include hard cases: a decoy hotel ("Hotel Marina Sol" vs "Hotel Sol de Marina"), messages identified only by a contact's first name or a destination, and one with no identifying information at all.

| Setting | Right hotel | Wrong hotel | Right sections in top k |
|---|---|---|---|
| k = 1 | 100% | 0 | 88% |
| k = 2 | 100% | 0 | 96% |
| **k = 3 (chosen)** | **100%** | **0** | **100%** |
| Text similarity only (no email/name checks) | 97% | 0 | 100% |

Without the deterministic checks, the decoy's message was ambiguous, and the system **abstained** rather than picking the wrong hotel. That's the safe failure: a reviewer spends 30 seconds instead of corrupting a contract.

**Why a separate retrieval eval matters:** when a RAG answer is wrong, you need to know if retrieval fetched the wrong page or the model misread the right page. Scoring retrieval on its own (recall@k) splits those two failure modes, and it costs nothing to run.

## End to end (chat 003, Haiku, one pass, run once)
| | F1 | Wrong | Missing | Cost |
|---|---|---|---|---|
| Haiku without RAG | 0.90 | 1 (hotel name) | 3 (offer's stay dates, combinable) | $0.0028 |
| **Haiku with RAG** | **0.98** | **0** | **1** | **$0.0035** |
| *For reference: Opus, 2 passes, no RAG (step 4)* | *0.86* | *1* | *3* | *about $0.16* |

With the contract in front of it, Haiku filled the inherited stay dates and combinability. It quoted the retrieved contract as evidence and marked those values as derived. The evidence check verified those quotes against the retrieved text, exactly as it does for the message. The hotel name now comes from our property master ("Hotel Sol de Marina", H001), not from how the sender wrote it.

Caveat: this is one input and one run. It shows the mechanism, not a statistically solid gain.

## What it cost
The whole step used about **$0.007** of API credit: one tiny key check plus two Haiku runs of the chat. Retrieval and its eval are free. I deliberately did not rerun the earlier evals.

## PM angle
- **Context beats model size here.** The small model with the right page did better than the big model without it, at about 1/50th of the cost. Before paying for a bigger model, ask what information the model is missing.
- **Abstaining is a feature.** "I'm not sure which hotel this is" routed to a human is far cheaper than a confident wrong match.
- **Retrieval is product data work.** Its quality depends on how contracts are stored and chunked, which is a data model decision as much as an ML one.

## Interview soundbite
"Amendments only make sense against the existing contract, so I added retrieval: hotel first, by email domain, then name, then text similarity, with abstention when it's ambiguous. Then the relevant contract sections and playbook rules. I evaluated retrieval separately, for free, and chose k=3 from the recall curve. End to end, Haiku with retrieval beat Opus without it on the chat amendment, 0.98 vs 0.86 F1, for well under a cent. The lesson: give the model the right context before reaching for a bigger model."
