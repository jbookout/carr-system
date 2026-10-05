#!/usr/bin/env python3
"""Extract a local, privacy-reviewed candidate pool; never replay its commands."""
import argparse
import collections
import hashlib
import json
import pathlib
import re

SAFE_PROMPT_WORDS = set("""a an and all are as at be before branch branches build by can carr check checks ci code codex commit committed commits config current deliver delivery did do does done doctorcre don t edit engineering eval every existing failed failing failure fix for fresh from gates get git go green has have hook hooks how i if in is it jev job keep last let live local main make merge merged model models more new no not now of on only open origin or our out own please pr prs precision proceed production pull push pushed read release repo repository review root run same script scripts selftest session sessions ship should show source stop system task test testing tests that the then there this to tool tools turn turns update use using verify was we what when why will with work worktree would yes you your zero rule rules benchmark train heldout shadow tokens split train test availability precision retried retry write rerun force paid calls nothing one two three next change changes apply output outputs plan report broken currently python don`t continue finished status need state today compiler classifier selection selector trigger triggers universal binding context pack packs missing readback health default user prompt prompts requests architecture design validation validate evaluator hardening baseline llm steering pretooluse userpromptsubmit llms automated evidence human human-only dangerous full implementation archaeology noisy noise deterministic controlled train-only optimize optimization find search filtered filter filtering locked threshold thresholds token injected injection dedupe route routing paths match matches matched false true earlier history historical loop loops history draft drafts factory software doctor cre CARR DoctorCRE Sol Claude Opus Fable Sonnet Grok Joe Dell""".lower().split())
SAFE_PROMPT_WORDS.update("""working sufficient actually just think adding able documentation support some certain those them without information reviewer audit progress board factory stopped stop stopping means mean really result results often always never also already still again because want wants going automatically automatic automation action actions issue issues been being could cannot can t didn doesn don won wasn weren couldn wouldn isn aren wasn m re s only include included including wire wired wiring integrate integration integrating otherwise about much less even any many each enough effective effectiveness know known came comes come sense makes made best better complete currently available need needed needs everything anything sure expected expecting question questions ask asked asking understand understanding seriously concern held hold holdout baseline safety deterministic codebase client-independent pruning cheap cheaper cost expensive reranking labels label labelled labeling precision recall request requested recurring task-list guided governing look looking review reviews reviewing fix fixes replace replaced instead later recommended recommendation route routes routing model-room app apps room desk desks orchestrator orchestration study studies sources sending send sends sent delivered acknowledged unneeded obsolete unnecessary allowed supposed matter verify verified watching watch watched admit admitted just-in-time architectural candidate candidates architecture workflow workflows grants grant giving given proof of cite citations meets met sealed read-only tests testable failing fails passed passing reports reporting reports research wrong false positives negatives postmortem retrospective authorization authorized authorize records ledger disposition dispositions calibration calibrated standard stopword stemming stem stemmed scope scoped implement implemented implementing computes compute computed deterministic precision-recall counterpart producer consumed consumer consumers consuming contract contracts active activity activates actually amended retired retirement toggle toggled score scored scores scoring proxy bound breach breaches train-only unrelated requires requirement requirements experiment experiments explain explanation review-grade investigated investigate investigation inspected inspect inspected features feature behavior behaviour gate gates boring small smaller greenpoint fulltext similarity cosine embedding embeddings lexical rerank reranker red-team redteam xhigh high medium low subscription subscriptions haiku model-fit model-tiers confidence confident considered broken rough recent reads no-op metrics measurement measurements measured statement statements counterexamples counterexample front-end frontend backend requirements-ready estimate estimates estimated external empty thin-script entry entrypoint task-shape record-aware unnecessary moving syntax release-ready adoption tiny routine routines runs running launch launchd launchagent recurring slack gmail outlook mail calendar calendars status-row statusrows error errors try tried tries trying timeout timeouts performance profiler instrumentation instrument tier tiers validate validator validation floor floors exact body bodies text title titles proposed propose confirms confirmed confirmation confirms session-level turn-level machine readable collection collections folder folders path paths file files filename filenames repository repositories instructions instruction plain English concise simple ask-user-question box""".lower().split())
SENSITIVE = re.compile(r"(?i)(token|secret|password|credential|api.?key|authorization|bearer|postgres(?:ql)?://|https?://|@|BEGIN .+PRIVATE|[A-Za-z0-9+/]{48,})")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def prompt_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(c, dict) and c.get("type") == "text" for c in content):
        return "\n".join(c.get("text", "") for c in content)
    return None


