#!/usr/bin/env python3
"""LLM Council helper: deterministic plumbing for the 3-stage council.

Port of karpathy/llm-council (backend/council.py) using the five thinking-lens
advisors of Ole Lehmann's llm-council skill. No model calls are made here. The
orchestrating agent runs the persona sub-agents, and they write their output into
the run directory.

Run directory, inside the workspace (sub-agents can read it):
  user_question.md     the user's words, verbatim                  (orchestrator)
  question.md          framed question that every advisor receives (orchestrator)
  stage1/<seat>.md     Stage 1 answers; stage2-prompt moves them to the private dir
  stage2_prompt.md     anonymized review prompt                    (stage2-prompt)
  stage2/<seat>.md     Stage 2 reviews                             (advisors)
  aggregate.json       parsed rankings and scoreboard              (aggregate)
  stage3_prompt.md     chairman prompt, de-anonymized              (stage3-prompt)
  stage3/chairman.md   council verdict                             (chairman)
  title.txt            run title                                   (clerk)
  transcript.json, report.md                                       (report)

Private directory, ~/.llm-council/private/<run>-<hash>/. It lies outside every
path a sub-agent can read and holds seats.json (assignments and failover events),
label_map.json (the secret de-anonymization map) and the anonymized Stage 1
answers.

Usage:
  council.py install-subagents [--check]
  council.py seats                         advisor seats, in order
  council.py agent  SEAT                   persona sub-agent for a seat
  council.py chain  SEAT                   best-first model chain for a seat
  council.py next   RUN STAGE SEAT [--failed MODEL] [--reason TEXT]
                    prints the model to (re)launch the seat on; exit 3 = exhausted
  council.py record RUN STAGE SEAT (--delegation ID | --model MODEL)
  council.py title-prompt  RUN
  council.py stage2-prompt RUN [--seed N] [--no-shuffle] [--force]
  council.py aggregate     RUN
  council.py stage3-prompt RUN
  council.py report        RUN
  council.py selftest

STAGE is stage1, stage2, stage3 or title. SEAT is an advisor seat (stage1 and
stage2), chairman (stage3) or clerk (title).
"""

import contextlib
import datetime
import fcntl
import hashlib
import json
import random
import re
import shutil
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

SUBAGENT_DIR = Path(__file__).resolve().parent.parent / "subagents"
INSTALL_DIR = Path.home() / ".agents" / "subagents"
PRIVATE_ROOT = Path.home() / ".llm-council" / "private"
SESSIONS_DIR = Path.home() / ".pi-desktop" / "sessions"
MARKER = "Use only from the llm-council skill."

# Advisor seat -> (persona sub-agent, display name). Order = Stage 1 order.
ADVISORS = {
    "contrarian": ("council-contrarian", "The Contrarian"),
    "first-principles": ("council-first-principles", "The First Principles Thinker"),
    "expansionist": ("council-expansionist", "The Expansionist"),
    "outsider": ("council-outsider", "The Outsider"),
    "executor": ("council-executor", "The Executor"),
}
SEAT_AGENT = {s: a for s, (a, _) in ADVISORS.items()}
SEAT_AGENT.update(chairman="council-chairman", clerk="council-clerk")
DISPLAY = {s: n for s, (_, n) in ADVISORS.items()}
DISPLAY.update(chairman="The Chairman", clerk="The Clerk")
STAGE_SEATS = {"stage1": tuple(ADVISORS), "stage2": tuple(ADVISORS),
               "stage3": ("chairman",), "title": ("clerk",)}
OUTPUT = {"stage1": "stage1/{seat}.md", "stage2": "stage2/{seat}.md",
          "stage3": "stage3/chairman.md", "title": "title.txt"}
PROMPT_LINE_WARN = 1800  # the Read tool returns 2000 lines per call by default


# --------------------------------------------------------------------------
# Personas: the single source of truth for each seat's model chain
# --------------------------------------------------------------------------

def _unquote(v):
    v = v.strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"" else v


