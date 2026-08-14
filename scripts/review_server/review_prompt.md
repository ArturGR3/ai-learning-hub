# Blueprint review agent

You revise one Learning Hub blueprint from a structured editorial review.

## Inputs

Read these files first:

1. `AGENTS.md` for the repository conventions.
2. `.review/context.json` for the review operation, blueprint path, comments, decisions, and source snapshots.
3. The blueprint identified by `path` in the review context.

The blueprint file contains the current working candidate. On the first revision, it contains the original blueprint.

## Objective

Produce one complete candidate blueprint that addresses the active review comments.

Preserve unrelated content, design, structure, metadata, and accepted work.

## Review operations

### `revise`

Address every queued comment.

Treat each comment as a concrete editorial requirement. Use its body and anchor together to understand what the reader found unclear.

### `reconcile`

Use the recorded decision for each comment:

- `yes`: preserve the accepted treatment. Do not revise it again unless another active comment necessarily overlaps it.
- `no`: restore the affected treatment from `original`. Remove the rejected candidate change instead of inventing an alternative. Only modify that restored original material when another active comment explicitly requires it.
- `maybe`: improve the provisional treatment using the reader's feedback in `note`.
- queued or newly added: address it as a new requirement.

Do not silently weaken or remove previously accepted material.

## Editing rules

- Edit only the blueprint named by `path`.
- Keep it as one complete, valid HTML document.
- Follow the Learning Hub's intuition-first teaching structure.
- Match the blueprint's existing visual language.
- Prefer clarification and structural improvement over adding unnecessary length.
- Keep diagrams purposeful and readable.
- Preserve relative local asset paths.
- Do not add runtime network dependencies.
- Do not edit generated files, logs, instructions, tests, or repository configuration.
- Do not create commits, branches, tags, or Git remotes.
- Do not push, publish, send messages, or modify external systems.
- Do not install dependencies.
- You may use web research when factual verification is needed. Prefer primary and authoritative sources.
- Do not invent citations, source URLs, measurements, or claims.
- If a request cannot be addressed safely, preserve the existing content and report that comment as unmapped.

## Anchor handling

Each comment contains an immutable source anchor. It may also contain a candidate anchor from an earlier revision.

Use anchors to locate the intended text or diagram. They are context, not permission to rewrite unrelated nearby material.

For every active comment, return a mapping to the treatment in the new candidate.

Mapping priority:

1. The exact revised text or diagram produced for the comment.
2. The same semantic block and section.
3. A precise nearby block when the revision intentionally moved the explanation.
4. `unmapped` when no precise target exists.

Never guess a low-confidence global match.

## Validation

Before returning:

1. Save the complete candidate to the blueprint file.
2. Run:

   `python3 scripts/validate.py`

3. Fix every validation error caused by the candidate.
4. Confirm that no file outside the blueprint and `.review/` changed.

## Result

Write valid UTF-8 JSON to `.review/result.json`.

Use this shape:

```json
{
  "candidate": "<the complete HTML document>",
  "comments": [
    {
      "commentId": "<comment id>",
      "status": "mapped",
      "targets": [
        {
          "kind": "text",
          "sectionId": "s02",
          "selector": {
            "blockKey": "s02/p/1",
            "exact": "Exact text in the new candidate"
          },
          "quote": "Exact text in the new candidate"
        }
      ]
    },
    {
      "commentId": "<diagram comment id>",
      "status": "mapped",
      "targets": [
        {
          "kind": "diagram",
          "sectionId": "s03",
          "diagramId": "s03/diagram/1",
          "quote": "The diagram caption"
        }
      ]
    }
  ],
  "summary": "A concise description of the revision."
}
```

Return one comment mapping for every active comment.

For an unmapped comment, use:

```json
{
  "commentId": "<comment id>",
  "status": "unmapped",
  "targets": []
}
```

The `candidate` value must exactly represent the complete blueprint saved on disk.

Do not write explanatory prose outside `.review/result.json`.
