# AI Learning Hub

Have you ever understood something during a conversation with an AI agent, only
to realize a few weeks later that you could no longer explain it?

I built AI Learning Hub because this kept happening to me. Useful explanations
were buried in old chats, and the understanding faded with them.

The hub turns what you learn into **blueprints**: standalone HTML lessons about
one topic. Each blueprint starts with intuition, then builds toward examples and
precise terms. If something is unclear, or your understanding changes, you can
revise it with your agent.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/index-dark.png">
  <img src="docs/index.png" alt="The learning hub index with blueprints, tags, update dates, and quiz status">
</picture>

<p align="center"><sub>Your blueprints appear in one index and link to related topics.</sub></p>

## How it works

<p align="center"><strong>Learn with an agent &rarr; Save a blueprint &rarr; Revisit it &rarr; Refine or quiz</strong></p>

The blueprint is the part worth keeping. It is a normal HTML file that opens in
a browser, works offline, and remains yours. The index is rebuilt as the hub
grows, so you can see what you have learned and what you may want to revisit.

The repository gives your coding agent the instructions and tools to maintain
that structure. You focus on learning. The agent handles the page format,
cross-links, index, and filing.

## Start your own hub

1. [Create a repository from this template](https://github.com/ArturGR3/ai-learning-hub/generate).
2. Give it a name and choose **Private**. Blueprints may contain personal notes
   or details from work conversations.
3. Clone your new repository and open it with your coding agent.
4. Give the agent this prompt:

   ```text
   Read AGENTS.md and help me set up this learning hub for my use. Explain the
   setup before changing anything. Then help me personalize how the hub teaches
   me and open the included example.
   ```

Your agent can handle the setup and explain each step. If you prefer to clone it
yourself:

```bash
git clone YOUR_PRIVATE_REPOSITORY_URL my-learning-hub
cd my-learning-hub
```

Open `index.html` in a browser to see the hub. The included blueprint is both a
real lesson and an example of the format.

## Create your first blueprint

Start a conversation with your agent from inside the hub. You can learn a new
topic:

```text
Teach me how DNS caching works. Start with the intuition and check that I
understand it. Once it is clear, help me save what I learned as a blueprint.
```

Or capture something that already came up during your work:

```text
Review what I learned in this conversation. Help me check my understanding,
then save the useful parts as a blueprint in this hub.
```

The agent reads the repository's conventions, creates the page, validates it,
adds it to the index, and records it in Git. Later, ask the agent to clarify a
weak section, add a better example, connect a related topic, or quiz you.

## Use it with your coding agent

The core workflow is not tied to one agent. It lives in `AGENTS.md`, so an agent
working inside the hub can read the same teaching and filing instructions.

Capturing a lesson while working in another repository needs a global skill
that can locate your hub. Install it once:

```bash
./scripts/install-skill.sh
```

The installer links the same versioned skill into the personal skill locations
used by all three supported agents:

| Agent | Start a learning session |
| --- | --- |
| Claude Code | Run `/learn` |
| Codex | Type `$learn` in your prompt |
| OpenCode | Ask it to use the `learn` skill |

If a `learn` skill already exists in one of those locations, the installer
moves it to a timestamped backup before creating the link. Nothing is deleted.

## Read it elsewhere

Opening `index.html` locally is the simplest and most private setup. If you want
the hub on your phone, the [deployment guide](deploy/README.md) compares a
private Pi with Tailscale setup and Cloudflare Pages.

## Background and help

This project is a learning-focused take on Andrej Karpathy's
[LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
idea: let an agent maintain a persistent body of knowledge instead of rebuilding
it from each conversation.

If something is unclear or broken, [open an issue](https://github.com/ArturGR3/ai-learning-hub/issues).
The project is [MIT licensed](LICENSE), and the blueprints you create are yours.
