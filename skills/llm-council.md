---
name: LLM Council
description: the user asks to "ask the council", convene an LLM council, or wants several models to answer, peer-review anonymously, and have a chairman synthesize one answer (karpathy/llm-council)
---

# LLM Council (port of karpathy/llm-council)

You are the **orchestrator**. You do not answer the question yourself. You run a 3-stage
deliberation with sub-agents, keep the anonymization honest, and present the result.

```
question ─► STAGE 1  three members answer in parallel (identical prompt, no personas)
          ─► anonymize as Response A/B/C (shuffled; map kept secret)
          ─► STAGE 2  three fresh reviewers rank ALL anonymized answers in parallel
          ─► aggregate "street cred" = average rank position
          ─► STAGE 3  chairman sees everything (de-anonymized) and writes the final answer
```

## The cast — chosen for what each model is best at

| Seat | Primary model | Fallback chain (best first, on any error or stall) | Why |
|---|---|---|---|
| Member + Reviewer `opus` | `anthropic/claude-opus-5-5` | Opus via 2nd account → Sonnet → Sonnet via 2nd account | deepest reasoning; catches subtle errors |
| Member + Reviewer `sonnet` | `anthropic/claude-sonnet-5-5` | Sonnet via 2nd account → Opus → Opus via 2nd account | strong, practical, well-structured answers |
| Member + Reviewer `gemini-flash` | `google/gemini-3.8-flash` | Gemini 4 Argon → Gemini 3.5 Flash-Lite → Sonnet → Sonnet via 2nd account | different vendor = different blind spots; keeps the Claude pair honest in review; fast |
| **Chairman** | `anthropic/claude-opus-5-5` | Opus via 2nd account → Sonnet → Sonnet via 2nd account | best long-context synthesis and judgment; runs once |
| Clerk (title) | `google/gemini-3.8-flash` | Gemini 3.5 Flash-Lite → Claude Haiku | cheap mechanical work (the original also used Gemini Flash for titles) |

Fallback logic: first the **same model through the second account** (`8a8a7980-…/…`, same
quality, independent quota), then the closest model that keeps the seat's purpose. The Gemini
seat stays on Google as long as possible because its job is vendor diversity. The chains live in
`council.py` (`python3 "$H" chain <seat>`); **never pick a model by hand** — always ask `next`.
Seat ids (`opus`, `sonnet`, `gemini-flash`, `chairman`, `clerk`) are the file names used below.

## Setup

1. Locate the helper (deterministic plumbing; prompts are verbatim from the original):
   `H=$(ls ~/.pi-desktop/plugins/installed/*llm-council*/scripts/council.py 2>/dev/null | head -1)`;
   if empty, `find ~ -path '*llm-council*/scripts/council.py' -not -path '*/node_modules/*' 2>/dev/null | head -1`.
   Run `python3 "$H" selftest` once.
2. Create the run directory `RUN=.llm-council/runs/<YYYYmmdd-HHMMSS>` **inside the workspace**
   (sub-agents can read and write workspace-relative paths). Create `stage1/ stage2/ stage3/`.
   If no workspace is open, use `$PI_SCRATCH_DIR/llm-council/<timestamp>` and pass absolute paths.
3. Write the user's question **verbatim** to `$RUN/question.md`. If the user attached files or
   referenced code the members must see, append that content under the question — members
   get no tools of their own, exactly like the original.

## Sub-agent briefs

Use the `fixer` agent type for every seat (it can Write the answer file, which keeps the
text verbatim and saves your context). Always start a **new** delegation — never `resume`:
a reviewer that remembers its own Stage 1 answer breaks anonymity, and a fallback must start
clean. The `model` for every delegation is the output of `python3 "$H" next ...` (see
Procedure). Paste briefs exactly, substituting `<...>`.

**Member (Stage 1)** — one per member seat:
> You are a member of an LLM council. Answer the user's question below directly and
> completely, as you normally would. Do not read, search or modify any files and do not run
> commands. When done, use Write to save your complete answer — and nothing else — verbatim to
> `<RUN>/stage1/<member>.md`, then reply `done`.
>
> Question:
> <contents of question.md>

