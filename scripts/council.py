#!/usr/bin/env python3
"""LLM Council helper - deterministic plumbing for the 3-stage council.

Port of karpathy/llm-council backend/council.py. Prompts are verbatim.
Model calls are NOT made here: the orchestrating agent runs them as
sub-agents and saves their text into the run directory.

Run directory layout:
  question.md                 user question (Stage 1 prompt, sent as-is)
  stage1/<member>.md          Stage 1 answers   (written by orchestrator)
  stage2_prompt.md            anonymized ranking prompt   (stage2-prompt)
  label_to_model.json         secret de-anonymization map (stage2-prompt)
  stage2/<member>.md          Stage 2 evaluations (written by orchestrator)
  aggregate.json              parsed rankings + aggregate   (aggregate)
  stage3_prompt.md            chairman prompt              (stage3-prompt)
  stage3/chairman.md          final answer      (written by orchestrator)
  transcript.json, report.md  full record                  (report)

Usage:
  council.py next          RUN_DIR STAGE SEAT [--failed MODEL] [--reason TEXT]
                           -> prints the model to (re)launch the seat on; exit 3 = exhausted
  council.py chain         SEAT
  council.py stage2-prompt RUN_DIR [--seed N] [--no-shuffle]
  council.py aggregate     RUN_DIR
  council.py stage3-prompt RUN_DIR
  council.py report        RUN_DIR [--title TEXT]
  council.py selftest

STAGE is one of: stage1, stage2, stage3, title.  SEAT is opus | sonnet |
gemini-flash (stage1/stage2), chairman (stage3) or clerk (title).
"""

import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

# Council casting (PI-Desktop delegation model ids). Seat id -> primary model.
MEMBERS = {
    "opus": "anthropic/claude-opus-5-5",
    "sonnet": "anthropic/claude-sonnet-5-5",
    "gemini-flash": "google/gemini-3.8-flash",
}
CHAIRMAN = "anthropic/claude-opus-5-5"
CLERK = "google/gemini-3.8-flash"

# Fallback chains, best first. First choice for a failing seat is the SAME model
# through the second Anthropic account (identical quality, independent quota);
# then the closest model that preserves the seat's purpose. The Gemini seat stays
# on Google as long as possible because its job is vendor diversity.
ALT = "8a8a7980-6e22-405e-81a1-2abed7a2c5b0"
OPUS, OPUS_ALT = "anthropic/claude-opus-5-5", f"{ALT}/claude-opus-5-5"
SONNET, SONNET_ALT = "anthropic/claude-sonnet-5-5", f"{ALT}/claude-sonnet-5-5"
FALLBACKS = {
    "opus": [OPUS, OPUS_ALT, SONNET, SONNET_ALT],
    "sonnet": [SONNET, SONNET_ALT, OPUS, OPUS_ALT],
    "gemini-flash": ["google/gemini-3.8-flash", "google/gemini-4-argon",
                     "google/gemini-3.5-flash-lite", SONNET, SONNET_ALT],
    "chairman": [OPUS, OPUS_ALT, SONNET, SONNET_ALT],
    "clerk": ["google/gemini-3.8-flash", "google/gemini-3.5-flash-lite",
              "anthropic/claude-haiku-5-5"],
}
STAGES = {"stage1", "stage2", "stage3", "title"}
STAGE_FILE = {"stage1": "stage1/{seat}.md", "stage2": "stage2/{seat}.md",
              "stage3": "stage3/chairman.md", "title": "title.txt"}


# --------------------------------------------------------------------------
# Verbatim prompt templates from karpathy/llm-council backend/council.py
# --------------------------------------------------------------------------

def build_ranking_prompt(user_query, labeled):
    responses_text = "\n\n".join(
        f"Response {label}:\n{text}" for label, text in labeled
    )
    return f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""