def _frontmatter(path):
    """Parse frontmatter as PI-Desktop does; return (fields, duplicate keys)."""
    lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    out, dups, key = {}, [], None
    if not lines or lines[0] != "---":
        return out, dups
    for line in lines[1:]:
        if line == "---":
            break
        item = re.match(r"^[ \t]*-[ \t]+(.*)$", line)
        if item and key:
            if not isinstance(out.get(key), list):
                out[key] = []
            out[key].append(_unquote(item.group(1)))
            continue
        pair = re.match(r"^([A-Za-z][A-Za-z0-9_\- ]*):[ \t]*(.*)$", line)
        if pair:
            key = pair.group(1).strip().lower()
            if key in out:
                dups.append(key)
            out[key] = _unquote(pair.group(2)) if pair.group(2).strip() else []
    return out, dups


def _as_list(v):
    if isinstance(v, list):
        return v
    return [_unquote(x) for x in v.strip("[]").split(",") if x.strip()] if v else []


@lru_cache(maxsize=None)
def chains():
    out = {}
    for seat, agent in SEAT_AGENT.items():
        f = SUBAGENT_DIR / f"{agent}.md"
        if not f.is_file():
            sys.exit(f"error: missing persona {f}")
        fm, _ = _frontmatter(f)
        if not fm.get("model"):
            sys.exit(f"error: {f} has no model")
        out[seat] = [fm["model"]] + _as_list(fm.get("fallbackmodels", []))
    return out


def _resolve(model, chain, prefer=None):
    """Map a model id (possibly without provider) onto the seat's chain."""
    if model in chain:
        return model
    tail = model.split("/")[-1].strip().lower()
    hits = [m for m in chain if m.split("/")[-1].lower() == tail]
    if prefer in hits:
        return prefer
    return hits[0] if hits else None


# --------------------------------------------------------------------------
# Prompts. Karpathy's templates, with Lehmann's review questions and verdict
# structure added. Every Karpathy sentence is kept word for word.
# --------------------------------------------------------------------------

def build_ranking_prompt(user_query, labeled):
    responses_text = "\n\n".join(f"Response {label}:\n{text}" for label, text in labeled)
    return f"""You are evaluating different responses to the following question:

Question: {user_query}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
   After that, answer these three questions. Be specific and reference responses by letter.
   - Strongest: Which response is the strongest? Why?
   - Biggest blind spot: Which response has the biggest blind spot? What is it missing?
   - All missed: What did ALL responses miss that the council should consider?
2. Then, at the very end of your response, provide a final ranking.

Be direct. Keep everything before the final ranking under 400 words.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

Strongest: Response C, because...
Biggest blind spot: Response B, which misses...
All missed: ...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""


def build_chairman_prompt(user_query, stage1, stage2, aggregate):
    stage1_text = "\n\n".join(
        f"{r['label']} - {r['advisor']} (Model: {r['model']})\nResponse: {r['response']}"
        for r in stage1)
    stage2_text = "\n\n".join(
        f"Reviewer: {r['advisor']} (Model: {r['model']})\nRanking: {r['ranking']}"
        for r in stage2)
    agg_text = "\n".join(
        f"{i}. {a['label']} - {a['advisor']}: average rank {a['average_rank']} "
        f"({a['rankings_count']} votes)" for i, a in enumerate(aggregate, 1)) or "(no valid rankings)"
    return f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Each response came from a different advisor, each thinking from a deliberately different angle. Reviewers saw the responses only as Response A, B, C...; the advisor and model names below are revealed to you alone.

Original Question: {user_query}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

STAGE 2 - Aggregate ranking (average position, lower is better):
{agg_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

You may disagree with the majority. If most advisors agree but a dissenter's reasoning is stronger, side with the dissenter and explain why.

Structure your answer exactly like this:

## Where the Council Agrees
[Points several advisors reached independently. These are high-confidence signals.]

## Where the Council Clashes
[Genuine disagreements. Present both sides, explain why reasonable advisors disagree, then decide.]

## Blind Spots the Council Caught
[Things that only emerged in the peer review, or that one advisor flagged and the others missed.]

## The Recommendation
[A clear, direct recommendation with its reasoning. Not "it depends".]

## The One Thing to Do First
[A single concrete next step. Not a list.]

Be direct. Don't hedge. The point of the council is to give the user clarity they could not get from a single perspective.

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""


def build_title_prompt(user_query):
    if len(user_query) > 2000:
        user_query = user_query[:2000] + "..."
    return f"""Generate a very short title (3-5 words maximum) that summarizes the following question.
