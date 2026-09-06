Write the minutes of this meeting.

Answer with one JSON object of exactly this shape:

{
  "agenda": ["what was discussed, in the order it came up"],
  "decisions": ["what was settled, stated as the decision"],
  "actions": [
    {"text": "what is to be done", "owner": "who took it on", "evidence_ts": 83.0}
  ]
}

- `agenda`: the subjects actually covered - what happened, not what was planned.
- `decisions`: only things that were settled. A discussion that ended without a
  conclusion is not a decision; say so in `agenda` instead.
- `actions`: as an action list - `owner` and `evidence_ts` may be `null`, and
  `evidence_ts` may be copied as `m:ss`.

Any of the three lists may be empty. An honest empty list is worth more than an
invented entry.

{{ source_label }}:

{{ transcript }}
