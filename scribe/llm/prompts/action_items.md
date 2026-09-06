Pull out the things somebody agreed to do.

Answer with one JSON object of exactly this shape:

{
  "items": [
    {"text": "what is to be done", "owner": "who said they would", "evidence_ts": 83.0}
  ]
}

- `text`: one action, phrased as the doer would write it on a list.
- `owner`: the name or speaker label of the person who took it on, or `null`
  when nobody did. Do not guess an owner from who talks most.
- `evidence_ts`: the timestamp of the line the action came from, either as
  seconds or copied as `m:ss`. `null` when you cannot point at one line.

Only what was actually agreed or asked for. An idea nobody took on is not an
action item, and `items` may be an empty list - that is a real answer.

{{ source_label }}:

{{ transcript }}
