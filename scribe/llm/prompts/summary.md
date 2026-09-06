Summarise this recording.

Answer with one JSON object of exactly this shape:

{
  "paragraph": "one paragraph, three to six sentences",
  "bullets": ["a short point", "another short point"]
}

- `paragraph`: what this recording is, who is in it and what it comes to. Write
  it for somebody who was not there and has three minutes.
- `bullets`: three to eight short points, each one thing that was actually said
  or decided. Keep the `[m:ss]` of the moment it came from at the end of a
  bullet when there is an obvious one.

Use only what is below. If something is unclear or was never said, leave it out
rather than filling the gap.

{{ source_label }}:

{{ transcript }}
