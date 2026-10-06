You are reading excerpt {{ index }} of {{ count }} from a longer recording,
covering {{ start }} to {{ end }} of it.

Somebody else will read your notes together with the notes from the other
excerpts and then do this task: {{ goal }}

Take the notes that task will need. Plain lines, not JSON, each keeping the
`[m:ss]` it came from. Only what is in this excerpt: you cannot see the rest,
so do not conclude anything about the recording as a whole, and do not write an
opening or closing sentence. If the excerpt holds nothing the task needs, say
that in one line.
{% if speakers %}

Begin every note with the speaker of the line it came from, as the line names
them: who said something is part of what the task needs.
{% endif %}

Excerpt:

{{ transcript }}
