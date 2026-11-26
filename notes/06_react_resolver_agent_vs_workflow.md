# Concept note 6: ReAct, tool use, and agent vs workflow

## The problem in one line
Some fields can't be read straight off the message. "The sea-facing doubles": which room code? "+6 on HB in High": six on top of what? "Close till end of Feb": which year, and is that the 28th or the 29th? Answering means looking things up, and what you look up next depends on what the last lookup said.

## What ReAct means
**ReAct = Reason + Act, in a loop.** The model writes one line of reasoning, calls a tool, reads the result, reasons again, and stops when it can submit an answer or decides to flag the field for a human. A real trace from the eval:

```
think: I need the room code, the board code and the High season rate.
act:   lookup_room("Sea view double")  -> DSV Double Sea View (3 words match), DBL (1)
act:   lookup_board("half board")      -> HB, sold by this hotel
act:   get_rate(DSV, HB, High)         -> 200 EUR
act:   search_playbook("rate change")  -> "compute from the existing rate, mark as derived"
act:   calculate("200 + 6")            -> 206
submit: resolved 206
```

## Tools: the model decides, code answers
The tools (`tools.py`) are plain Python over our own data: `get_rooms`, `lookup_room`, `lookup_board`, `get_seasons`, `get_rate`, `resolve_date` (it adds the year from the contract period and knows 2028 is a leap year), `calculate` and `search_playbook`. A final `submit` tool ends the run with "resolved" plus a value, or "flag" plus a reason.

The model chooses which tool to call and what to make of the answer. It never supplies a fact itself. Every price, code and date comes from a logged tool result, so a reviewer can see exactly why the agent answered what it did.

Guard rails:
- At most 8 steps. Hitting the limit means the field is flagged.
- "A wrong answer costs far more than a flag" is in the instructions.
- Haiku with low effort.
- At most 5 agent runs per message.

## Agent vs workflow (the key design choice)
The pipeline stays a **workflow**: fixed steps in code (classify, route, retrieve, extract, validate). The agent runs **only on fields that fail a plain-code check** against the existing contract:
- a room code the hotel doesn't have
- a board it doesn't sell
- a date outside the contract
- a rate change with no new price

Everything else never touches it. Workflows are cheaper, faster, predictable and easy to test. Agents are worth it only where the steps can't be known in advance. That's the line to draw in an interview.

## The eval: 28 ambiguous fields, three ways, run once
The cases cover room wording, board wording, dates without a year, price deltas and release changes. 10 of them have the right answer "flag it", for example "doubles" at a hotel with two kinds of double, a board the hotel doesn't sell, a date after the contract ends, or a price change in July when July spans two seasons with different prices.

| Method | Right | Confident wrong answers | Flagged needlessly | Cost per 1,000 fields | Median time |
|---|---|---|---|---|---|
| Rules only (same tools, fixed order, no model) | 64% | 0 | 10 | free | 0 s |
| One-shot (contract pasted in, one Haiku call) | 86% | **2** | 2 | $0.29 | 2.1 s |
| **ReAct (Haiku with tools)** | **96%** | **0** | 1 | $0.87 | 2.4 s |

What each one tells you:
- **Rules** handled every room and board case, including the right flags, for free. They can't do date maths, price deltas or house rules, so everything else goes to a human. That's the workflow baseline to beat.
- **One-shot** got every price right with the contract in front of it. But it twice gave a confident wrong answer: a "breakfast only" rate (BB) and a "full board" rate (FB) at hotels that don't sell those boards. It mapped the words correctly and never checked whether the hotel sells the board. Those are the expensive errors.
- **ReAct** made no confident wrong answers. It used about 3 tools per field: 2 for a room, up to 6 for a price change. The playbook rules showed up in its reasoning ("the playbook says to flag when a room name matches two codes").

**Reading the one ReAct miss:** "Minus 10% on all-inclusive Double Room". The agent flagged it because `lookup_room` said "Double Room" matched DBL and DSV equally. The agent followed the rules correctly; the tool was wrong, since "Double Room" is DBL's exact name. I fixed the tool so an exact name wins outright, and that case now resolves to 178.2. **Read the traces before blaming the model.** It's the same lesson as step 3, where the scorer had the bug.

Caveat: 28 cases, one run. The difference in dangerous errors (2 vs 0) is the signal, not the exact percentages.

## What it cost
About **$0.04** for the whole step:
- two-case smoke test: $0.003
- one sample trace: $0.001
- the full eval: $0.033
- rerunning the fixed case: $0.002

Rules and all tests are free. No earlier evals were rerun.

ReAct costs about 3 times one-shot, because the conversation is re-sent on each step (about 2.6 model calls and 6k input tokens per field). Both are fractions of a cent per field, against a reviewer's minute.

## PM angle
- **Use an agent only where the path is unknown.** Here that's about a handful of fields per message, not the whole pipeline.
- **Tools make agents auditable.** Every fact in an answer is a logged tool result. That's what you'd show a commercial or compliance stakeholder.
- **Measure confident wrong answers separately.** One-shot and ReAct look close on accuracy (86% vs 96%), but only one of them sent 2 wrong values to customers.
- **Agent errors are often tool errors.** Treat the tools as product surface: test them, version them, own them.

## Interview soundbite
"The pipeline is a workflow, and I added an agent only for fields that fail a check against the existing contract, like an unknown room name or a price delta. It's a ReAct loop on Haiku with deterministic tools: room and board lookup, rates, date maths, a calculator and the playbook. It either submits an answer or flags the field. On 28 ambiguous fields it was right 96% of the time with no confident wrong answers. Pasting the contract into one call scored 86% and gave two wrong boards that would have gone live. The one agent miss was a bug in my room-lookup tool, which the trace made obvious. The cost was under a tenth of a cent per field."
