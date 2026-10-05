#!/usr/bin/env python3
"""brief-check.py — before the orchestrator reviews a builder PR, say which
numbered requirement of the builder's brief has evidence in the PR.

    ops/brief-check.py <owner/repo> <pr> [--brief PATH] [--post] [--jev] [--json]
    ops/brief-check.py --stamp <brief>

WHY (Joe 2026-10-05, gap #17). Review time went to re-deriving whether the
builder did each numbered item. That part is mostly mechanical: did the diff
touch the file the brief named, did tests land and is CI green on them, does
the PR body carry what the brief asked to put there. This does the mechanical
part and leaves the reviewer the residue that needs judgment.

THE BRIEF RECORD CONVENTION. A builder states which brief it ran as one line in
the PR body:

    Brief: out/orch/gaps/gap17.md sha256:<64 hex>

The path is relative to the orchestrator checkout that holds the brief (or
absolute when the brief lives outside a git checkout). Runners append the
instruction to the prompt they hand the builder:

    { cat "$P"; python3 ~/carr-system/ops/brief-check.py --stamp "$P"; } | <builder>

The hash lets this check say whether the brief changed after the builder ran,
in which case the requirements it reads are not the ones the builder saw.

THE PROCEDURE, per numbered requirement. The requirement is split into
sentences; each sentence runs these ordered probes, every one deterministic:

  1. Does it ask for tests (test, tests, selftest)?
       no test file in the diff           -> fail
       a CI check failed                  -> fail
       CI still running, or no checks     -> judgment
       test files in the diff, CI green   -> pass
  2. Does it ask for PR-body content ("PR body", "in the PR")?
       empty body                         -> fail
       a paragraph carries its key terms  -> pass, quoted
       otherwise                          -> judgment
  3. For each repository path it names:
       changed in the diff                -> pass, linked to the diff
       named in a CI check's name         -> that check's result
       exists on the base branch / disk   -> reference only, no evidence either way
       exists nowhere                     -> fail (a deliverable that was never made)

  A sentence with any fail fails; with any judgment, or with no passing probe,
  needs judgment; otherwise passes. A requirement is "not met" if any sentence
  fails, "met" if every sentence passes, else "needs judgment".

  "met" therefore means every sentence had positive deterministic evidence. It
  is evidence for the reviewer, not a verdict on quality, and this tool never
  approves: it only ever creates or edits its own marked comment.

JEV, ONLY ON THE RESIDUE. With --jev, the needs-judgment requirements go to Jev
in ONE batched request, a noul each, and the answer is printed beside the item
labelled as advisory. It never moves a verdict. Off by default: paid Jev calls
in the review path were switched off on 2026-10-04 for cost, so the caller opts
in per run.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKER = "<!-- brief-check -->"
VERDICTS = ("met", "not met", "needs judgment")
STAMP_RE = re.compile(r"^Brief: (\S+) sha256:([0-9a-f]{64})\s*$", re.M)

# A list header is a line that says the list is required. The job-conduct
# section ("RULES FOR THIS JOB (all required):") is not a build requirement.
HEADER_RE = re.compile(r"required", re.I)
NOT_REQUIREMENTS_RE = re.compile(r"^\W*RULES\b")
ITEM_RE = re.compile(r"^(\d+)\.\s+(.*)$")
# A caps label ("BOUNDARIES:", "RULES FOR THIS JOB (...):") or a markdown heading ends a list.
SECTION_RE = re.compile(r"^(#|[A-Z][A-Z /-]{2,}(\([^)]*\))?:)")

TESTS_RE = re.compile(r"\b(tests?|selftests?|test-first)\b", re.I)
BODY_RE = re.compile(r"\bPR (body|description)\b|\bin the PR\b", re.I)
PATH_RE = re.compile(r"(?<![\w/~.:@-])(\.?[A-Za-z0-9_][A-Za-z0-9_.-]*/(?:[A-Za-z0-9_.-]+/)*(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*\.[A-Za-z0-9]{1,6})?)(?![\w*/<-])")
ALTERNATIVES_RE = re.compile(PATH_RE.pattern + r"\s+or\s+" + PATH_RE.pattern)
TABLE_RE = re.compile(r"^\|.*\|", re.M)
TEST_FILE_RE =re.compile(r"(^|/)(tests?|__tests__|e2e)/|(^|/)test[-_][^/]*$|[-_.](selftest|test|spec)\.[A-Za-z0-9]+$")
STOP = set("""about above after again against also because been before being below between both
build builder change changes code does done each every file files first from have into just like
made make must name never only other over same should show some such than that their them then
there these they this those through under until very want when where which while will with
without would your body description section include report state evidence list item items put what""".split())
GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}
PENDING_STATES = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}
REVIEW_LINE_RE = re.compile(r"^(APPROVE|Reviewed-SHA:|REVIEW: BLOCKED|CHANGES REQUESTED)", re.M)
COMMENT_ENDPOINT_RE = re.compile(r"^repos/[\w.-]+/[\w.-]+/issues/(\d+/comments|comments/\d+)$")


class BriefError(Exception):
    pass


# ── brief ─────────────────────────────────────────────────────────────────────

def parse_requirements(text):
    """The numbered items under the first header that says they are required."""
    lines = text.splitlines()
    for start, line in enumerate(lines):
        if HEADER_RE.search(line) and not NOT_REQUIREMENTS_RE.match(line) and (
                line.lstrip().startswith("#") or line.rstrip().endswith(":") or SECTION_RE.match(line)):
            items = _numbered_list(lines[start + 1:])
            if items:
                return items
    raise BriefError("no numbered list under a 'required' header in the brief")


def _numbered_list(lines):
    items = []
    for line in lines:
        item = ITEM_RE.match(line)
        if item:
            items.append({"n": int(item.group(1)), "text": item.group(2).strip()})
        elif not line.strip():
            if items:
                break
        elif SECTION_RE.match(line) or not items:
            if items:
                break
        else:
            items[-1]["text"] += " " + line.strip()
    return items


def sentences(text):
    """Split on . ! ? followed by a capital, never inside parentheses."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        depth += (ch == "(") - (ch == ")")
        if ch in ".!?" and depth <= 0 and re.match(r"\s+[A-Z0-9\"“]", text[i + 1:]):
            out.append(text[start:i + 1].strip())
            start = i + 1
    tail = text[start:].strip()
    return [s for s in out + [tail] if s]


