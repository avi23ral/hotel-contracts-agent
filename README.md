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
- `src/contract_agent/tools.py`: step 6 resolver tools (room/board lookup, seasons, rates, date maths, calculator, playbook)
- `src/contract_agent/resolver.py`: step 6 ReAct resolver, its baselines, and the pipeline hook
- `data/resolver/cases.jsonl`: 28 ambiguous-field cases with expected answers (or "flag")
- `src/contract_agent/review.py`: step 7 review queue, contract versions, correction log (SQLite)
- `src/contract_agent/apply.py`: applies an approved change set to the contract as a new version
- `src/contract_agent/export.py`: offer file export (CSV / JSONL + stop sales) from approved versions only
- `src/contract_agent/orchestrate.py`: the whole flow end to end; builds the free demo replays
- `app/demo_app.py`: the review screen and demo (Streamlit)
- `data/demo/replays/`: saved runs the demo replays for free; `data/flywheel/`: logged corrections
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
- `evals/run_resolver_eval.py`: resolver eval: rules only vs one-shot vs ReAct (rules only is free)
- `evals/run_review_eval.py`: whole loop with a simulated reviewer (free): rows right before/after review
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
python evals/run_resolver_eval.py --llm       # resolver eval, Haiku, about 3 cents
python evals/run_review_eval.py               # review loop eval, free
python evals/calibration.py # chart from all saved step 3 runs
```

## Demo: try it in your browser

![Review queue](docs/screenshots/review_queue.png)

**On your laptop** (about 2 minutes):
```
git clone https://github.com/avi23ral/hotel-contracts-agent.git
cd hotel-contracts-agent
pip install -r requirements.txt
streamlit run app/demo_app.py
```
Your browser opens at http://localhost:8501.
- **Replay a sample** (the default): pick the PDF contract, the email thread or the WhatsApp chat. You see the intent, the route, what was looked up, every field with its confidence and source quote, and the review queue. Accept or correct the flagged fields, approve, and download the offer file. No API key and no cost.
- **Run live**: put your key in `.env` (see `.env.example`), switch the mode, then paste an email or chat or upload a PDF. It uses Haiku with one pass, at about 1 cent a message.

**A shareable link, free (Streamlit Community Cloud).** Nothing is deployed yet; these are the steps for when you want it:
1. Sign in at https://share.streamlit.io with your GitHub account.
2. Click "Create app", then choose this repo, branch `main` and file `app/demo_app.py`.
3. Under Advanced settings, then Secrets, add:
   ```
   HOTEL_AGENT_API_KEY = "sk-ant-..."
   DEMO_PASSWORD = "pick-one"
   DEMO_DAILY_USD = "1.0"
   ```
   Without `HOTEL_AGENT_API_KEY`, live mode is simply switched off and the link is replay only, which is safe to send to anyone.
4. Deploy. You get a link like `https://<name>.streamlit.app` to put in an email or a CV.

Safety for a public link:
- Replay mode never calls the API.
- Live mode needs the password and stops at the daily cap.
- The key lives only in the host's secrets.
- The cap is kept in a local file that resets when the free host restarts the app, so also set a monthly spend limit on the API key in the Anthropic Console. That is the hard stop.
- If the repo is public, anyone can read the code. It contains only synthetic data, and `.env` is never committed.

## Progress
- [x] Step 1: schema, 3 synthetic inputs, gold labels, offer rows
- [x] Step 2: baseline extractor and eval harness (first run: mean F1 0.675, intent 3/3)
- [x] Step 3: evidence checks, validators, per-field confidence and calibration (see notes/03)
- [x] Step 4: intent detection, router (ARI vs promotions), model cascade (see notes/04)
- [x] Step 5: RAG over existing contracts and the playbook, retrieval eval (see notes/05)
- [x] Step 6: ReAct resolver for ambiguous fields, with tools and a resolver eval (see notes/06)
- [x] Step 7: review screen, contract versions, offer file export, correction log, demo app (see notes/07)
