---
name: LLM Council
description: the user asks to "ask the council", convene an LLM council, or wants several models to answer, peer-review anonymously, and have a chairman synthesize one answer (karpathy/llm-council)
---

# LLM Council (port of karpathy/llm-council)

You are the **orchestrator**. You do not answer the question yourself. You run a 3-stage
deliberation with the council's persona sub-agents, keep the anonymization honest, and present
the result.

```
question ─► STAGE 1  three members answer in parallel (identical persona and prompt)
          ─► anonymize as Response A/B/C (shuffled; map kept secret)
          ─► STAGE 2  three fresh member sessions rank ALL anonymized answers in parallel
          ─► aggregate "street cred" = average rank position
          ─► STAGE 3  the chairman sees everything (de-anonymized) and writes the final answer
```

## The council: persona sub-agents

| Seat | Sub-agent | Model, then fallbacks (best first) | Why this model |
|---|---|---|---|
| `opus` | `council-member-opus` | Opus → Opus via 2nd account → Sonnet → Sonnet via 2nd account | deepest reasoning; catches subtle errors |
| `sonnet` | `council-member-sonnet` | Sonnet → Sonnet via 2nd account → Opus → Opus via 2nd account | strong, practical, well-structured answers |
| `gemini-flash` | `council-member-gemini` | Gemini 3.8 Flash → Gemini 4 Argon → Gemini 3.5 Flash-Lite → Sonnet → Sonnet via 2nd account | different vendor = different blind spots; keeps the Claude pair honest in review |
| `chairman` | `council-chairman` | Opus → Opus via 2nd account → Sonnet → Sonnet via 2nd account | best long-context synthesis and judgment |
| `clerk` | `council-clerk` | Gemini 3.8 Flash → Gemini 3.5 Flash-Lite → Haiku | cheap mechanical work (the original also used Gemini Flash for titles) |

Each sub-agent definition pins its own model, fallback chain, thinking level, minimal tools
(Read/Write only) and watchdog timeouts. The three **member personas are identical apart from
the model**. That is deliberate: in the original, every member gets the same prompt and the
diversity comes from the models, not from roles. Seat ids are also the output file names.

Never use generic sub-agents (`explorer`, `code-reviewer`, `fixer`, …) for a council seat, and
never pass `model` when launching a seat, except for a watchdog relaunch (see below).

## Setup

1. Locate the helper:
   `H=$(ls ~/.pi-desktop/plugins/installed/*llm-council*/scripts/council.py 2>/dev/null | head -1)`;
   if empty, `find ~ -path '*llm-council*/scripts/council.py' -not -path '*/node_modules/*' 2>/dev/null | head -1`.
2. Run `python3 "$H" install-subagents --check`. If it reports missing or outdated personas, run
   `python3 "$H" install-subagents`. The council sub-agents must also appear in your `Task`
   agent list. If they don't, PI-Desktop loads sub-agents per session: tell the user to start a
   new session, and stop.
3. Create the run directory `RUN=.llm-council/runs/<YYYYmmdd-HHMMSS>` **inside the workspace**
   (sub-agents read and write workspace-relative paths), with `stage1/ stage2/ stage3/`. If no
   workspace is open, use `$PI_SCRATCH_DIR/llm-council/<timestamp>` with absolute paths.
4. Write the user's question **verbatim** to `$RUN/question.md`. If the user attached files or
   referenced code the members must see, append that content under the question.

## Briefs

The personas already carry the rules: answer plainly, stay anonymous, write the file verbatim,
reply `done`. Briefs only say what to do and where. Always start a **new** delegation, never
`resume`: a reviewer that remembers its own answer breaks anonymity.

- **Stage 1** (agent `python3 "$H" agent <seat>`):
  `Stage 1. Save your answer to <RUN>/stage1/<seat>.md.` + a blank line + `Question:` + the
  contents of question.md. For a question over ~6,000 characters, write `The question is in
  <RUN>/question.md.` instead of inlining it.
- **Title** (agent `council-clerk`): `Write the result to <RUN>/title.txt. Prompt:` + the output
  of `python3 "$H" title-prompt $RUN`.