def stamp_line(path, sha):
    return f"Brief: {path} sha256:{sha}"


def stamp_preamble(brief_path, recorded_as=None):
    with open(brief_path, encoding="utf-8") as handle:
        sha = hashlib.sha256(handle.read().encode()).hexdigest()
    line = stamp_line(recorded_as or _recorded_path(brief_path), sha)
    return ("\nBRIEF RECORD (required): put this line, verbatim, on its own line in the PR body, "
            "so the reviewer's brief check reads the brief you ran:\n" + line + "\n")


def _recorded_path(brief_path):
    absolute = os.path.abspath(brief_path)
    root = _main_checkout(os.path.dirname(absolute))
    return os.path.relpath(absolute, root) if root and absolute.startswith(root + os.sep) else absolute


def _main_checkout(directory):
    """The main checkout owning `directory` (worktrees share its common dir)."""
    try:
        common = subprocess.run(["git", "-C", directory, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return os.path.dirname(common)


def read_stamp(body):
    found = STAMP_RE.search(body or "")
    return (found.group(1), found.group(2)) if found else None


def stamp_state(recorded_sha, brief_text):
    if recorded_sha is None:
        return "not recorded in the PR body"
    current = hashlib.sha256(brief_text.encode()).hexdigest()
    return "matches" if current == recorded_sha else "changed since the builder ran"


# ── evidence ──────────────────────────────────────────────────────────────────

def _ci(checks):
    """(state, failed, pending, green) over a statusCheckRollup."""
    failed, pending, green = [], [], []
    for check in checks:
        name = check.get("name") or check.get("context") or "check"
        url = check.get("detailsUrl") or check.get("targetUrl") or ""
        if check.get("__typename") == "StatusContext" or "state" in check and "status" not in check:
            state = (check.get("state") or "").upper()
            bucket = green if state == "SUCCESS" else pending if state in PENDING_STATES else failed
            bucket.append((name, url, state))
            continue
        if (check.get("status") or "").upper() != "COMPLETED":
            pending.append((name, url, check.get("status")))
        elif (check.get("conclusion") or "").upper() in GREEN:
            green.append((name, url, check.get("conclusion")))
        else:
            failed.append((name, url, check.get("conclusion")))
    state = "failed" if failed else "pending" if pending or not checks else "green"
    return state, failed, pending, green


def _link(name, url):
    return f"[{name}]({url})" if url else name


def _diff_link(pr, path):
    return f"[`{path}`]({pr['url']}/files#diff-{hashlib.sha256(path.encode()).hexdigest()})"


def _terms(text):
    """{stem: word as written} for the words that carry the ask."""
    text = re.sub(r"\([^)]*\)", " ", text)
    words = {}
    for word in re.findall(r"[a-z0-9][a-z0-9-]*", text.lower()):
        if len(word) >= 4 and word not in STOP and _stem(word) not in STOP:
            words.setdefault(_stem(word), word)
    return words


def _stem(word):
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def _paragraphs(body):
    return [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]


def _top_level_split(text, pattern):
    """Split on `pattern` only where no parenthesis is open."""
    parts, depth, start = [], 0, 0
    for at, ch in enumerate(text):
        depth += (ch == "(") - (ch == ")")
        found = re.match(pattern, text[at:]) if depth <= 0 else None
        if found and at >= start:
            parts.append(text[start:at])
            start = at + found.end()
    return parts + [text[start:]]


def _body_probe(sentence, body):
    """Each comma- or and-separated thing the sentence asks the body for must
    appear in some paragraph: 60% of its terms, at least one."""
    if not (body or "").strip():
        return "fail", "PR body is empty"
    paragraphs = [(p, {_stem(w) for w in re.findall(r"[a-z0-9][a-z0-9-]*", p.lower())}) for p in _paragraphs(body)]
    asks = [_terms(c) for c in _top_level_split(BODY_RE.sub(" ", sentence), r"[,;]\s*|\s+and\s+")]
    asks = [a for a in asks if a]
    if not asks:
        return "judge", "PR body: nothing in the requirement to match against"
    def hits(ask, paragraph):
        text, words = paragraph
        return sum(t in words or (t == "table" and bool(TABLE_RE.search(text))) for t in ask)

    found, missing = [], []
    for ask in asks:
        best = max(paragraphs, key=lambda p: hits(ask, p))
        enough = hits(ask, best) >= max(1, math.ceil(0.6 * len(ask)))
        (found if enough else missing).append((ask, best[0]))
    if not missing:
        excerpts = dict.fromkeys(re.sub(r"\s+", " ", p)[:160] for _, p in found)
        return "pass", "PR body: " + " … ".join(f"“{e}”" for e in excerpts)
    return "judge", "PR body has nothing on: " + "; ".join(" ".join(a.values()) for a, _ in missing)


def _tests_probe(pr, changed):
    tests = [p for p in changed if TEST_FILE_RE.search(p)]
    if not tests:
        return [("fail", "no test file in the diff")]
    listed = "tests in diff: " + ", ".join(_diff_link(pr, p) for p in tests[:6]) + (
        f" (+{len(tests) - 6} more)" if len(tests) > 6 else "")
    state, failed, pending, green = _ci(pr.get("statusCheckRollup") or [])
    if state == "failed":
        return [("fail", listed), ("fail", "CI " + "; ".join(f"{c} {_link(n, u)}" for n, u, c in failed))]
    if state == "pending":
        total = len(pr.get("statusCheckRollup") or [])
        detail = f"CI pending: {len(pending)} of {total} checks still running" if total else "CI pending: no checks reported"
        return [("pass", listed), ("judge", detail)]
    return [("pass", listed), ("pass", f"CI green ({len(green)} checks), e.g. {_link(green[0][0], green[0][1])}")]


def _path_probe(pr, changed, path, exists_in_base, local_reference):
    is_dir = path.endswith("/")
    if path in changed or (is_dir and any(p.startswith(path) for p in changed)):
        return "pass", "diff: " + (_diff_link(pr, path) if not is_dir else f"`{path}` ({sum(p.startswith(path) for p in changed)} files)")
    for check in pr.get("statusCheckRollup") or []:
        name = check.get("name") or check.get("context") or ""
        if path in name.split():
            state, failed, pending, _ = _ci([check])
            url = check.get("detailsUrl") or check.get("targetUrl") or ""
            word = {"green": "pass", "failed": "fail", "pending": "judge"}[state]
            return word, f"check {_link(f'`{name}`', url)} {state}"
    if exists_in_base is not None and exists_in_base(path):
        return "neutral", f"`{path}` named, exists on the base branch, unchanged"
    if local_reference is not None and local_reference(path):
        return "neutral", f"`{path}` named, a local file the repository does not track"
    if exists_in_base is None:
        return "judge", f"`{path}` not in the diff (base branch not checked)"
    return "fail", f"`{path}` is in neither the diff nor the base branch"


def _sentence(pr, changed, sentence, exists_in_base, local_reference):
    probes = []
    if TESTS_RE.search(sentence):
        probes += _tests_probe(pr, changed)
    if BODY_RE.search(sentence):
        probes.append(_body_probe(sentence, pr.get("body")))
    # "tools/ or ops/" is one ask with two acceptable homes: the best result stands for both.
    groups = {path: [path] for path in PATH_RE.findall(sentence)}
    for first, second in ALTERNATIVES_RE.findall(sentence):
        groups[first] = groups[second] = [first, second]
    for group in dict.fromkeys(tuple(g) for g in groups.values()):
        results = [_path_probe(pr, changed, p, exists_in_base, local_reference) for p in group]
        probes.append(min(results, key=lambda r: ("pass", "neutral", "judge", "fail").index(r[0])))
    states = {s for s, _ in probes}
    if "fail" in states:
        verdict = "fail"
    elif "judge" in states or "pass" not in states:
        verdict = "judge"
    else:
        verdict = "pass"
    evidence = [e for _, e in probes]
    if verdict == "judge" and not any(s in ("judge", "pass") for s, _ in probes):
        evidence.append("no deterministic evidence for: “" + sentence[:90] + ("…" if len(sentence) > 90 else "") + "”")
    return verdict, evidence


def check(brief_text, pr, *, exists_in_base=None, local_reference=None, brief_path=None):
    """The report: one row per numbered requirement, verdict plus evidence.

    exists_in_base(path) and local_reference(path) answer whether a named path
    is on the base branch, or is a local file the repository does not track
    (present on disk or gitignored, like the out/orch briefs). None means
    unknown, which turns a missing path into needs judgment rather than not met.
    """
    changed = [f["path"] for f in pr.get("files") or []]
    stamp = read_stamp(pr.get("body"))
    rows = []
    for item in parse_requirements(brief_text):
        results = [_sentence(pr, changed, s, exists_in_base, local_reference) for s in sentences(item["text"])]
        found = [r for r, _ in results]
        verdict = "not met" if "fail" in found else "met" if all(r == "pass" for r in found) else "needs judgment"
        evidence = list(dict.fromkeys(e for _, ev in results for e in ev))
        rows.append({"n": item["n"], "text": item["text"], "verdict": verdict, "evidence": evidence})
    state, _, _, _ = _ci(pr.get("statusCheckRollup") or [])
    return {
        "pr": pr.get("number"), "url": pr.get("url"), "head": pr.get("headRefOid"),
        "brief": brief_path or (stamp[0] if stamp else None),
        "brief_state": stamp_state(stamp[1] if stamp else None, brief_text),
        "ci": state, "requirements": rows,
    }


# ── Jev on the residue ────────────────────────────────────────────────────────

def add_jev(report, *, judge=None, noul=None):
    residue = [r for r in report["requirements"] if r["verdict"] == "needs judgment"]
    if not residue:
        return report
    if judge is None or noul is None:
        judge, noul = _jev()
    questions = {f"r{r['n']}": noul(
        f"Requirement {r['n']} of the builder's brief: {r['text']}\n"
        "Using only the pull request evidence in the state, was this requirement met?") for r in residue}
    subject = {"pr_body": (report.get("body") or "")[:6000], "evidence": {f"r{r['n']}": r["evidence"] for r in residue}}
    try:
        answer = judge(subject, questions, caller="brief_check")
    except Exception as exc:  # any failure is reported beside the item, never hidden
        for r in residue:
            r["jev"] = f"Jev unavailable ({type(exc).__name__}: {str(exc)[:80]})"
        return report
    for r in residue:
        p = float(answer["answers"][f"r{r['n']}"]["noul"])
        word = "likely met" if p >= 0.8 else "likely not met" if p <= 0.2 else "unsure"
        r["jev"] = f"Jev (advisory, needs-judgment residue): {word}, p={p:.2f}"
    return report


def _jev():
    def load(name):
        spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "ops", f"{name}.py"))
        assert spec is not None and spec.loader is not None, f"cannot load ops/{name}.py"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return load("jev_judge").judge, load("typesafe_client").noul


