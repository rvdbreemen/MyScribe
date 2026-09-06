Divide this recording into chapters.

Answer with one JSON object of exactly this shape:

{
  "chapters": [
    {"start": 0.0, "title": "What the first stretch is about"}
  ]
}

- `start`: when the chapter begins, either as seconds or copied as `m:ss` from
  the line it starts at. The first chapter starts at the beginning.
- `title`: five to eight words, saying what changes here. Not "Introduction"
  unless the introduction is what it is.

Between four and twelve chapters for an hour, fewer for something shorter, and
in time order. A chapter boundary is a change of subject, not a fixed interval.

{{ source_label }}:

{{ transcript }}
