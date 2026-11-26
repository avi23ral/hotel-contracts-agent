# Hotel contract-to-offer agent (learning project)

Turns messy hotel contracts (PDF, email, chat) into structured offer records with per-field confidence. All hotels, people and numbers are fictional.

The key is read from `HOTEL_AGENT_API_KEY`, falling back to `ANTHROPIC_API_KEY`.

Plan: see the "Hotel contract-to-offer agent: build plan" doc in the project.

## Layout
- `src/contract_agent/schema.py`: offer schema (Contract, ChangeSet, FieldMeta, GoldLabel)
- `src/contract_agent/offers.py`: flattens a contract into static offer rows
- `data/inputs/`: synthetic inputs (PDF, .eml, chat .txt)
- `data/gold/`: hand-labelled expected outputs
- `data/offers/gold_offers.csv`: offer rows generated from the gold contracts
- `src/contract_agent/extract.py`: baseline extractor (one Claude call, structured output)
- `src/contract_agent/scoring.py`: field-by-field comparison against gold
- `evals/run_eval.py`: eval runner, saves results to `evals/results/`
- `scripts/`: `make_pdf_contract.py` builds the PDF, `build_gold.py` writes the gold JSON
- `notes/`: concept notes for interviews

## Run
```
pip install -r requirements.txt
cp .env.example .env        # then paste your key after HOTEL_AGENT_API_KEY=
python -m pytest -q         # offline checks, no key needed
python evals/run_eval.py    # runs Claude on all inputs and scores against gold
```

## Progress
- [x] Step 1: schema, 3 synthetic inputs, gold labels, offer rows
- [x] Step 2: baseline extractor and eval harness (first run: mean F1 0.675, intent 3/3)
- [ ] Step 3: evidence checks and confidence scoring