def build_chairman_prompt(user_query, stage1, stage2):
    stage1_text = "\n\n".join(
        f"Model: {r['model']}\nResponse: {r['response']}" for r in stage1
    )
    stage2_text = "\n\n".join(
        f"Model: {r['model']}\nRanking: {r['ranking']}" for r in stage2
    )
    return f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""


def build_title_prompt(user_query):
    return f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""


# --------------------------------------------------------------------------
# Parsing / aggregation (same logic as the original)
# --------------------------------------------------------------------------

def parse_ranking_from_text(ranking_text):
    if "FINAL RANKING:" in ranking_text:
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            section = parts[1]
            numbered = re.findall(r"\d+\.\s*Response [A-Z]", section)
            if numbered:
                return [re.search(r"Response [A-Z]", m).group() for m in numbered]
            return re.findall(r"Response [A-Z]", section)
    return re.findall(r"Response [A-Z]", ranking_text)


def calculate_aggregate_rankings(stage2, label_to_model):
    positions = defaultdict(list)
    for r in stage2:
        for pos, label in enumerate(parse_ranking_from_text(r["ranking"]), start=1):
            if label in label_to_model:
                positions[label_to_model[label]].append(pos)
    agg = [
        {"model": m, "average_rank": round(sum(p) / len(p), 2), "rankings_count": len(p)}
        for m, p in positions.items() if p
    ]
    agg.sort(key=lambda x: x["average_rank"])
    return agg


# --------------------------------------------------------------------------
# Run-directory helpers
# --------------------------------------------------------------------------

def _seats(run):
    p = run / "seats.json"
    return json.loads(p.read_text()) if p.is_file() else {"events": []}


def _model_of(member_id, run=None, stage=None):
    """Model that actually produced this seat's output (after any fallback)."""
    if run is not None and stage:
        used = _seats(run).get(stage, {}).get(member_id)
        if used:
            return used
    return MEMBERS.get(member_id, member_id)


def cmd_next(run, stage, seat, failed=None, reason=None):
    """Assign (or, after a failure, re-assign) a seat to the best remaining model."""
    import datetime
    if stage not in STAGES or seat not in FALLBACKS:
        sys.exit(f"error: bad stage/seat {stage}/{seat}")
    run.mkdir(parents=True, exist_ok=True)
    seats = _seats(run)
    tried = [e["failed_model"] for e in seats["events"]
             if e["stage"] == stage and e["seat"] == seat]
    if failed:
        tried.append(failed)
        stale = run / STAGE_FILE[stage].format(seat=seat)
        if stale.exists():
            stale.unlink()  # never keep partial output from a failed model
    nxt = next((m for m in FALLBACKS[seat] if m not in tried), None)
    if failed:
        seats["events"].append({
            "stage": stage, "seat": seat, "failed_model": failed,
            "reason": reason or "error", "next_model": nxt,
            "at": datetime.datetime.now().isoformat(timespec="seconds")})
    if nxt:
        seats.setdefault(stage, {})[seat] = nxt
    else:
        seats.setdefault(stage, {}).pop(seat, None)
    (run / "seats.json").write_text(json.dumps(seats, indent=2))
    if not nxt:
        print("EXHAUSTED")
        sys.exit(3)
    print(nxt)


def _read_dir(run, sub):
    d = run / sub
    out = []
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            text = f.read_text(encoding="utf-8").strip()
            if text:  # failed members are simply absent/empty (graceful degradation)
                out.append((f.stem, text))
    return out


def _question(run):
    q = run / "question.md"
    if not q.is_file():
        sys.exit(f"error: {q} not found")
    return q.read_text(encoding="utf-8").strip()


