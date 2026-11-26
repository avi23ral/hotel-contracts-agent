**In short:** about 12 minutes of checking per contract instead of about 3 hours of typing, with every offer row right on the test set, and no confident wrong answers from the agent.

| What we measured | Result |
|---|---|
| Fields extracted correctly (F1, 3 test inputs) | **0.95 to 0.97** across runs, and 1.0 after the human check |
| Wrong fields caught by the confidence checks | **5 of 6**, while sending about 16% of fields to a person. The model's own confidence caught 2 of 6. |
| Recognising what a message is (44 messages, 12 deliberately tricky) | **98%** with a small model that hands unsure cases to a large one, for about $0.40 per 1,000 messages. Keyword rules: 73%. |
| Finding the right hotel and contract section | **100%** (31 cases). With a look-alike hotel name and no other clues, it says "not sure" instead of guessing. |
| A change message read with the existing contract vs without | **0.98 vs 0.90** on the small model, which beat the large model without the contract (0.86) at about 1/50th of the cost |
| Ambiguous fields (28 cases, such as "the sea-facing doubles" or "+6 on half board") | Agent with tools: **96%, no confident wrong answers**. One model call with the contract pasted in: 86%, with 2 confident wrong answers. Fixed rules: 64%. |
| Time for a person to check | **About 12 minutes per contract** at 30 seconds per field, against about 3 hours of manual keying |
| Offer rows exactly right after review | **108 of 108** |

**Cost:**
- The small model reads a change message for well under 1 cent.
- A full contract read twice by the large model costs about 20 to 25 cents.
- Most messages never reach the large model.

**Honest caveats:**
- The data is synthetic and the test sets are small: 3 documents, 44 messages, 28 ambiguous fields.
- Most results come from a single run.
- The review was simulated with the known answers.

These numbers show how each part works. They are not production accuracy.
