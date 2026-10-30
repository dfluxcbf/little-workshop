You are updating the documentation of the project `{project_id}` on branch `{branch}`, following a reviewer's comments.

{setup}

The review comments are in @{job_file} — read that file first (with your Read tool if it is not attached). Each comment is one entry in `comments`, with:

- `id` — the comment's UUID. Use it, and only it, to refer to the comment.
- `path` — the markdown file the comment was written on, relative to the repository root.
- `start_line` / `end_line` — the source lines (1-based, inclusive) of the highlighted section, as they were when the comment was written.
- `highlighted_text` — the exact text the reviewer highlighted, as it reads in the rendered document (no markdown syntax).
- `source_excerpt` — those source lines, verbatim.
- `comment` — what the reviewer asks for, in markdown.

A comment applies ONLY to its own highlighted section in its own file. Never apply one comment's request to another comment's section. If two comments touch the same area, handle them one at a time and re-read the file between them, since line numbers shift as you edit.

`certified_sections` (same file) lists sections the reviewer has CERTIFIED: `path`, `start_line`/`end_line`, `highlighted_text` and `source_excerpt`, with no comment. A certified section is, by the reviewer's own standards, well written. Treat it as correct and settled:

- Do not change it. Be highly critical and biased against any change: touch a certified section only when a comment (or keeping the rest of the document correct and consistent) makes that absolutely necessary, and then change no more than that requires.
- When you do change one, its content must not be lost and must not deviate from what it says now — keep its meaning, facts, structure and wording as far as possible.
- Say in the `note` of the comment that made it necessary that you changed a certified section, and why. If you did not have to touch any, do not mention them.
- Line numbers of certified sections are as of the branch's tip, like the comments'.

The same comments, for reference:

{comments_summary}

What to do:

1. Address every comment by editing the documentation. Keep the rest of each document as it is.
2. Commit your changes on the current HEAD with `git commit`. Do NOT push, do NOT switch or create branches, do NOT touch anything under `.review/` other than the result file below — pushing and the review bookkeeping are done for you afterwards.
3. Finally — and only once every comment is addressed and committed — write the result file `{result_file}` (JSON; create the `.review/` directory first if it is missing). Writing it is the signal that you are finished; nothing is picked up before it exists. Its shape:

```json
{
  "status": "done",
  "comments": [
    {"id": "<comment id>", "action": "edited", "path": "docs/example.md", "start_line": 12, "end_line": 18, "note": "one sentence on what you changed"},
    {"id": "<comment id>", "action": "removed", "path": "docs/example.md", "start_line": 40, "note": "why the section was removed"},
    {"id": "<comment id>", "action": "unchanged", "note": "why no edit was needed (e.g. the comment was a question — answer it here)"}
  ]
}
```

- `edited`: `start_line`/`end_line` are the lines (1-based, inclusive) of the new or rewritten section in the file as it is AFTER your commit.
- `removed`: the section was deleted entirely; `start_line` is the line in the file after your commit where it used to begin.
- `unchanged`: nothing needed changing.
- Every comment id from the job file must appear exactly once.