# ── comment ───────────────────────────────────────────────────────────────────

def _cell(text):
    return re.sub(r"\s+", " ", text).replace("|", "\\|")


def render_comment(report):
    counts = {v: sum(r["verdict"] == v for r in report["requirements"]) for v in VERDICTS}
    lines = [
        MARKER,
        "### Brief check (evidence only; this comment never approves)",
        "",
        f"Checked head: `{report['head']}` · CI: {report['ci']} · brief: `{report['brief'] or 'unknown'}` "
        f"({report['brief_state']})",
        "",
        f"**{counts['met']} met · {counts['not met']} not met · {counts['needs judgment']} need judgment**",
        "",
        "| # | Requirement | Verdict | Evidence |",
        "|---|---|---|---|",
    ]
    for r in report["requirements"]:
        evidence = "<br>".join(_cell(e) for e in r["evidence"] + ([r["jev"]] if r.get("jev") else []))
        text = r["text"] if len(r["text"]) <= 220 else r["text"][:220] + "…"
        lines.append(f"| {r['n']} | {_cell(text)} | **{r['verdict']}** | {evidence} |")
    lines += ["", "<sub>ops/brief-check.py: met = every sentence of the requirement has deterministic evidence; "
              "needs judgment = the reviewer decides. Re-runs edit this comment in place.</sub>"]
    text = "\n".join(lines) + "\n"
    assert not REVIEW_LINE_RE.search(text), "a brief-check comment must never read as a review verdict"
    return text


