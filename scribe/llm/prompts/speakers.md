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
      "name": "Sarah Chen",
      "role": "host | co-host | guest | expert | other",
      "confidence": 96,
      "evidence": "[1:23] \"I'm Sarah, and with me today is…\"",
      "notes": "what else is known: company, expertise, title"
    }
  ]
}

- `cluster`: the label exactly as it appears in the transcript.
- `name`: the person's full name as the transcript gives it - first name and
  surname ("Sarah Chen"), or title and surname ("Dr. Chen") - even when they
  are mostly called by their first name; a tussenvoegsel is part of the
  surname ("Robert van den Breemen"). Only the first name when that is all the
  transcript ever says. Otherwise a short role word - `Host`, `Co-host`,
  `Guest`, `Expert` - and never a guess at a name or a surname. At most four
  words. No brackets.
- `confidence`: a number from 0 to 100, not a word. Above 90 means the name is
  said in the transcript and the label it belongs to is unambiguous - a name
  above 90 will be written to the recording without anyone checking it first,
  so reserve it for what the quote actually proves. Between 50 and 90 when the
  name is there but the attribution rests on reading the conversation. Below 50
  when only a role can be given. Say 0 rather than guess.
- `evidence`: the quote or quotes, with their `[m:ss]`, that the name or role
  rests on. Empty string when there is none.

Cover every label that appears. Use only what is below.

{{ source_label }}:

{{ transcript }}
