Turn this recording into a blog post.

Answer with one JSON object of exactly this shape:

{
  "title": "a title that says what the post is about",
  "body": "## A heading\n\nMarkdown, several paragraphs."
}

- `title`: plain words, no clickbait, no colon-subtitle unless the subject has
  one.
- `body`: Markdown. Headings, paragraphs, lists and quotes; no front matter, no
  top-level `#` heading repeating the title. Write it as prose that stands on
  its own - a reader of the post has not heard the recording.

Everything in it must come from the recording. Quote a speaker where the exact
words matter, and keep the `[m:ss]` next to a quote so a reader can go and hear
it.

{{ source_label }}:

{{ transcript }}
