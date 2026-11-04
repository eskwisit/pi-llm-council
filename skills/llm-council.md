---
name: LLM Council
description: the user says "council this", "ask the council", "pressure-test this", or brings a real decision or tradeoff they want examined by several advisors, peer-reviewed anonymously and settled by a chairman verdict
---

# LLM Council

You are the **orchestrator**. You do not answer the question yourself. You cast the council from
the models this user actually has, frame the question, convene five advisor sub-agents, run an
anonymous peer review, have the chairman write the verdict, and present the result.

The design combines two sources:
- **Karpathy's llm-council:** different models, anonymized peer ranking, a chairman synthesis and an
  aggregate leaderboard.
- **Ole Lehmann's llm-council skill:** five thinking lenses that deliberately pull against each
  other, context framing, blind-spot review questions and a structured verdict.

```
question ─► CAST      read the user's delegation models, assign one per seat (reuse a saved cast)
          ─► FRAME     read workspace context, write one neutral framed question
          ─► STAGE 1  five advisors answer in parallel, each from its lens, on its cast model
          ─► anonymize as Response A–E (shuffled; map and answers moved out of reach)
          ─► STAGE 2  five fresh sessions review ALL answers: strongest / blind spot / all missed + FINAL RANKING
          ─► aggregate "street cred" = average rank per advisor
          ─► STAGE 3  chairman sees everything de-anonymized and writes the verdict
```

## When to convene, and when not to

Convene for genuine uncertainty where a bad call is expensive: a decision between options, a plan,
a strategy, a piece of work to pressure-test, or "am I crazy to…".

Do **not** convene for:
- a question with one right answer, or a lookup: just answer it;
- a creation or processing task ("write a tweet", "summarize this").

If the user explicitly asks for the council anyway, run it.

## The council

| Seat | Sub-agent | Lens | Model to cast |
|---|---|---|---|
| `contrarian` | `council-contrarian` | Assumes a fatal flaw and finds it | Strongest reasoning model |
| `first-principles` | `council-first-principles` | What are we really solving? Reframes | Strongest reasoning model |
| `expansionist` | `council-expansionist` | The upside everyone misses | Strong, ideally another vendor or family than the chairman's |
| `outsider` | `council-outsider` | Fresh eyes, no context, curse of knowledge | Strong, ideally another vendor or family than the chairman's |
| `executor` | `council-executor` | Can it be done? What happens Monday morning? | Strong practical "balanced" model |
| `chairman` | `council-chairman` | Writes the verdict | Strongest reasoning model with long context |
| `clerk` | `council-clerk` | Writes the title | Cheapest fast model |

The lenses create three tensions:
- **Contrarian ↔ Expansionist:** downside against upside.
- **First Principles ↔ Executor:** rethink it against just do it.
- **Outsider:** keeps everyone honest.

The personas carry lens, tools (Read/Write; the clerk has only Write) and timeouts, but **no
model**. Models are cast per user, at run time, from their own catalog. Never use generic
sub-agents (`explorer`, `fixer`, …) for a seat.

## Setup

1. **Locate the helper.**
   `H=$(ls ~/.pi-desktop/plugins/installed/*llm-council*/scripts/council.py 2>/dev/null | head -1)`.
   If that is empty, use
   `find ~ -path '*llm-council*/scripts/council.py' -not -path '*/node_modules/*' 2>/dev/null | head -1`.
2. **Check the personas.** Run `python3 "$H" install-subagents --check`.
   - If anything is missing, outdated or obsolete, run `python3 "$H" install-subagents`.
   - The seven `council-*` sub-agents must appear in your Task agent list. If they don't, tell the
     user to start a new session, and stop.
3. **Create the run directory.** Use `RUN=.llm-council/runs/<YYYYmmdd-HHMMSS>`, with
   `stage1/ stage2/ stage3/`, **inside the workspace**: sub-agents read and write
   workspace-relative paths.
   - If no workspace is open, use `$PI_SCRATCH_DIR/llm-council/<timestamp>` with absolute paths.
   - The helper keeps the cast, the failover log and the secret label map in
     `~/.llm-council/private/`, out of every sub-agent's reach.

## Step 0: cast the council