# ── gh, comment endpoints only ────────────────────────────────────────────────

def guarded(args):
    """The only gh calls this tool makes: read the PR, read and write its own comment."""
    args = list(args)
    if args[:2] == ["pr", "view"]:
        return args
    if args and args[0] == "api":
        rest = [a for a in args[1:] if not a.startswith("-")]
        method = args[args.index("-X") + 1] if "-X" in args else "GET"
        endpoint = next((a for a in rest if a.startswith("repos/")), "")
        if method in ("GET", "POST", "PATCH") and COMMENT_ENDPOINT_RE.match(endpoint):
            return args
    raise BriefError("refused gh call outside the comment endpoints: " + " ".join(args))


def run_gh(args, stdin=None):
    try:
        return subprocess.run(["gh", *guarded(args)], input=stdin, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as exc:
        raise BriefError(f"gh {' '.join(args[:3])} failed: {((exc.stderr or '').strip().splitlines() or [''])[-1][:300]}") from None


def _json_stream(text):
    decoder, at, items = json.JSONDecoder(), 0, []
    while at < len(text.strip()):
        while text[at].isspace():
            at += 1
        value, at = decoder.raw_decode(text, at)
        items += value if isinstance(value, list) else [value]
        if at >= len(text) or not text[at:].strip():
            break
    return items


def post_comment(repo, number, body, *, gh=run_gh):
    comments = _json_stream(gh(guarded(["api", "--paginate", f"repos/{repo}/issues/{number}/comments"])))
    mine = [c for c in comments if (c.get("body") or "").startswith(MARKER)]
    if mine:
        args = ["api", "-X", "PATCH", f"repos/{repo}/issues/comments/{mine[-1]['id']}", "-F", "body=@-"]
    else:
        args = ["api", "-X", "POST", f"repos/{repo}/issues/{number}/comments", "-F", "body=@-"]
    return json.loads(gh(guarded(args), stdin=body)).get("html_url")


# ── command line ──────────────────────────────────────────────────────────────

def _git_exists(checkout, ref):
    def exists(path):
        return subprocess.run(["git", "-C", checkout, "cat-file", "-e", f"{ref}:{path.rstrip('/')}"],
                              capture_output=True).returncode == 0
    return exists


def _local_reference(checkout):
    def local(path):
        return os.path.exists(os.path.join(checkout, path)) or subprocess.run(
            ["git", "-C", checkout, "check-ignore", "-q", "--no-index", path], capture_output=True).returncode == 0
    return local


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("repo", nargs="?")
    parser.add_argument("pr", nargs="?", type=int)
    parser.add_argument("--stamp", metavar="BRIEF", help="print the brief-record instruction for a builder prompt")
    parser.add_argument("--brief", help="brief file (default: the Brief: line in the PR body)")
    parser.add_argument("--brief-root", help="where a relative Brief: path resolves (default: this repo's main checkout)")
    parser.add_argument("--checkout", help="local clone of <repo> for base-branch lookups (default: ~/<repo name>)")
    parser.add_argument("--post", action="store_true", help="create or update the one brief-check comment on the PR")
    parser.add_argument("--jev", action="store_true", help="ask Jev about the needs-judgment residue (paid, advisory)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON instead of the comment")
    args = parser.parse_args(argv)

    if args.stamp:
        sys.stdout.write(stamp_preamble(args.stamp))
        return 0
    if not args.repo or not args.pr:
        parser.error("<owner/repo> <pr> are required unless --stamp is given")

    pr = json.loads(run_gh(["pr", "view", str(args.pr), "-R", args.repo, "--json",
                            "number,url,headRefOid,baseRefName,body,files,statusCheckRollup"]))
    brief_path = args.brief
    if not brief_path:
        stamp = read_stamp(pr.get("body"))
        if not stamp:
            print(f"brief-check: PR {args.pr} carries no 'Brief: <path> sha256:<hex>' line; pass --brief", file=sys.stderr)
            return 2
        root = args.brief_root or _main_checkout(REPO) or REPO
        brief_path = stamp[0] if os.path.isabs(stamp[0]) else os.path.join(root, stamp[0])
    with open(brief_path, encoding="utf-8") as handle:
        brief_text = handle.read()

    checkout = args.checkout or os.path.expanduser(f"~/{args.repo.split('/')[-1]}")
    has_checkout = os.path.isdir(os.path.join(checkout, ".git")) or os.path.isfile(os.path.join(checkout, ".git"))
    report = check(
        brief_text, pr,
        exists_in_base=_git_exists(checkout, f"origin/{pr.get('baseRefName') or 'main'}") if has_checkout else None,
        local_reference=_local_reference(checkout) if has_checkout else None,
        brief_path=args.brief or read_stamp(pr.get("body"))[0])
    if args.jev:
        report["body"] = pr.get("body")
        add_jev(report)
        report.pop("body", None)

    comment = render_comment(report)
    print(json.dumps(report, indent=2) if args.json else comment)
    if args.post:
        print("posted:", post_comment(args.repo, args.pr, comment), file=sys.stderr)
    return 1 if any(r["verdict"] == "not met" for r in report["requirements"]) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BriefError as exc:
        print(f"brief-check: {exc}", file=sys.stderr)
        sys.exit(2)