def cmd_stage2_prompt(run, seed=None, shuffle=True):
    query = _question(run)
    stage1 = _read_dir(run, "stage1")
    if not stage1:
        sys.exit("error: no Stage 1 responses - all models failed to respond.")
    if shuffle:
        random.Random(seed).shuffle(stage1)  # extension: removes position bias
    labels = [chr(65 + i) for i in range(len(stage1))]
    label_to_model = {f"Response {l}": _model_of(m, run, "stage1") for l, (m, _) in zip(labels, stage1)}
    label_to_member = {f"Response {l}": m for l, (m, _) in zip(labels, stage1)}
    prompt = build_ranking_prompt(query, [(l, t) for l, (_, t) in zip(labels, stage1)])
    (run / "stage2_prompt.md").write_text(prompt, encoding="utf-8")
    (run / "label_to_model.json").write_text(json.dumps(
        {"label_to_model": label_to_model, "label_to_member": label_to_member}, indent=2))
    print(f"wrote stage2_prompt.md ({len(stage1)} anonymized responses); "
          "label_to_model.json is SECRET - never show it to reviewers.")


def _load_map(run):
    p = run / "label_to_model.json"
    if not p.is_file():
        sys.exit("error: run stage2-prompt first")
    return json.loads(p.read_text())["label_to_model"]


def _stage2(run):
    return [{"model": _model_of(m, run, "stage2"), "member": m, "ranking": t,
             "parsed_ranking": parse_ranking_from_text(t)} for m, t in _read_dir(run, "stage2")]


def cmd_aggregate(run):
    l2m = _load_map(run)
    stage2 = _stage2(run)
    if not stage2:
        print("warning: no Stage 2 evaluations; aggregate is empty")
    agg = calculate_aggregate_rankings(stage2, l2m)
    (run / "aggregate.json").write_text(json.dumps(
        {"stage2": [{k: r[k] for k in ("member", "model", "parsed_ranking")} for r in stage2],
         "aggregate_rankings": agg}, indent=2))
    for r in stage2:
        flag = "" if len(r["parsed_ranking"]) == len(l2m) else "  <-- PARSE WARNING"
        print(f"{r['member']:>14}: {', '.join(r['parsed_ranking']) or '(none)'}{flag}")
    print("\nStreet cred (avg rank, lower is better):")
    for i, a in enumerate(agg, 1):
        print(f"  {i}. {a['model']}  avg {a['average_rank']}  ({a['rankings_count']} votes)")


def cmd_stage3_prompt(run):
    query = _question(run)
    stage1 = [{"model": _model_of(m, run, "stage1"), "response": t} for m, t in _read_dir(run, "stage1")]
    stage2 = _stage2(run)
    (run / "stage3_prompt.md").write_text(
        build_chairman_prompt(query, stage1, stage2), encoding="utf-8")
    print(f"wrote stage3_prompt.md ({len(stage1)} responses, {len(stage2)} rankings)")


