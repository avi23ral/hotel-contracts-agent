# Concept note 1: schema first, evals first

## What we did
Before writing any prompt, we defined the output schema (`src/contract_agent/schema.py`) and hand-labelled the correct answer for 3 synthetic inputs (`data/gold/`). There's a clean PDF, a negotiation email thread and a WhatsApp chat.

## Why it matters
- **The schema is the product spec.** Deciding that a rate is `season × room × board → price`, and that prices are per room or per person, is a product decision. The model just fills it in. A vague schema gives vague output that nobody can review.
- **Gold labels define "correct" before you're biased by what the model outputs.** If you label after seeing model answers, you tend to accept them.
- **Each example is designed to test something** (`tests_what` in each gold file). For example, the email thread checks that the *latest* agreed price wins over the first proposal and the buyer's counter-offer. That's a classic LLM failure.
- **Deterministic checks are free quality signals.** The schema rejects overlapping seasons, rates for rooms that don't exist, and seasons outside the contract dates. Later these checks lower a field's confidence automatically.

## Design choice worth explaining in an interview
Confidence and evidence live in a separate `field_meta` map keyed by path (`rates[3].price`), not inside every field. That keeps gold labels clean and the eval simple (compare Contract to Contract). The review UI can still show a score and quote for every field.

## The static offer file
`offers.py` flattens a contract into bookable rows. Two contracts produce 108 rows, and that's with children fixed at 0 and no offer variants. Add child ages, offers, flight pairings and every hotel, and you see why operators talk about offer sets in the billions. It also shows why one wrong price in a contract fans out into hundreds of wrong offers.

## Interview soundbite
"I wrote the eval set before the agent. Each example targets a known failure mode, like latest-message-wins in email threads or amendments mistaken for new contracts, so every prompt change is measured, not eyeballed."