- **Stage 2** (same member agent, fresh session):
  `Stage 2. The ranking prompt is <RUN>/stage2_prompt.md. Save your evaluation to <RUN>/stage2/<seat>.md.`
- **Stage 3** (agent `council-chairman`):
  `The chairman prompt is <RUN>/stage3_prompt.md. Save your final answer to <RUN>/stage3/chairman.md.`

If a sub-agent replies with its text instead of writing the file, write that text to the
expected path yourself, unchanged.

## Failover: no dangling seats

There are two layers. Neither ever retries a model that just failed or leaves a seat waiting.

1. **Native, inside the delegation.** When a model call errors (503, overloaded, rate limit,
   unavailable), the PI-Desktop runtime immediately switches the same delegation to the next
   model in the persona's `fallbackModels`, keeping its context. You do nothing. When the seat
   settles, record the model that actually answered:
   `python3 "$H" record $RUN <stage> <seat> <modelId from the delegation result>`.
2. **Watchdog, across delegations.** Each persona has an idle timeout (members 180 s,
   chairman 300 s, clerk 60 s) and a maximum duration (600 / 900 / 120 s). A seat that stalls,
   is stopped, ends with every native fallback exhausted, or finishes without a non-empty output
   file has **failed**. In the same turn:
   `M=$(python3 "$H" next $RUN <stage> <seat> --failed <last modelId> --reason "<short reason>")`
   This logs the event, deletes partial output and prints the next model **after** the failed
   one in the chain. Relaunch the same persona as a fresh delegation with `model: M`. If `next`
   prints `EXHAUSTED` (exit 3), drop the seat (the original's graceful degradation).

**Watch loop**: `TaskWait` with `mode: "any"`, `minCompleted: 1`. Handle each seat the moment it
settles. Do not wait for the others before recording it or relaunching it.

## Procedure

1. **Stage 1.** For each seat in {opus, sonnet, gemini-flash}, run
   `python3 "$H" next $RUN stage1 <seat>` (records the assignment), and the same for
   `title clerk`. Start the 3 member delegations and the clerk in ONE message, then run the
   watch loop. If every member seat is exhausted, stop and report "All models failed to
   respond."
2. **Anonymize.** Run `python3 "$H" stage2-prompt $RUN`. Labels map to the model that actually
   answered. Never reveal `label_to_model.json` or `seats.json` to any sub-agent.
3. **Stage 2.** Run `next $RUN stage2 <seat>` for each member seat. Start the three reviews in
   ONE message (fresh sessions; each member also ranks its own anonymized answer, as in the
   original), then run the watch loop.
4. **Aggregate.** Run `python3 "$H" aggregate $RUN`. If a line shows `PARSE WARNING`, show the
   user that reviewer's raw text.
5. **Stage 3.** Run `python3 "$H" stage3-prompt $RUN`, then `next $RUN stage3 chairman`, then
   the chairman delegation under the watch loop. If the chain is exhausted, keep going: the
   report records "Error: Unable to generate final synthesis."
6. **Report.** Run `python3 "$H" report $RUN --title "<title.txt or short title>"`.

## Presenting the result (keep the original's transparency)

1. **Final answer**: the chairman's text, unedited, labelled with the model that wrote it.
2. **Street cred**: the aggregate table (model, average rank, votes) and, for each reviewer,
   the extracted ranking with real model names in **bold**. Say plainly that reviewers only
   saw anonymous labels and the names were added afterwards.
3. **Notes**: every failover (seat: failed model → replacement, reason, from `seats.json`),
   parse warnings, and caveats that matter. For example: a failover put two copies of one model
   on the council; only Claude models reviewed, so there was no vendor diversity.
4. Link `report.md` and `transcript.json`.

Do not summarize away disagreement; if members disagreed, say so in one line.

## Fidelity notes

Same as the original: identical Stage 1 prompt for every member, verbatim ranking and chairman
prompts, strict `FINAL RANKING:` parsing with a regex fallback, average-rank aggregation,
self-inclusion in review, graceful degradation, Gemini Flash for the title.

Deliberate extensions:
- Stage 2 labels are shuffled; the original uses a fixed order, which can leak position bias.
- Failover is immediate, native first and then watchdog, along best-first chains; the original
  simply drops a failed model.
- A `report.md` is saved alongside the JSON transcript.