1. **Record the catalog.** Pipe the "Available models for delegation" list from your own system
   prompt, verbatim, into `python3 "$H" models $RUN`. Excluded models are marked. Note any user
   notes it prints, such as "free tier".
2. **Reuse a saved cast if possible.** Run `python3 "$H" cast $RUN --reuse`. If it exits 0, use
   that cast and go to Step 1. Exit 4 means there is no saved cast or it is stale.
3. **Cast from the non-excluded models.** For each seat, give an ordered list: the primary model,
   then 1–3 fallbacks.
   - **Match the "Model to cast" column above**, using what you know about each model. Prefer
     `reasoning` models for the Opus-like seats and fast `standard` ones for the clerk.
   - **Prefer vendor diversity** across the five advisors when more than one vendor is usable. It
     is Karpathy's core idea: different models have different blind spots.
   - **Avoid free-tier, preview or rate-limited models** for advisors and the chairman. They stall
     and slow the whole council.
   - **Fallbacks:** list the next best model of a similar strength, preferably from another
     account or vendor. The helper adds the same model through other accounts automatically.
4. **Confirm with the user.** Show the cast as a table (seat, lens, model, fallbacks) and ask the
   user to confirm or change it. Ask once; later runs reuse it.
   - If the user rules out a model or provider ("Gemini is free tier"), record it with
     `python3 "$H" config --exclude '<glob, e.g. google/*>' --note '<why>'` and recast.
5. **Save the cast.** Pipe the cast to the helper as JSON:
   `python3 "$H" cast $RUN <<< '{"contrarian": ["<id>", "<fallback>"], ..., "clerk": ["<id>"]}'`.
   It rejects unknown or excluded ids, prints warnings (for example, no vendor diversity) and
   saves the cast for reuse.
6. **No catalog.** If the catalog is empty, cast every seat as `["inherit"]`, launch every seat
   without `model`, and warn the user that the council has no model diversity.

## Step 1: frame the question

1. **Save the user's words.** Write them verbatim to `$RUN/user_question.md`.
2. **Gather context.** Spend at most a minute reading the 2–4 files that would make generic
   advice specific:
   - `AGENTS.md`, `CLAUDE.md` or the project README;
   - files the user referenced or attached;
   - data relevant to the decision;
   - recent `.llm-council/runs/*/report.md` on the same topic.
3. **Clarify if needed.** If the question is too vague to act on, ask the user **one** clarifying
   question, then proceed.
4. **Write the framed question** to `$RUN/question.md`. Include:
   - the core decision;
   - key context from the message and the files (numbers, constraints, past results), quoting
     short excerpts where precision matters;
   - what is at stake.

   Keep it neutral: add no opinion and do not steer.

## Briefs

Launch every seat with `model:` set to the output of `python3 "$H" next ...` (omit `model` only
when it prints `inherit`). Always start a **new** delegation; never `resume`.

| Delegation | Agent | Brief |
|---|---|---|
| Stage 1 | `python3 "$H" agent <seat>` | `Stage 1. The question is in <RUN>/question.md. Save your answer to <RUN>/stage1/<seat>.md.` Add `Length: up to 600 words.` if the question needs more room. |
| Title | `council-clerk` | `Write the result to <RUN>/title.txt. Prompt:` followed by the output of `python3 "$H" title-prompt $RUN`. |
| Stage 2 | the same advisor persona, in a fresh session | `Stage 2. The review prompt is <RUN>/stage2_prompt.md. Save your review to <RUN>/stage2/<seat>.md.` |
| Stage 3 | `council-chairman` | `The chairman prompt is <RUN>/stage3_prompt.md. Save the council verdict to <RUN>/stage3/chairman.md.` |

If a sub-agent replies with its text instead of writing the file, write that text to the expected
path yourself, unchanged.

## Failover: no seat dangles

The model that fails is never retried, and it is skipped by every later seat and stage of the run.

A seat has **failed** when any of these happens:
- **Error:** the delegation ends failed or stopped, or reports an error (503, overloaded, rate
  limit, quota).
- **No first response:** an advisor or the chairman starts by reading its brief file. If it shows
  0 tool calls 45 s after launch, it has failed. The clerk has 60 s to finish.
