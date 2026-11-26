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
- `src/contract_agent/extract.py`: one Claude call, structured output, evidence notes per field
- `src/contract_agent/intent.py`: step 4 intent detection (multi-label, Haiku, rules baseline, cascade)
- `src/contract_agent/router.py`: routes each intent to its pipeline (new contract / ARI / promotion / ignore)
- `src/contract_agent/rag.py`: step 5 retrieval: property master, contract chunks, playbook, BM25
- `data/store/`: property master (`hotels.json`) and existing contracts; `scripts/build_store.py` writes them
- `data/playbook.md`: house rules retrieved alongside the contract
- `src/contract_agent/pipeline.py`: step 3 flow: two passes, normalise, validate, check evidence, score
- `src/contract_agent/evidence.py`: checks each quote is really in the source and holds the value
- `src/contract_agent/validators.py`: deterministic business rules (seasons, cancellation, prices...)
- `src/contract_agent/confidence.py`: combined confidence score, per-field thresholds, cost weights
- `src/contract_agent/scoring.py`: field-by-field comparison against gold
- `evals/run_eval.py`: eval runner, saves results to `evals/results/` (`--rescore` re-scores an old run)
- `evals/run_intent_eval.py`: intent eval on `data/intents/messages.jsonl` (rules vs Haiku vs Opus vs cascade)
- `evals/run_retrieval_eval.py`: retrieval eval (free, no model calls): right hotel, right sections at k
- `evals/run_rag_eval.py`: chat amendment with vs without RAG, on Haiku, prints cost
- `evals/calibration.py`: calibration chart and threshold trade-off (`evals/results/calibration.png`)
- `scripts/`: `make_pdf_contract.py` builds the PDF, `build_gold.py` writes the gold JSON
- `notes/`: concept notes for interviews

## Run
```
pip install -r requirements.txt
cp .env.example .env        # then paste your key after HOTEL_AGENT_API_KEY=
python -m pytest -q         # offline checks, no key needed
python evals/run_eval.py    # routes and extracts all inputs, scores against gold (--direct skips the router)
python evals/run_intent_eval.py --repeats 2   # intent detection eval
python evals/run_retrieval_eval.py --k 3      # retrieval eval, free
python evals/run_rag_eval.py                  # RAG on vs off, Haiku, under 1 cent
python evals/calibration.py # chart from all saved step 3 runs
```

## Progress
- [x] Step 1: schema, 3 synthetic inputs, gold labels, offer rows
- [x] Step 2: baseline extractor and eval harness (first run: mean F1 0.675, intent 3/3)
- [x] Step 3: evidence checks, validators, per-field confidence and calibration (see notes/03)
- [x] Step 4: intent detection, router (ARI vs promotions), model cascade (see notes/04)
- [x] Step 5: RAG over existing contracts and the playbook, retrieval eval (see notes/05)