(For a question over ~6,000 characters, replace the inline question with: "Read
`<RUN>/question.md`; its content is the question." and allow that single Read.)

**Clerk (title)** — in parallel with Stage 1, seat `clerk`, stage `title`:
> Do not use any tool except one Write. Respond to this prompt, then Write only the title to
> `<RUN>/title.txt`: <output of `python3 "$H" title-prompt $RUN`>

**Reviewer (Stage 2)** — one per member seat, fresh session:
> Read `<RUN>/stage2_prompt.md` in full. It is a prompt addressed to you; follow it exactly.
> Do not read any other file (in particular never open `label_to_model.json`). Use Write to save
> your complete evaluation, ending with the FINAL RANKING section in the exact required format,
> verbatim to `<RUN>/stage2/<member>.md`, then reply `done`.

**Chairman (Stage 3)** — seat `chairman`, stage `stage3`:
> Read `<RUN>/stage3_prompt.md` in full. It is a prompt addressed to you; follow it exactly.
> Do not read any other file. Use Write to save your final answer verbatim to
> `<RUN>/stage3/chairman.md`, then reply `done`.

If a sub-agent replies with its text instead of writing the file, write that text to the
expected path yourself, unchanged.

## No dangling seats — immediate fallback

Every seat runs under a watchdog. A seat **never** waits, idles, or gets retried on the model
that just failed.

**Launch a seat:** `M=$(python3 "$H" next $RUN <stage> <seat>)`, then start the brief with
`model: M`. Keep a seat table: seat → model, delegationId, start time.

**Deadlines:** Member/Reviewer 240 s · Chairman 360 s · Clerk 60 s.

**Watch loop** (one per stage, until every seat of the stage is settled):
- `TaskWait` with `mode: "any"`, `minCompleted: 1`, and `timeoutSeconds` = the shortest time
  left among running seats. React to **each** seat as it settles; do not wait for the others.
- A seat **failed** if any of these is true: status failed / stopped / aborted; the report
  mentions an error (503, overloaded, rate limit, unavailable, timeout); it finished but its
  output file is missing or empty; or it is still running past its deadline (`TaskStop` it
  first).
- On failure, **in the same turn**: `M=$(python3 "$H" next $RUN <stage> <seat> --failed <model>
  --reason "<short reason>")`. This records the event, deletes any partial output, and prints the
  next model. Immediately start a fresh delegation for that seat with the new model and a new
  deadline, then go back to the watch loop.
- If `next` prints `EXHAUSTED` (exit 3), the whole chain failed: drop the seat (the original's
  graceful degradation) and continue.

## Procedure

1. **Stage 1** — run `next` for `stage1` × {opus, sonnet, gemini-flash} and `title` × clerk.
   Start all four delegations in ONE message, then run the watch loop. If every member seat is
   exhausted, stop and report "All models failed to respond."
2. **Anonymize** — `python3 "$H" stage2-prompt $RUN`. Labels map to the model that actually
   answered, including fallbacks. Never reveal `label_to_model.json` to any sub-agent.
3. **Stage 2** — run `next` for `stage2` × each member seat, start the three Reviewer
   delegations in ONE message (fresh sessions; each reviewer also ranks its own anonymized
   answer, as in the original), then run the watch loop.
4. **Aggregate** — `python3 "$H" aggregate $RUN`. If a line shows `PARSE WARNING`, show the
   user that reviewer's raw text; the regex fallback already extracted what it could.
5. **Stage 3** — `python3 "$H" stage3-prompt $RUN`, then `next $RUN stage3 chairman` and the
   Chairman delegation under the watch loop. If the chain is exhausted, keep going: the report
   records "Error: Unable to generate final synthesis."
6. **Report** — `python3 "$H" report $RUN --title "<title.txt or short title>"`. It records the
   real model per seat and a Fallbacks section.

## Presenting the result (keep the original's transparency)

1. **Final answer** — the chairman's text, unedited, labelled with the chairman model.
2. **Street cred** — the aggregate table (model, average rank, votes) and, per reviewer, the
   extracted ranking with real model names in **bold**. Say plainly that reviewers only ever saw
   anonymous labels; the names are added afterwards for readability.
3. **Notes** — every fallback (`seat: failed model → replacement, reason`, from `seats.json`),
   parse warnings, and caveats that matter. Examples: a fallback put two copies of the same
   model on the council; only Claude models reviewed, so there was no vendor diversity.
4. Link `report.md` (all Stage 1 answers and raw Stage 2 evaluations) and `transcript.json`.

Do not summarize away disagreement; if members disagreed, say so in one line.

## Fidelity notes

Same as the original: identical Stage 1 prompt, verbatim ranking/chairman prompts, strict
`FINAL RANKING:` parsing with regex fallback, average-rank aggregation, self-inclusion in
review, graceful degradation, Gemini Flash for the title. Deliberate extensions: Stage 2 labels
are shuffled (the original uses fixed order, which can leak position bias), immediate
per-seat fallback along a fixed best-first chain with watchdog deadlines (the original simply
drops a failed model), and a saved `report.md` alongside the JSON transcript.