def safe_prompt(text):
    if not text or len(text) > 500 or SENSITIVE.search(text) or "<" in text:
        return None
    words = re.findall(r"[A-Za-z]+", text.lower())
    if not words or set(words) - SAFE_PROMPT_WORDS:
        return None
    if len(words) < 3:
        return None
    if re.search(r"(?i)\b(?:git|ci|code|repo|worktree|rule|rules|jev|model|hook|selector|delivery|test|tests|pr|release|build|shadow|benchmark)\b", text):
        return text.strip()
    return None


def safe_path(text, repo):
    if not isinstance(text, str) or SENSITIVE.search(text):
        return None
    p = pathlib.Path(text)
    try:
        if p.is_absolute():
            rel = p.relative_to(repo)
        else:
            rel = p
        if ".claude/worktrees" in str(rel):
            bits = list(rel.parts)
            if len(bits) < 4:
                return None
            rel = pathlib.Path(*bits[3:])
        if str(rel) not in TRACKED or rel.parts[0] not in ("ops", "tools", "hooks", "lib", "mcp-server", "evals"):
            return None
    except (ValueError, IndexError):
        return None
    return str(rel)


def safe_call(name, inp, repo):
    if not isinstance(inp, dict):
        return None
    if isinstance(name, str) and re.fullmatch(r"mcp__.+__(?:standing-context|list-verbs|loop-board|loop-headers|current-work-requests|doctrine-index|read-loop)", name):
        def value_safe(value):
            if isinstance(value, (int, bool)) or value is None:
                return True
            if isinstance(value, str):
                return bool(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f-]{27,})?|boot|gist|full|shared|personal|all|joe|dell|open|closed|active|train|test", value))
            if isinstance(value, list):
                return all(value_safe(v) for v in value)
            return False
        if all(re.fullmatch(r"detail|page|rule_ids|number|loop|loop_id|owner|status|limit|scope", key) and value_safe(value) for key, value in inp.items()):
            return {"tool_name": name, "tool_input": inp}, "standing_read" if name.endswith("__standing-context") else "governance_read"
    if name == "Read":
        path = safe_path(inp.get("file_path"), repo)
        if path is None:
            return None
        out = {"file_path": "<repo>/" + path}
        for key in ("offset", "limit"):
            if isinstance(inp.get(key), int):
                out[key] = inp[key]
        return {"tool_name": name, "tool_input": out}, "source_read"
    if name != "Bash":
        return None
    command = inp.get("command")
    if not isinstance(command, str) or len(command) > 350 or SENSITIVE.search(command):
        return None
    command = command.strip()
    # Removing only the known checkout prefix preserves the command action.
    command = re.sub(r"^cd /Users/booko/carr-system(?:/\.claude/worktrees/[a-zA-Z0-9_.-]+)? && ", "", command)
    if any(c in command for c in "\n`$;|<>") or "&&" in command or "||" in command:
        return None
    groups = (
        ("git_status", r"git status(?: --[a-z-]+)*(?: -[a-zA-Z]+)?"),
        ("git_diff", r"git diff(?: (?:--[a-z-]+|[0-9a-f]{7,40}|origin/main|HEAD|HEAD~[0-9]+))*"),
        ("git_log", r"git log(?: (?:--[a-z-]+(?:=[0-9]+)?|-[0-9]+|-[a-zA-Z]+|origin/main|HEAD|HEAD~[0-9]+))*"),
        ("git_read", r"git (?:rev-parse|rev-list|branch|show)(?: (?:--[a-z-]+|-[a-zA-Z]+|origin/main|HEAD|HEAD~[0-9]+|[0-9a-f]{7,40}))*"),
        ("pr_read", r"gh pr (?:view|checks|diff|list)(?: [0-9]+)?(?: --(?:json [a-zA-Z_,]+|state (?:open|closed|merged|all)|limit [0-9]+|watch|web|verbose))*"),
        ("ci_check", r"(?:\./)?ops/ci\.sh(?: --(?:strict|list|only [a-z0-9-]+))*"),
        ("selftest", r"(?:\./\.venv/bin/python|python3?|\./\.venv/bin/python3) (?:ops|hooks|tools)/[a-z0-9_./-]+(?:selftest|self-test|test)[a-z0-9_.-]*\.py(?: --[a-z-]+)*"),
        ("clock", r"date(?: -u)?(?: \+['\"]?[%a-zA-Z :+0-9-]+['\"]?)?"),
        ("standing_read", r"(?:\./)?run\.sh call standing-context '\{[a-zA-Z0-9_\"{},:\[\]. -]*\}'"),
        ("health_read", r"(?:\./)?run\.sh health"),
    )
    for category, pattern in groups:
        if re.fullmatch(pattern, command):
            return {"tool_name": name, "tool_input": {"command": command}}, category
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=pathlib.Path, default=pathlib.Path.home() / ".claude/projects")
    parser.add_argument("--repo", type=pathlib.Path, required=True)
    parser.add_argument("--tracked-paths", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--since", default="2026-09-26")
    args = parser.parse_args()
    global TRACKED
    TRACKED = set(args.tracked_paths.read_text().splitlines())
    rows, counts, seen_by_session, ids_seen = [], collections.Counter(), {}, set()
    for directory in sorted(args.source_root.iterdir()):
        if not directory.is_dir() or not directory.name.startswith("-Users-booko-carr-system"):
            continue
        for path in sorted(directory.glob("*.jsonl")):
            counts["sessions_scanned"] += 1
            session = digest(path.stem.encode())[:20]
            split = "test" if int(digest(("ruleprecision-real-turn-v1:" + session).encode())[:8], 16) % 10 < 3 else "train"
            file_hash = digest(path.read_bytes())
            seen = seen_by_session.setdefault(session, set())
            for line_no, line in enumerate(path.open(encoding="utf-8", errors="replace"), 1):
                try:
                    row = json.loads(line)
                except ValueError:
                    counts["malformed_rows"] += 1
                    continue
                if str(row.get("timestamp", ""))[:10] < args.since or row.get("isSidechain"):
                    continue
                message = row.get("message")
                if not isinstance(message, dict):
                    continue
                candidates = []
                if row.get("type") == "user":
                    p = safe_prompt(prompt_text(message))
                    if p:
                        candidates.append(("user", p, [], "user_technical"))
                elif row.get("type") == "assistant" and isinstance(message.get("content"), list):
                    for block in message["content"]:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            call = safe_call(block.get("name"), block.get("input"), args.repo)
                            if call:
                                candidates.append(("tool", "", [call[0]], call[1]))
                for kind, prompt, calls, category in candidates:
                    counts["safe_candidates"] += 1
                    key = json.dumps([prompt, calls], sort_keys=True)
                    if key in seen:
                        counts["session_duplicates_excluded"] += 1
                        continue
                    seen.add(key)
                    source_hash = digest(line.encode())
                    out = {"id": "real-" + digest((session + ":" + str(line_no) + ":" + key).encode())[:20],
                           "session_group": session, "split": split, "event_kind": kind,
                           "category": category, "stratum": "chat_only" if kind == "user" else "engineering",
                           "prompt": prompt, "tool_calls": calls,
                           "origin": "real-user-prompt" if kind == "user" else "real-tool-event",
                           "opens_session": False, "reply_read_by": "partner",
                           "source": {"session_sha256": digest(path.stem.encode()),
                                      "file_sha256": file_hash, "row_sha256": source_hash,
                                      "line": line_no, "timestamp": row.get("timestamp"),
                                      "project_sha256": digest(directory.name.encode())},
                           "sanitization": "strict prompt vocabulary or input grammar; known checkout prefix removed; Read path replaced with <repo>; no result/assistant prose"}
                    if out['id'] not in ids_seen:
                        ids_seen.add(out['id'])
                        rows.append(out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    print(json.dumps({"counts": dict(counts), "event_kinds": dict(collections.Counter(r['event_kind'] for r in rows)), "categories": dict(collections.Counter(r['category'] for r in rows)), "safe_sessions": len({r['session_group'] for r in rows}), "out": str(args.out)}))


if __name__ == "__main__":
    main()