- **Stall:** it passes its idle timeout (advisors 120 s, chairman 300 s) or its maximum duration
  (advisors 600 s, chairman 900 s, clerk 120 s).
- **No output:** it finishes without a non-empty output file.

When a seat fails, in the same turn:
1. If it is still running, `TaskStop` it.
2. Run `M=$(python3 "$H" next $RUN <stage> <seat> --failed <its model> --reason "<short reason>")`.
   This logs the failure, deletes partial output and prints the next untried model in the seat's
   chain.
3. Relaunch the same persona as a fresh delegation with `model: M`.
4. If `next` prints `EXHAUSTED` (exit 3), drop the seat. This is the original's graceful
   degradation.

**Watch loop:** call `TaskWait` with `mode: "any"` and `minCompleted: 1`. Use `timeoutSeconds: 45`
while any seat is still waiting for its first tool call; after that, use the shortest remaining
deadline. Handle each seat the moment it settles or misses a deadline.

## Procedure

1. **Cast and frame:** Step 0, then Step 1.
2. **Stage 1.**
   - Run `M=$(python3 "$H" next $RUN stage1 <seat>)` for each seat in `python3 "$H" seats`, and
     `next $RUN title clerk`.
   - Start the five advisors and the clerk in ONE message, then run the watch loop.
   - If every advisor seat is exhausted, run `report` and tell the user that all models failed to
     respond.
3. **Anonymize:** `python3 "$H" stage2-prompt $RUN`.
4. **Stage 2.**
   - Run `next $RUN stage2 <seat>` for **every** advisor seat. A seat whose Stage 1 answer failed
     still reviews, as in the original.
   - Start the five reviews in ONE message, then run the watch loop.
5. **Aggregate:** `python3 "$H" aggregate $RUN`. If a line shows `PARSE WARNING`, show that
   reviewer's raw ranking text.
6. **Stage 3.**
   - Run `python3 "$H" stage3-prompt $RUN`, then `next $RUN stage3 chairman`.
   - Run the chairman delegation under the watch loop.
   - If its chain is exhausted, continue: the report records "Error: Unable to generate final
     synthesis."
7. **Report:** `python3 "$H" report $RUN`.

## Presenting the result

Present the verdict in chat, scannable and in bullets:

```
## Council Verdict: <title>
### Where the Council Agrees
### Where the Council Clashes
### Blind Spots the Council Caught
### The Recommendation
### The One Thing to Do First
```

Then give:

1. **Street cred:** the aggregate table (response, advisor, model, average rank, votes). Say that
   reviewers saw only anonymous labels.
2. **Notes:**
   - the cast;
   - every failover (seat: failed model → replacement, and the reason);
   - parse warnings;
   - caveats that matter, such as an advisor running on a fallback model, or no vendor diversity.
3. **Links** to `report.md` and `transcript.json`.

Do not smooth over disagreement.

## Fidelity notes

**From Karpathy, unchanged:**
- parallel Stage 1;
- anonymized Response labels;
- every member also ranks its own answer;
- the ranking prompt's text and the strict `FINAL RANKING:` format;
- the chairman template's sentences;
- average-rank aggregation;
- graceful degradation;
- the original's title cleanup.

**From Lehmann:**
- the five lenses and their tensions;
- context framing, with one clarifying question at most;
- the three review questions;
- the five-part verdict;
- the chairman may side with a dissenter;
- "don't council trivial questions".

**Deliberate changes:**
- **Casting:** models are cast per user from their delegation catalog, with exclusions and a saved
  cast; Karpathy hard-codes OpenRouter ids.
- **Stage 1 prompt:** the framed question plus the advisor's lens.
- **Review prompt:** the three questions and a 400-word limit are added to Karpathy's text.
- **Chairman prompt:** shows each answer's label, lens and model, plus the aggregate, and asks for
  the verdict structure.
- **Labels:** shuffled.
- **Aggregation:** per advisor seat.
- **Parser:** reads the **last** `FINAL RANKING`, case-insensitive, with word boundaries and
  deduplication.
- **Privacy:** the label map and Stage 1 answers move outside the workspace during review.
- **Failover:** immediate, along each seat's cast chain, with run-wide failure memory; the
  original drops a failed model.