The title should be concise and descriptive. Do not use quotes or punctuation in the title.

Question: {user_query}

Title:"""


def clean_title(title):
    """Same post-processing as the original generate_conversation_title."""
    title = (title or "").strip().strip("\"'")
    if not title:
        return "New Conversation"
    return title[:47] + "..." if len(title) > 50 else title


# --------------------------------------------------------------------------
# Parsing / aggregation
# --------------------------------------------------------------------------

_HEADER = re.compile(r"final\s+ranking\s*:?", re.I)
_NUMBERED = re.compile(r"\d+\.\s*[*_]*\s*Response ([A-Z])\b")
_LABEL = re.compile(r"\bResponse ([A-Z])\b")


def parse_ranking_from_text(ranking_text, valid=None):
    """Extract the ranked labels.

    Deviation from the original: the original reads from the FIRST "FINAL
    RANKING:" and is case-sensitive, so a reviewer who quotes the phrase earlier
    breaks it. This reads from the LAST header, ignores case, requires word
    boundaries, drops duplicate labels and drops labels that are not in `valid`.
    """
    headers = list(_HEADER.finditer(ranking_text))
    section = ranking_text[headers[-1].end():] if headers else ranking_text
    found = _NUMBERED.findall(section) or _LABEL.findall(section)
    out = []
    for letter in found:
        label = f"Response {letter}"
        if label not in out and (valid is None or label in valid):
            out.append(label)
    return out


def calculate_aggregate_rankings(stage2, label_map):
    """Average rank per label, i.e. per advisor seat. The original keys by model
    string, which merges two seats that run the same model."""
    positions = defaultdict(list)
    for r in stage2:
        for pos, label in enumerate(parse_ranking_from_text(r["ranking"], label_map), start=1):
            positions[label].append(pos)
    agg = [{"label": label, "seat": label_map[label]["seat"],
            "advisor": label_map[label]["advisor"], "model": label_map[label]["model"],
            "average_rank": round(sum(p) / len(p), 2), "rankings_count": len(p)}
           for label, p in positions.items() if p]
    agg.sort(key=lambda x: (x["average_rank"], x["label"]))
    return agg


# --------------------------------------------------------------------------
# Run state
# --------------------------------------------------------------------------

def private_dir(run):
    run = Path(run).resolve()
    key = hashlib.sha1(str(run).encode()).hexdigest()[:10]
    d = PRIVATE_ROOT / f"{run.name}-{key}"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextlib.contextmanager
def _locked_seats(run):
    d = private_dir(run)
    with open(d / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        p = d / "seats.json"
        seats = json.loads(p.read_text()) if p.is_file() else {"events": []}
        yield seats
        p.write_text(json.dumps(seats, indent=2))


def _seats(run):
    p = private_dir(run) / "seats.json"
    return json.loads(p.read_text()) if p.is_file() else {"events": []}


def _model_of(run, stage, seat):
    used = _seats(run).get(stage, {}).get(seat)
    return used or chains()[seat][0]


def _check_seat(stage, seat):
    if stage not in STAGE_SEATS or seat not in STAGE_SEATS[stage]:
        sys.exit(f"error: seat {seat!r} is not valid for {stage!r}")


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def cmd_next(run, stage, seat, failed=None, reason=None):
    """Assign a seat to the best model that has not failed anywhere in this run."""
    _check_seat(stage, seat)
    chain = chains()[seat]
    with _locked_seats(run) as seats:
        assigned = seats.get(stage, {}).get(seat)
        start = 0
        if failed:
            f = _resolve(failed, chain, prefer=assigned)
            if f is None:  # fail closed: never restart the chain from the top
                print(f"error: {failed!r} is not in the {seat} chain: {', '.join(chain)}",
                      file=sys.stderr)
                sys.exit(2)
            start = chain.index(f) + 1
            stale = Path(run) / OUTPUT[stage].format(seat=seat)
            if stale.exists():
                stale.unlink()  # never keep partial output from a failed model
        bad = {e["failed_model"] for e in seats["events"]}  # run-wide memory
        if failed:
            bad.add(f)
        nxt = next((m for m in chain[start:] if m not in bad), None)
        if failed:
            seats["events"].append({"stage": stage, "seat": seat, "failed_model": f,
                                    "reason": reason or "error", "next_model": nxt, "at": _now()})
        if nxt:
            seats.setdefault(stage, {})[seat] = nxt
        else:
            seats.setdefault(stage, {}).pop(seat, None)
    if not nxt:
        print("EXHAUSTED")
        sys.exit(3)
    print(nxt)


def delegation_model(delegation_id):
    """modelId the PI-Desktop runtime reports for a settled delegation."""
    files = sorted(SESSIONS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        found = None
        with open(f, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if delegation_id not in line:
                    continue
                for m in re.finditer(r'\{[^{}]*"delegationId":\s*"' + re.escape(delegation_id)
                                     + r'"[^{}]*\}', line):
                    try:
                        obj = json.loads(m.group(0))
                    except ValueError:
                        continue
                    if obj.get("modelId") and obj.get("completedAt"):
                        found = obj["modelId"]
        if found:
            return found
    return None


def cmd_record(run, stage, seat, model=None, delegation=None):
    """Record the model that actually produced a seat's output.

    Models skipped by a native failover (between the assigned model and the one
    that answered) are logged as failed, so later stages skip them too.
    """
    _check_seat(stage, seat)
    chain = chains()[seat]
    if delegation:
        model = delegation_model(delegation)
        if not model:
            sys.exit(f"error: no settled delegation {delegation} found in {SESSIONS_DIR}; "
                     "pass --model instead")
    with _locked_seats(run) as seats:
        assigned = seats.get(stage, {}).get(seat) or chain[0]
        used = _resolve(model, chain, prefer=assigned) or model
        if used in chain and assigned in chain and chain.index(used) > chain.index(assigned):
            for skipped in chain[chain.index(assigned):chain.index(used)]:
                seats["events"].append({"stage": stage, "seat": seat, "failed_model": skipped,
                                        "reason": "native failover", "next_model": used,
                                        "at": _now()})
        seats.setdefault(stage, {})[seat] = used
    print(f"{stage}/{seat}: {used}")


def _read_dir(d):
    out = []
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            text = f.read_text(encoding="utf-8").strip()
            if text:  # failed seats are simply absent or empty (graceful degradation)
                out.append((f.stem, text))
    return out


def _question(run, name="question.md"):
    q = Path(run) / name
    if not q.is_file():
        if name != "question.md":
            return _question(run)
        sys.exit(f"error: {q} not found")
    return q.read_text(encoding="utf-8").strip()


def _load_map(run):
    p = private_dir(run) / "label_map.json"
    if not p.is_file():
        sys.exit("error: run stage2-prompt first")
    return json.loads(p.read_text())["labels"]


def _warn_length(path):
    n = path.read_text(encoding="utf-8").count("\n") + 1
    flag = "  WARNING: long prompt; readers must page with offset" if n > PROMPT_LINE_WARN else ""
    print(f"{path.name}: {n} lines{flag}")


def cmd_stage2_prompt(run, seed=None, shuffle=True, force=False):
    run = Path(run)
    priv = private_dir(run)
    if (priv / "label_map.json").exists() and not force:
        sys.exit("error: already anonymized (reviews may be under way); use --force to redo")
    query = _question(run)
    src = run / "stage1"
    stage1 = _read_dir(src) or _read_dir(priv / "stage1")
    if not stage1:
        sys.exit("error: no Stage 1 responses - all models failed to respond.")
    if seed is None:
        seed = random.randrange(1 << 30)
    if shuffle:
        random.Random(seed).shuffle(stage1)
    labels = [f"Response {chr(65 + i)}" for i in range(len(stage1))]
    label_map = {label: {"seat": seat, "advisor": DISPLAY[seat],
                         "model": _model_of(run, "stage1", seat)}
                 for label, (seat, _) in zip(labels, stage1)}
    prompt = build_ranking_prompt(query, [(l.split()[-1], t) for l, (_, t) in zip(labels, stage1)])
    (run / "stage2_prompt.md").write_text(prompt, encoding="utf-8")
    (priv / "label_map.json").write_text(json.dumps(
        {"labels": label_map, "seed": seed, "shuffled": shuffle}, indent=2))
    if src.is_dir():  # keep answers out of the reviewers' reach
        (priv / "stage1").mkdir(exist_ok=True)
        for f in src.glob("*.md"):
            shutil.move(str(f), priv / "stage1" / f.name)
        with contextlib.suppress(OSError):
            src.rmdir()
    print(f"wrote stage2_prompt.md ({len(stage1)} anonymized responses); "
          f"label map and Stage 1 answers moved to {priv}")
    _warn_length(run / "stage2_prompt.md")


def _stage2(run):
    lm = _load_map(run)
    return [{"seat": s, "advisor": DISPLAY[s], "model": _model_of(run, "stage2", s), "ranking": t,
             "parsed_ranking": parse_ranking_from_text(t, lm)}
            for s, t in _read_dir(Path(run) / "stage2")]


def cmd_aggregate(run):
    lm = _load_map(run)
    stage2 = _stage2(run)
    if not stage2:
        print("warning: no Stage 2 reviews; the aggregate is empty")
    agg = calculate_aggregate_rankings(stage2, lm)
    (Path(run) / "aggregate.json").write_text(json.dumps(
        {"stage2": [{k: r[k] for k in ("seat", "model", "parsed_ranking")} for r in stage2],
         "aggregate_rankings": agg}, indent=2))
    for r in stage2:
        flag = "" if sorted(r["parsed_ranking"]) == sorted(lm) else "  <-- PARSE WARNING"
        print(f"{r['seat']:>16}: {', '.join(r['parsed_ranking']) or '(none)'}{flag}")
    print("\nStreet cred (average rank, lower is better):")
    for i, a in enumerate(agg, 1):
        print(f"  {i}. {a['label']} {a['advisor']} [{a['model']}]  "
              f"avg {a['average_rank']} ({a['rankings_count']} votes)")


def _stage1_labeled(run):
    lm = _load_map(run)
    by_seat = dict(_read_dir(private_dir(run) / "stage1"))
    return [{"label": label, **info, "response": by_seat.get(info["seat"], "")}
            for label, info in sorted(lm.items())]


def cmd_stage3_prompt(run):
    run = Path(run)
    lm = _load_map(run)
    stage2 = _stage2(run)
    agg = calculate_aggregate_rankings(stage2, lm)
    (run / "stage3_prompt.md").write_text(
        build_chairman_prompt(_question(run), _stage1_labeled(run), stage2, agg), encoding="utf-8")
    print(f"wrote stage3_prompt.md ({len(lm)} responses, {len(stage2)} reviews)")
    _warn_length(run / "stage3_prompt.md")


def cmd_report(run):
    run = Path(run)
    query = _question(run)
    seats = _seats(run)
    has_map = (private_dir(run) / "label_map.json").is_file()
    lm = _load_map(run) if has_map else {}
    stage1 = _stage1_labeled(run) if has_map else []
    stage2 = _stage2(run) if has_map else []
    agg = calculate_aggregate_rankings(stage2, lm) if has_map else []
    chairman = seats.get("stage3", {}).get("chairman", chains()["chairman"][0])
    final = run / "stage3" / "chairman.md"
    text = final.read_text(encoding="utf-8").strip() if final.is_file() else ""
    if not stage1:
        text = "All models failed to respond. Please try again."
    stage3 = {"model": chairman, "response": text or "Error: Unable to generate final synthesis."}
    title = clean_title((run / "title.txt").read_text(encoding="utf-8")
                        if (run / "title.txt").is_file() else "")
    transcript = {"title": title, "user_question": _question(run, "user_question.md"),
                  "question": query, "stage1": stage1, "stage2": stage2, "stage3": stage3,
                  "metadata": {"label_map": lm, "aggregate_rankings": agg,
                               "seats": {k: v for k, v in seats.items() if k != "events"},
                               "fallbacks": seats["events"]}}
    (run / "transcript.json").write_text(json.dumps(transcript, indent=2))
    lines = [f"# {title}", "", f"**Question:** {query}", "",
             "## Stage 3 - Council verdict", f"_Chairman: {chairman}_", "", stage3["response"], "",
             "## Stage 2 - Street cred", "", "| # | Response | Advisor | Model | Avg rank | Votes |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {i} | {a['label'].split()[-1]} | {a['advisor']} | {a['model']} | "
              f"{a['average_rank']} | {a['rankings_count']} |" for i, a in enumerate(agg, 1)]
    if seats["events"]:
        lines += ["", "### Failovers", ""]
        lines += [f"- {e['stage']}/{e['seat']}: {e['failed_model']} failed ({e['reason']}) "
                  f"-> {e['next_model'] or 'no model left, seat dropped'}" for e in seats["events"]]
    lines += [""]
    for r in stage2:
        lines += [f"### Review by {r['advisor']} ({r['model']})", "", r["ranking"], "",
                  f"_Extracted ranking: {', '.join(r['parsed_ranking']) or '(none)'}_", ""]
    lines += ["## Stage 1 - Advisor answers", ""]
    for r in stage1:
        lines += [f"### {r['label']} - {r['advisor']} ({r['model']})", "", r["response"], ""]
    (run / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote transcript.json and report.md in {run}")


def cmd_install_subagents(check=False):
    """Copy the personas to ~/.agents/subagents (PI-Desktop loads sub-agents only
    from there) and remove council personas this version no longer ships."""
    wanted = {f"{a}.md" for a in SEAT_AGENT.values()}
    stale = [(SUBAGENT_DIR / n, INSTALL_DIR / n) for n in sorted(wanted)
             if not (INSTALL_DIR / n).is_file()
             or (INSTALL_DIR / n).read_bytes() != (SUBAGENT_DIR / n).read_bytes()]
    extra = [p for p in INSTALL_DIR.glob("council-*.md")
             if p.name not in wanted and MARKER in p.read_text(encoding="utf-8")]
    if check:
        for _, dst in stale:
            print(f"missing or outdated: {dst}")
        for p in extra:
            print(f"obsolete: {p}")
        if stale or extra:
            sys.exit(1)
        print("council personas installed and up to date")
        return
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    for src, dst in stale:
        dst.write_bytes(src.read_bytes())
        print(f"installed {dst}")
    for p in extra:
        p.unlink()
        print(f"removed obsolete {p}")
    print("up to date" if not (stale or extra) else
          "start a new session if the Task tool does not list the council personas")


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

def cmd_selftest():
    import io
    import tempfile
    global PRIVATE_ROOT

    # Parser: the original behaviour, plus the documented fixes.
    p = parse_ranking_from_text
    assert p("x\nFINAL RANKING:\n1. Response C\n2. Response A") == ["Response C", "Response A"]
    assert p("FINAL RANKING: Response B > Response A") == ["Response B", "Response A"]
    assert p("I like Response A over Response B") == ["Response A", "Response B"]
    assert p("quoting `FINAL RANKING:` here, Response B...\nFINAL RANKING:\n1. Response C\n"
             "2. Response A\n3. Response B") == ["Response C", "Response A", "Response B"]
    assert p("Final ranking\n1. **Response B**\n2. Response B\n3. Response Z",
             {"Response A": 0, "Response B": 0}) == ["Response B"]
    assert p("Response Analysis is weak; FINAL RANKING:\n1. Response A") == ["Response A"]

    # Aggregation is per seat: two seats on the same model stay separate.
    lm = {"Response A": {"seat": "contrarian", "advisor": "x", "model": "m"},
          "Response B": {"seat": "first-principles", "advisor": "y", "model": "m"}}
    agg = calculate_aggregate_rankings(
        [{"ranking": "FINAL RANKING:\n1. Response B\n2. Response A"},
         {"ranking": "FINAL RANKING:\n1. Response A\n2. Response B"},
         {"ranking": "FINAL RANKING:\n1. Response B\n2. Response A"}], lm)
    assert [(a["seat"], a["average_rank"], a["rankings_count"]) for a in agg] == \
        [("first-principles", 1.33, 3), ("contrarian", 1.67, 3)], agg

    # Personas: complete, no duplicate keys, unique chains, identical shared sections.
    shared = set()
    for seat, agent in SEAT_AGENT.items():
        fm, dups = _frontmatter(SUBAGENT_DIR / f"{agent}.md")
        assert not dups, (agent, dups)
        for k in ("name", "description", "tools", "model", "fallbackmodels", "thinkinglevel",
                  "permission", "idletimeout", "maxduration"):
            assert fm.get(k), (agent, k)
        assert fm["name"] == agent and MARKER in fm["description"], agent
        assert len(set(chains()[seat])) == len(chains()[seat]), (agent, "duplicate chain entry")
        if seat in ADVISORS:
            body = (SUBAGENT_DIR / f"{agent}.md").read_text()
            shared.add(body[body.index("## Stage 2: review"):])
    assert len(shared) == 1, "advisor Stage 2/Output sections must be identical"

    # Prompts keep Karpathy's text; title cleanup matches the original.
    rp = build_ranking_prompt("Q?", [("A", "a"), ("B", "b")])
    assert "Each line should be: number, period, space, then ONLY the response label" in rp
    assert clean_title('"' + "x" * 60 + '"') == "x" * 47 + "..." and clean_title("") == "New Conversation"

    # Failover bookkeeping in a throwaway run.
    with tempfile.TemporaryDirectory() as d:
        PRIVATE_ROOT = Path(d) / "private"
        run = Path(d) / "run"
        (run / "stage1").mkdir(parents=True)
        (run / "question.md").write_text("Q?")
        def out(f, *a, **k):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                f(*a, **k)
            return buf.getvalue().strip()
        opus, opus_alt, sonnet = chains()["contrarian"][:3]
        assert out(cmd_next, run, "stage1", "contrarian") == opus
        (run / "stage1" / "contrarian.md").write_text("partial")
        assert out(cmd_next, run, "stage1", "contrarian", failed="claude-opus-5-5") == opus_alt
        assert not (run / "stage1" / "contrarian.md").exists()
        # run-wide memory: Opus failed for the contrarian, so first-principles skips it
        assert out(cmd_next, run, "stage1", "first-principles") == opus_alt
        # native failover recorded: the alt account is skipped as well, everywhere
        out(cmd_record, run, "stage1", "first-principles", model=sonnet)
        assert out(cmd_next, run, "stage2", "contrarian") == sonnet
        try:  # fail closed on a model outside the chain
            out(cmd_next, run, "stage1", "contrarian", failed="no/such-model")
            raise AssertionError("expected exit 2")
        except SystemExit as e:
            assert e.code == 2
        # anonymization moves answers out of reach and refuses to run twice
        (run / "stage1" / "contrarian.md").write_text("answer one")
        (run / "stage1" / "executor.md").write_text("answer two")
        out(cmd_stage2_prompt, run, seed=1)
        assert not (run / "stage1").exists()
        assert not any(x.name == "label_map.json" for x in run.rglob("*"))
        try:
            out(cmd_stage2_prompt, run, seed=1)
            raise AssertionError("expected refusal")
        except SystemExit:
            pass
    PRIVATE_ROOT = Path.home() / ".llm-council" / "private"
    print("selftest OK")


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd, rest = argv[1], argv[2:]

    def opt(name, default=None):
        return rest[rest.index(name) + 1] if name in rest else default

    if cmd == "selftest":
        return cmd_selftest()
    if cmd == "install-subagents":
        return cmd_install_subagents("--check" in rest)
    if cmd == "seats":
        return print("\n".join(ADVISORS))
    if cmd in ("agent", "chain"):
        if not rest or rest[0] not in SEAT_AGENT:
            sys.exit(f"seats: {', '.join(SEAT_AGENT)}")
        return print(SEAT_AGENT[rest[0]] if cmd == "agent" else "\n".join(chains()[rest[0]]))
    if not rest:
        sys.exit(__doc__)
    run = Path(rest[0])
    if cmd == "next" and len(rest) >= 3:
        cmd_next(run, rest[1], rest[2], opt("--failed"), opt("--reason"))
    elif cmd == "record" and len(rest) >= 3:
        if not (opt("--model") or opt("--delegation")):
            sys.exit("error: record needs --delegation ID or --model MODEL")
        cmd_record(run, rest[1], rest[2], model=opt("--model"), delegation=opt("--delegation"))
    elif cmd == "title-prompt":
        print(build_title_prompt(_question(run, "user_question.md")))
    elif cmd == "stage2-prompt":
        seed = opt("--seed")
        cmd_stage2_prompt(run, int(seed) if seed else None, "--no-shuffle" not in rest,
                          "--force" in rest)
    elif cmd == "aggregate":
        cmd_aggregate(run)
    elif cmd == "stage3-prompt":
        cmd_stage3_prompt(run)
    elif cmd == "report":
        cmd_report(run)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
