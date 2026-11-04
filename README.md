# LLM Council for PI-Desktop

Ask one model and you get one answer, with no way to tell whether it is great or mediocre. The
council runs your question through **five advisors**. Each thinks from a deliberately different
angle on the model best suited to that angle. They review each other anonymously, and a chairman
delivers a verdict: where they agree, where they clash, what they all missed, what to do, and the
one thing to do first.

It combines two designs:

- [karpathy/llm-council](https://github.com/karpathy/llm-council): several models answer, rank
  each other's anonymized answers into a leaderboard, and a chairman synthesizes the result.
- [Ole Lehmann's llm-council skill](https://github.com/aiwithremy/claude-skills-llm-council):
  five thinking lenses that create tension, context framing, blind-spot reviews and a structured
  verdict.

## The council

| Advisor | Model | Lens |
|---|---|---|
| The Contrarian | Opus | Assumes there is a fatal flaw and finds it |
| The First Principles Thinker | Opus | What are we actually trying to solve? Reframes the question |
| The Expansionist | Gemini Flash | The upside everyone is missing |
| The Outsider | Gemini Flash | Fresh eyes, no context; catches the curse of knowledge |
| The Executor | Sonnet | Can it be done, and what do you do Monday morning? |
| Chairman | Opus | Synthesizes the answers and reviews into the verdict |
| Clerk | Gemini Flash | Titles the run |

The lenses create three tensions:
- **Contrarian ↔ Expansionist:** downside against upside.
- **First Principles ↔ Executor:** rethink it against just do it.
- **The Outsider** keeps everyone honest.

The model mix adds Karpathy's diversity: two vendors with different blind spots, reviewing each
other.

## How a session runs

1. **Frame.** The orchestrator reads the relevant workspace context and writes one neutral,
   specific question. If the question is too vague, it asks one clarifying question first.
2. **Stage 1.** The five advisors answer in parallel, 150–300 words each, fully committed to their
   angle.
3. **Stage 2.** The answers are shuffled into Response A–E. Each advisor, in a fresh session,
   evaluates them all and names the strongest, the biggest blind spot and what everyone missed. It
   ends with Karpathy's strict `FINAL RANKING:`. The rankings are averaged into a "street cred"
   table.
4. **Stage 3.** The chairman sees every answer with its advisor and model, every review and the
   table, and writes the verdict. The chairman may side with a dissenter.

Each run is saved under `.llm-council/runs/<timestamp>/` in your workspace as `report.md` and
`transcript.json`.

## What is in the box: an agent plus sub-agents, shipped as a plugin

| Part | Where | Role |
|---|---|---|
| Orchestrator agent | `skills/llm-council.md`, a plugin skill | Framing, stages, anonymity, failover, presentation |
| Persona sub-agents | `subagents/*.md`, installed to `~/.agents/subagents/` | `council-contrarian`, `council-first-principles`, `council-expansionist`, `council-outsider`, `council-executor`, `council-chairman`, `council-clerk`. Each pins its lens, model chain, thinking level, Read/Write tools and timeouts |
| Helper | `scripts/council.py` | Prompts, anonymization, ranking parser, per-advisor scoring, failover bookkeeping, reports, persona install |

PI-Desktop plugins can contribute skills but not sub-agents, and sub-agents are global-only. So the
plugin ships the persona files, and the skill installs them on first use with
`council.py install-subagents`.

## Usage

1. Install the `.piplug` from the plugins page, or load this folder for development.
2. Run `python3 scripts/council.py install-subagents`, or let the skill do it. If the council
   personas don't show up, start a new session.
3. Say "council this: …", "pressure-test this", or bring a real decision.

The council is for decisions with real stakes, not for lookups or writing tasks.

## Anonymity and failover

**Anonymity.** While reviews run, the label map and the original answers sit in
`~/.llm-council/private/`, outside every path a sub-agent can read. Reviewers only ever see
Response A–E.

**Failover.** No seat dangles and no failed model is retried. Failover has three layers:
- **Native:** PI-Desktop switches a delegation to the persona's next model on a model error.
- **Heartbeat:** a seat with no activity 45 s after launch is relaunched on the next model.
- **Watchdog:** idle and maximum-duration limits catch a seat that stalls later.

A model that fails anywhere in a run is skipped for every later seat. Every substitution is logged
in the report, with the model that actually answered. The persona files are the single source of
truth for models and chains (`python3 scripts/council.py chain <seat>`).

## Credits

Based on [karpathy/llm-council](https://github.com/karpathy/llm-council). The ranking, chairman and
title prompts keep his text, with additions, and the ranking parser extends his. The advisor lenses,
framing step, review questions and verdict structure are adapted from
[Ole Lehmann's llm-council skill](https://github.com/aiwithremy/claude-skills-llm-council). Neither
repository declares a license.
