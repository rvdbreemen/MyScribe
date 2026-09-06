Work out who each speaker in this recording is.

Every line below starts with its time and then a label like `SPEAKER_00:`.
Those labels come from a voice-clustering step that knows nothing about
names; your job is to map each label to the person behind it, from what is
actually said.

Go about it in this order, and keep the evidence:

1. **Names said out loud.** Self-introductions ("I'm Sarah", "my name is"),
   introductions by others ("welcome Sarah", "this is Sarah from…"), direct
   address ("Sarah, what do you think?", "thanks Sarah"), and titles
   ("Dr. Chen"). Note *who says* the name and *who answers to it*: a name a
   speaker is called by belongs to that speaker, not to the one saying it.
2. **Roles from behaviour.** A host opens the show, welcomes guests, asks the
   questions, moves between topics, thanks people at the end. A guest is
   introduced, answers, talks about their own work or background. A co-host
   shares the opening and asks questions alongside the host.
3. **One label per person.** If two labels are clearly the same voice split
   by the clustering (they finish each other's sentences, are addressed by
   the same name), say so in the evidence and give both the same name.

Answer with one JSON object of exactly this shape:

{
  "format": "interview | panel | conversation | monologue | other",
  "speakers": [
    {
      "cluster": "SPEAKER_00",
      "name": "Sarah",
      "role": "host | co-host | guest | expert | other",
      "confidence": "high | medium | low",
      "evidence": "[1:23] \"I'm Sarah, and with me today is…\"",
      "notes": "what else is known: company, expertise, title"
    }
  ]
}

- `cluster`: the label exactly as it appears in the transcript.
- `name`: the actual first name (or title and surname) when the transcript
  supports it; otherwise a short role word - `Host`, `Co-host`, `Guest`,
  `Expert` - and never a guess at a name. One or two words. No brackets.
- `confidence`: `high` when named in the text and the label matches
  unambiguously; `medium` when the name is there but the attribution rests on
  reading the conversation; `low` when only a role can be given.
- `evidence`: the quote or quotes, with their `[m:ss]`, that the name or role
  rests on. Empty string when there is none.

Cover every label that appears. Use only what is below.

{{ source_label }}:

{{ transcript }}
