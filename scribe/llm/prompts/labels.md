Say what this recording is about, in labels a library can be filtered by.

A label is a subject, not a summary: two or three words at most, lowercase,
no punctuation. `lockpicking`, `incident response`, `career switch`. Not a
sentence, not a title, and never the name of the recording.
{% if known_labels %}
The library already uses these labels:

{% for name in known_labels %}- {{ name }}
{% endfor %}
**Reuse them wherever one fits.** A label that already exists is worth more
than a better-worded new one, because filtering only works when the same
subject lands under the same word every time. Reach for a new label only when
nothing above covers a subject this recording genuinely spends time on, and
then keep it in the same style as the list.
{% else %}
The library has no labels yet, so this recording starts the vocabulary. Choose
words the next recording could reuse: `hardware hacking` will fit others,
`the time Ed lost his badge` will not.
{% endif %}
Between three and six labels. Fewer is better than padding: a subject that is
mentioned once in passing is not what the recording is about.

Answer with one JSON object of exactly this shape:

{
  "labels": [
    {
      "label": "lockpicking",
      "confidence": "high | medium | low",
      "evidence": "[12:04] \"...\""
    }
  ]
}

- `label`: the subject, in the style above. One label per entry.
- `confidence`: `high` when the recording is substantially about it; `medium`
  when it is discussed but not central; `low` when it is touched on.
- `evidence`: a quote with its `[m:ss]` that shows the subject being
  discussed. Empty string when there is none.

Use only what is below.

{{ source_label }}:

{{ transcript }}