def cmd_report(run, title=None):
    query = _question(run)
    l2m = _load_map(run)
    stage1 = [{"model": _model_of(m, run, "stage1"), "response": t} for m, t in _read_dir(run, "stage1")]
    stage2 = _stage2(run)
    seats = _seats(run)
    chairman = seats.get("stage3", {}).get("chairman", CHAIRMAN)
    final = run / "stage3" / "chairman.md"
    stage3 = {"model": chairman, "response": final.read_text(encoding="utf-8").strip()
              if final.is_file() else "Error: Unable to generate final synthesis."}
    agg = calculate_aggregate_rankings(stage2, l2m)
    transcript = {"title": title or "", "question": query, "stage1": stage1,
                  "stage2": stage2, "stage3": stage3,
                  "metadata": {"label_to_model": l2m, "aggregate_rankings": agg,
                               "fallbacks": seats["events"]}}
    (run / "transcript.json").write_text(json.dumps(transcript, indent=2))
    lines = [f"# {title or 'LLM Council'}", "", f"**Question:** {query}", "",
             "## Stage 3 - Final answer", f"_Chairman: {chairman}_", "", stage3["response"], "",
             "## Stage 2 - Aggregate rankings", "", "| # | Model | Avg rank | Votes |",
             "|---|---|---|---|"]
    lines += [f"| {i} | {a['model']} | {a['average_rank']} | {a['rankings_count']} |"
              for i, a in enumerate(agg, 1)]
    lines += ["", "Label map: " + ", ".join(f"{k} = **{v}**" for k, v in l2m.items()), ""]
    if seats["events"]:
        lines += ["### Fallbacks", ""]
        lines += [f"- {e['stage']}/{e['seat']}: {e['failed_model']} failed ({e['reason']}) "
                  f"-> {e['next_model'] or 'no model left, seat dropped'}" for e in seats["events"]]
        lines += [""]
    for r in stage2:
        lines += [f"### Evaluation by {r['model']}", "", r["ranking"], "",
                  f"_Extracted ranking: {', '.join(r['parsed_ranking'])}_", ""]
    lines += ["## Stage 1 - Individual responses", ""]
    for r in stage1:
        lines += [f"### {r['model']}", "", r["response"], ""]
    (run / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote transcript.json and report.md in {run}")


def cmd_selftest():
    assert parse_ranking_from_text("x\nFINAL RANKING:\n1. Response C\n2. Response A") == \
        ["Response C", "Response A"]
    assert parse_ranking_from_text("FINAL RANKING: Response B > Response A") == \
        ["Response B", "Response A"]
    assert parse_ranking_from_text("I like Response A over Response B") == \
        ["Response A", "Response B"]
    agg = calculate_aggregate_rankings(
        [{"ranking": "FINAL RANKING:\n1. Response B\n2. Response A"},
         {"ranking": "FINAL RANKING:\n1. Response A\n2. Response B"},
         {"ranking": "FINAL RANKING:\n1. Response B\n2. Response A"}],
        {"Response A": "m1", "Response B": "m2"})
    assert agg[0] == {"model": "m2", "average_rank": 1.33, "rankings_count": 3}, agg
    import tempfile, io, contextlib
    with tempfile.TemporaryDirectory() as d:
        run = Path(d)
        (run / "stage1").mkdir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cmd_next(run, "stage1", "opus")
        assert out.getvalue().strip() == OPUS
        (run / "stage1" / "opus.md").write_text("partial")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cmd_next(run, "stage1", "opus", failed=OPUS, reason="503")
        assert out.getvalue().strip() == OPUS_ALT
        assert not (run / "stage1" / "opus.md").exists()   # partial output discarded
        assert _model_of("opus", run, "stage1") == OPUS_ALT
        for m in [OPUS_ALT, SONNET]:
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_next(run, "stage1", "opus", failed=m)
        assert _model_of("opus", run, "stage1") == SONNET_ALT
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                cmd_next(run, "stage1", "opus", failed=SONNET_ALT)
            raise AssertionError("expected exhaustion")
        except SystemExit as e:
            assert e.code == 3
    print("selftest OK")


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd = argv[1]
    if cmd == "selftest":
        return cmd_selftest()
    if cmd == "chain":
        if len(argv) < 3 or argv[2] not in FALLBACKS:
            sys.exit(f"seats: {', '.join(FALLBACKS)}")
        return print("\n".join(FALLBACKS[argv[2]]))
    if len(argv) < 3:
        sys.exit(__doc__)
    run = Path(argv[2])
    opts = argv[3:]

    def opt(name, default=None):
        return opts[opts.index(name) + 1] if name in opts else default

    if cmd == "next":
        if len(opts) < 2:
            sys.exit(__doc__)
        cmd_next(run, opts[0], opts[1], opt("--failed"), opt("--reason"))
    elif cmd == "stage2-prompt":
        seed = opt("--seed")
        cmd_stage2_prompt(run, int(seed) if seed else None, "--no-shuffle" not in opts)
    elif cmd == "aggregate":
        cmd_aggregate(run)
    elif cmd == "stage3-prompt":
        cmd_stage3_prompt(run)
    elif cmd == "report":
        cmd_report(run, opt("--title"))
    elif cmd == "title-prompt":
        print(build_title_prompt(_question(run)))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
