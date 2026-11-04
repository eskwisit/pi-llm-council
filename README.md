# LLM Council for PI-Desktop

Port of [karpathy/llm-council](https://github.com/karpathy/llm-council). Instead of asking
one model, the agent convenes a council.

1. **Stage 1, first opinions:** Opus, Sonnet and Gemini Flash answer the same question in
   parallel.
2. **Stage 2, review:** the answers are shuffled and anonymized as Response A/B/C. Each model
   ranks all of them in fresh sessions, with a strict `FINAL RANKING:` format. The rankings are
   averaged into a score.
3. **Stage 3, final response:** the Chairman (Opus) sees every answer and review and writes one
   final answer.

## Usage

Install the `.piplug` from the plugins page, or load this folder for development. Then ask:
"Ask the council: …"

Each run is saved under `.llm-council/runs/<timestamp>/` in your workspace as `report.md` and
`transcript.json`. You may want to add `.llm-council/` to `.gitignore`.

## Casting

| Seat | Model |
|---|---|
| Members and reviewers | Opus (depth), Sonnet (balanced, practical), Gemini Flash (other vendor, fast) |
| Chairman | Opus |
| Clerk (title) | Gemini Flash |

## Failover

A seat that errors (for example 503 or overloaded), stops, or misses its deadline (240 s for
members and reviewers, 360 s for the Chairman) is stopped. It is relaunched straight away on the
next model in its fallback chain. A failed model is never retried and never left running.

The chains, best first:

| Seat | Fallback chain |
|---|---|
| Opus | Opus on the 2nd account, then Sonnet, then Sonnet on the 2nd account |
| Sonnet | Sonnet on the 2nd account, then Opus, then Opus on the 2nd account |
| Gemini Flash | Gemini 4 Argon, then Gemini 3.5 Flash-Lite, then Sonnet |
| Chairman | Same chain as the Opus seat |

Every substitution is logged in `seats.json` and in the report, so you can always see which
model actually answered. See them with `python3 scripts/council.py chain <seat>`.

To change the cast or the chains, edit `MEMBERS`/`CHAIRMAN`/`FALLBACKS` in
`scripts/council.py` and the table in `skills/llm-council.md`.

## Files

- `skills/llm-council.md`: orchestrator procedure and the sub-agent briefs.
- `scripts/council.py`: anonymization, ranking parser, aggregation, and Karpathy's prompts
  (byte-identical). Run `python3 scripts/council.py selftest` to check it.

## Credits

Based on [karpathy/llm-council](https://github.com/karpathy/llm-council). The Stage 2 ranking,
Stage 3 Chairman and title prompts, and the ranking parser, are reproduced from its
`backend/council.py`. That repository does not declare a license.
