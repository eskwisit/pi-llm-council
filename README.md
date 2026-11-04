# LLM Council for PI-Desktop

A port of [karpathy/llm-council](https://github.com/karpathy/llm-council). Instead of asking one
model, the agent convenes a council of persona sub-agents. It runs in three stages:

1. **Stage 1, first opinions**: Opus, Sonnet and Gemini Flash answer the same question in
   parallel.
2. **Stage 2, review**: the answers are shuffled and anonymized as Response A/B/C. Each member
   ranks them all in a fresh session, using a strict `FINAL RANKING:` format, and the ranks are
   averaged into an aggregate "street cred" score.
3. **Stage 3, final response**: the Chairman sees every answer and every review, and writes one
   final answer.

## What is in the box: an agent plus sub-agents, shipped as a plugin

| Part | Where | Role |
|---|---|---|
| Orchestrator agent | `skills/llm-council.md`, a plugin skill | Turns the session agent into the council's orchestrator: runs the stages, keeps answers anonymous, handles failover, presents the result |
| Persona sub-agents | `subagents/*.md`, installed to `~/.agents/subagents/` | `council-member-opus`, `council-member-sonnet`, `council-member-gemini`, `council-chairman`, `council-clerk`. Each pins its model, fallback chain, thinking level, Read/Write-only tools and watchdog timeouts |
| Helper | `scripts/council.py` | Anonymization, ranking parser, aggregation, Karpathy's prompts (byte-identical), fallback bookkeeping, reports and persona installation |

A PI-Desktop plugin can contribute skills but not sub-agents, and sub-agents can only be defined
globally in `~/.agents/subagents/`. So the plugin ships the persona files, and the skill installs
them (`council.py install-subagents`) on first use. Start a new session afterwards so PI-Desktop
picks them up.

The three member personas are **identical apart from their model**. That is faithful to the
original, where every member gets the same prompt and the diversity comes from the models
themselves.

## Usage

1. Install the `.piplug` from the plugins page, or load this folder for development.
2. Run `python3 scripts/council.py install-subagents`, or let the skill do it, then start a new
   session.
3. Ask: "Ask the council: …"

Each run is saved under `.llm-council/runs/<timestamp>/` in your workspace, as `report.md` and
`transcript.json`.

## Casting and failover

| Seat | Sub-agent | Chain (best first) |
|---|---|---|
| Member | `council-member-opus` | Opus → Opus via 2nd account → Sonnet → Sonnet via 2nd account |
| Member | `council-member-sonnet` | Sonnet → Sonnet via 2nd account → Opus → Opus via 2nd account |
| Member | `council-member-gemini` | Gemini 3.8 Flash → Gemini 4 Argon → Gemini 3.5 Flash-Lite → Sonnet → Sonnet via 2nd account |
| Chairman | `council-chairman` | Opus → Opus via 2nd account → Sonnet → Sonnet via 2nd account |
| Clerk (title) | `council-clerk` | Gemini 3.8 Flash → Gemini 3.5 Flash-Lite → Haiku |

Failover never retries a failed model and never leaves a seat dangling:

- **Native**: if a model call errors (503, overloaded, rate limit), the PI-Desktop runtime
  switches the same delegation to the next model in the persona's `fallbackModels`, keeping its
  context.
- **Watchdog**: if a seat stalls past its idle timeout or maximum duration, or ends without
  output, the orchestrator relaunches that persona on the model after the failed one.

Every substitution is logged in `seats.json` and in the report, so you always know which model
actually answered.

The persona files are the single source of truth for models and chains; the helper reads its
chains from them. To change the cast, edit `subagents/*.md`, then reinstall them.

## Credits

Based on [karpathy/llm-council](https://github.com/karpathy/llm-council). The Stage 2 ranking,
Stage 3 Chairman and title prompts, and the ranking parser, are reproduced from its
`backend/council.py`. That repository does not declare a license.
