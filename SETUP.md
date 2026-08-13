# Set up your learning hub

Use this guide when creating a hub or installing it on a new computer.

Setup creates your private copy, personalizes how agents teach you, installs the
`learn` skill, and verifies that the hub works.

## Use an existing hub on a new computer

Clone your existing private repository into the directory where you keep your
repositories:

```bash
gh repo clone YOUR_ACCOUNT/YOUR_HUB_NAME
cd YOUR_HUB_NAME
```

Then continue at **1. Verify the repository**.

## Start from the template

If you gave a coding agent only the template URL, it follows these steps. It
explains each external change before making it and asks for missing information
one question at a time.

1. Ask for the repository name and clone location if they are not known.
2. From the chosen parent directory, create and clone a private repository:

   ```bash
   gh repo create YOUR_HUB_NAME \
     --template ArturGR3/ai-learning-hub \
     --private \
     --clone
   cd YOUR_HUB_NAME
   ```

You can also create the private repository through
[GitHub's template page](https://github.com/ArturGR3/ai-learning-hub/generate),
then clone it:

```bash
git clone YOUR_PRIVATE_REPOSITORY_URL my-learning-hub
cd my-learning-hub
```

Continue from inside your new repository.

## 1. Verify the repository

Confirm that `origin` is your private copy, not
`ArturGR3/ai-learning-hub`, and that you have write access:

```bash
git remote get-url origin
gh repo view --json nameWithOwner,viewerPermission
```

Stop and correct the repository if either check is wrong.

## 2. Personalize how the hub teaches you

Rewrite **Who the user is** in `AGENTS.md`.

If an agent helps, it learns your preferences through a short conversation. It
asks one question at a time, not as a questionnaire. When your answers are
short, it drafts the section from the context it has and asks you to correct it.

Remove the placeholder owner note after you approve the personalized section.

## 3. Install the learning skill

The installer links the versioned `learn` skill into the personal skill
locations used by Claude Code, Codex, and OpenCode. It preserves existing skills
as timestamped backups.

For an interactive setup, run:

```bash
./scripts/install-skill.sh
```

When an agent or another non-interactive process runs setup, use explicit
confirmation:

```bash
./scripts/install-skill.sh --yes
```

The installer prints the links it creates. If a client is already open, restart
it before checking for the skill.

## 4. Validate the hub

Run:

```bash
python3 scripts/validate.py
```

Resolve every error. The unconfigured-owner warning disappears after
**Who the user is** no longer contains the placeholder.

## 5. Open the example

Open `index.html` in a browser, then open the included example blueprint.

Setup is complete when:

- `origin` points to your private repository and you can push;
- **Who the user is** describes you;
- the installed skill links resolve to this repository;
- validation passes without the unconfigured-owner warning; and
- the example opens with its styling applied.
