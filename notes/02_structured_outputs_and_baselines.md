# Concept note 2: structured outputs, baselines and field-level evals

## What we built
- `extract.py`: **one** Claude call. It gets the source text and a system prompt with house rules, and must return JSON that matches our schema.
- `scoring.py`: compares the output with gold **field by field**.
- `run_eval.py`: runs all inputs, prints precision, recall and F1, and saves every run so we can compare runs over time.

## Structured outputs
We pass a Pydantic model as `output_format`. The API constrains the model to return JSON that fits that schema, and the SDK validates it. That gets rid of a whole class of bugs ("the model forgot a bracket", "price came back as 'EUR 95'"). It doesn't make the *values* right. That's what the eval is for.

## Why start with a baseline
This is the simplest version that could work: no routing, no RAG, no agent. Every later step has to beat its score. If an agent loop costs 5x more and only improves F1 by 0.01, that's a product decision you can now make with data. Without a baseline you can't tell.

## How the scoring works (and why it's designed this way)
- **Lists are matched by meaning, not position.** A rate is identified by season, room and board, so the order doesn't matter.
- **Numbers get a small tolerance** (0.01). Text is compared case-insensitively. Free-text fields (hotel wording, payment terms) are left out for now. They'll need an LLM judge.
- We count four outcomes per field: **correct**, **wrong** (the dangerous one: a confident wrong price), **missing** (safe but costs reviewer time) and **extra** (possible hallucination).
- Precision = correct ÷ everything the model said. Recall = correct ÷ everything it should have said.

## PM angle
Precision and recall map straight to business cost. Low precision means wrong prices go live. Low recall means humans re-key fields, so less time is saved. Which one matters more differs by field: a wrong price costs far more than a missing city-tax note.

## Interview soundbite
"I started with a single structured-output call as the baseline and scored it field by field against gold. Splitting errors into wrong, missing and hallucinated showed where to invest: wrong fields need better confidence, missing fields need better recall."
