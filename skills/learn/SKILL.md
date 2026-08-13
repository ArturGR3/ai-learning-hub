---
name: learn
description: Trigger a learning session. Crystallize understanding into a blueprint - teach a concept from scratch, capture learnings from a work session, or generate a blueprint directly.
---

# Learn

Invoke this skill when you want to crystallize understanding into a blueprint.

A learning hub is a repo of HTML blueprints with `topics/`, `scripts/`, and an
`AGENTS.md` holding its conventions. This skill can be invoked from any project;
the hub is usually somewhere else on disk.

## Step 1: Locate the hub and read its conventions

This file lives inside the hub at `<hub>/skills/learn/SKILL.md`. The installer
links that directory into the personal skill locations used by supported coding
agents.

Find the hub in this order:

1. `$LEARNING_HUB`, if set.
2. The real path of this active skill directory, when the client provides it.
   Resolve symlinks, then move up from `skills/learn` to the hub root.
3. The installed skill locations:
   - `~/.agents/skills/learn`
   - `~/.claude/skills/learn`

   Resolve symlinks, then move up from `skills/learn` to the hub root.
4. The current directory or any ancestor of it - you may already be in the hub.
5. **Ask the user where their hub is.**

A valid hub contains `topics/` and `scripts/file-blueprint.sh`. Validate every
candidate before using it. If several candidates point to different valid hubs,
list them and ask which one to use.

**Never fetch anything, never guess a path, and never clone or create a hub
uninvited.**

Then read `AGENTS.md` from the hub you found. It is self-contained and it
governs everything below: the teaching method, the template patterns, the
skeleton, the quiz protocol, the local-first asset rules, and the filing
checklist. Where this skill and that file disagree, **that file wins**. Read
`topics/` to see what already exists, for cross-referencing.

If you have no filesystem access at all, say so and work from what the user can
paste.

## Step 2: Determine the mode

Infer the mode from the user's request. Do not ask the user to choose a mode.
Start with the first action for the inferred mode.

### TEACH - "teach me X" / "I want to understand X"

A dedicated learning session.

1. **Establish the starting point**: Tell the user that you first want to
   understand their current mental model so the teaching starts in the right
   place. Ask them to explain how they currently understand the topic in their
   own words. Mention that they can use voice input if it is available and
   easier for a longer answer. Do not ask them to label themselves as a
   beginner, intermediate, or expert.
2. **Find the gaps**: Identify what the user understands, where they are
   uncertain, and which connections are missing or incorrect. Ask only the
   follow-up questions needed to choose a starting point, one at a time. Do not
   use a fixed questionnaire. Then summarize the starting point and gaps, and
   let the user correct your assessment.
3. **Teach**: Build from the assessed starting point. Skip material the user
   already understands except where it anchors a new connection. Use intuition
   first, then examples, then precise terms. Use diagrams where they earn their
   place. Check understanding as you go.
4. **Crystallize**: Afterwards ask "want me to crystallize this into a
   blueprint?" If yes, generate from the teaching and file it (step 3). If no,
   offer a quiz.

### CRYSTALLIZE - "summarize what we learned" / "capture this session"

The teaching already happened, embedded in the work.

1. **Outline**: Review the conversation for concepts worth capturing. Propose
   "here are N topics: 1. X, 2. Y, 3. Z." Let the user change the list.
2. **Check understanding**: For each, ask whether it's clear. Fill gaps with
   mini-teaching. The "Remember" callouts should come from real gaps found here,
   not generic statements.
3. **Crystallize**: Generate each blueprint from the conversation plus that
   feedback. File each.

### BUILD - "build a blueprint for X"

The user knows what they want and wants the artifact.

1. **Generate** per the hub's `AGENTS.md`. Use the skeleton, fill all metas,
   maintain cross-references.
2. **File** (step 3).

## Step 3: File the blueprint

With repo access:

1. Save to `topics/<slug>.html` in the hub found in step 1.
2. Asset paths are relative - nothing may load over http. The hub's conventions
   give the exact head block, including the math setup.
3. Maintain cross-references: link related blueprints both directions, editing
   those blueprints to link back.
4. **File with the script - never run git commands manually:**

   ```bash
   ./scripts/file-blueprint.sh topics/<slug>.html "{{topic}} - {{one-line summary}}"
   ```

   It validates, rebuilds the index, appends the log, commits, and pushes if a
   remote is configured. Do not run `git add`, `git commit`, `git push`, or the
   index builder yourself.
5. Open the filed blueprint from disk and confirm it renders: styling applied,
   math typeset, no errors.

Without repo access, output the complete HTML for the user and note any
cross-references for follow-up. Do not link to blueprints you cannot verify
exist.

## Plan mode

Teaching, outlining, and generating HTML are all text output and all allowed.
Filing writes files, so ask the user to exit plan mode for that. Don't present a
"plan" of blueprint sections - the conventions define the structure. Just teach,
outline, or build.

## After filing

Offer to quiz the user, following the protocol in the hub's `AGENTS.md`. One
question at a time, probe the model not recall, end with synthesis.
