#!/usr/bin/env python3
"""check-eval-receipt.py — a change to an LLM-steering surface ships with a measurement.

THE RULE. evals/surfaces.json registers every file in this repository whose
text or configuration changes what a model reads or which model runs. A pull
request that changes one of them carries, for each surface it touches, either

  evals/<surface>/receipt.json   changed IN THIS pull request, and valid, or
  no-eval: <surface>: <reason>   a line in the pull-request body naming why a
                                 measurement is impossible.

The receipt is what /claude-api build-eval and /claude-api hillclimb produce,
written down: cases and their split, repeats, the grader and its validation,
and baseline against candidate on the SEALED TEST split, per dimension, with
confidence intervals and a verdict. evals/README.md is the procedure.

ONE EVALUATION SYSTEM, NOT TWO. CARR's standing evaluation ruling is the
laddered, multidimensional portfolio in tools/room-bridge/evaluation_kernel.py:
rungs smoke / regression / hill_climb / launch, named dimensions with no
aggregate, results bound to user-job stages and to the model + harness +
adapter that produced them, and a critical-dimension regression that no
headline can hide. A receipt is a small projection of that shape, and this file
reuses the kernel's own vocabulary and its critical_dimension_blockers() rather
than carrying a second copy. Offline suites bind through ops/ai_eval.py.

WHAT A RECEIPT MAY NOT SAY. "ship" when the primary dimension's paired delta
interval contains zero, when a critical dimension failed or regressed, when the
grader failed its validation, or when the eval's noise floor is not smaller
than the smallest gain worth having. A gain inside the noise must say "do not
merge on quality grounds" in its verdict; it may still ship on cost at parity
when it is cheaper per case.

WHERE IT ENFORCES. In a pull_request run (GITHUB_EVENT_NAME=pull_request and a
readable GITHUB_EVENT_PATH) a missing receipt or line fails; an unreadable PR
event refuses the check. A changed surface whose receipt says do_not_merge or
inconclusive also fails, even when that verdict correctly describes its data.
Anywhere else, the
pre-push floor and local runs included, there is no pull-request body to read,
so a missing receipt is reported and does not fail; a MALFORMED receipt, an
unregistered context-emitting hook, or a broken registry fails everywhere.
The event payload is the body as of the last push: edit the body, then push
(or re-run with a fresh event) for the line to count.

Exit 0 pass, 1 refusal, 2 the check could not run (registry unreadable, no base
in a pull-request run).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "tools" / "room-bridge"))
import ai_eval  # noqa: E402  (also puts room-bridge on the path)
import evaluation_kernel as kernel  # noqa: E402
import execution_contract as contract  # noqa: E402

SURFACE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CASE_SOURCES = {"production_trace", "human_judged_hard_case", "bug_report", "hand_written", "synthesized"}
REAL_SOURCES = {"production_trace", "human_judged_hard_case"}
GRADER_KINDS = {"programmatic", "pairwise", "pointwise_rubric", "human"}
DECISIONS = {"ship", "ship_cost_at_parity", "do_not_merge", "inconclusive"}
IN_NOISE_PHRASE = "do not merge on quality grounds"
REQUIRED = {"schema_version", "surface", "change", "measured_on", "rung", "adapter", "cases", "split",
            "repeats", "grader", "noise_floor", "min_useful_gain", "primary_dimension", "dimensions",
            "stage_results", "cost", "verdict"}
OPTIONAL = {"overall", "offline_suite", "rounds", "notes"}
MEASURE_FIELDS = {"baseline", "candidate", "delta"}
PLACEHOLDER_REASONS = re.compile(r"^(n/?a|none|tbd|trivial|minor|docs?( only)?|not needed.*|no change.*|skip.*|-+)$", re.I)
MIN_REASON_WORDS = 10
NO_EVAL = re.compile(r"^ {0,3}(?:[-*] )?no-eval:\s*([^:\s]+)\s*:\s*(.*?)\s*$", re.I)
CONTEXT_EMITTER = re.compile(r"additionalContext|systemMessage|hookSpecificOutput")


class GateError(ValueError):
    pass


# ------------------------------------------------------------------ registry
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?"); i += 3
        elif pattern.startswith("**", i):
            out.append(".*"); i += 2
        elif pattern[i] == "*":
            out.append("[^/]*"); i += 1
        elif pattern[i] == "?":
            out.append("[^/]"); i += 1
        else:
            out.append(re.escape(pattern[i])); i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(path: str, pattern: str) -> bool:
    return bool(glob_to_regex(pattern).match(path))


def validate_registry(reg: Any) -> dict[str, Any]:
    if not isinstance(reg, dict) or reg.get("schema_version") != 1:
        raise GateError("registry schema_version must be 1")
    if not isinstance(reg.get("surfaces"), list) or not reg["surfaces"]:
        raise GateError("registry needs surfaces")
    seen = set()
    for s in reg["surfaces"]:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not SURFACE_ID.match(s["id"]):
            raise GateError(f"surface id is invalid: {s.get('id') if isinstance(s, dict) else s!r}")
        if s["id"] in seen:
            raise GateError(f"duplicate surface id {s['id']}")
        seen.add(s["id"])
        if not isinstance(s.get("globs"), list) or not s["globs"] or not all(isinstance(g, str) and g for g in s["globs"]):
            raise GateError(f"surface {s['id']} needs non-empty globs")
    if not isinstance(reg.get("exclude_globs", []), list):
        raise GateError("exclude_globs must be a list")
    return reg


def load_registry(path: Path) -> dict[str, Any]:
    try:
        return validate_registry(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read registry {path}: {exc}") from exc


def surfaces_for(path: str, reg: dict[str, Any]) -> list[str]:
    if path.startswith("evals/"):
        return []  # the procedure, the registry and the receipts are not steering text
    if any(glob_match(path, g) for g in reg.get("exclude_globs", [])):
        return []
    return [s["id"] for s in reg["surfaces"] if any(glob_match(path, g) for g in s["globs"])]


def unregistered_context_hooks(root: Path, reg: dict[str, Any]) -> list[str]:
    """Hooks that put text in front of a model but that no surface names."""
    missing = []
    for hook in sorted((root / "hooks").glob("*.py")):
        rel = hook.relative_to(root).as_posix()
        try:
            text = hook.read_text(errors="replace")
        except OSError:
            continue
        if CONTEXT_EMITTER.search(text) and not surfaces_for(rel, reg):
            missing.append(rel)
    return missing


# ------------------------------------------------------------------ receipts
def _num(value: Any, lo: float | None = None, hi: float | None = None) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def _int(value: Any, lo: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= lo


def _measure(label: str, m: Any, errs: list[str], *, lo: float = 0.0, hi: float = 1.0, point: str = "score") -> dict | None:
    if not isinstance(m, dict) or set(m) != {point, "ci_low", "ci_high"}:
        errs.append(f"{label} must be exactly {{{point}, ci_low, ci_high}}")
        return None
    if not all(_num(m[k], lo, hi) for k in m):
        errs.append(f"{label} values must be numbers in [{lo}, {hi}]")
        return None
    if not m["ci_low"] <= m[point] <= m["ci_high"]:
        errs.append(f"{label}: {point} {m[point]} lies outside its own interval [{m['ci_low']}, {m['ci_high']}]")
        return None
    return m


def _direction(delta: dict) -> str:
    if delta["ci_high"] < 0:
        return "regressed"
    if delta["ci_low"] > 0:
        return "improved"
    return "equivalent"


def validate_receipt(r: Any, surface: str, root: Path = ROOT) -> list[str]:
    errs: list[str] = []
    if not isinstance(r, dict):
        return ["receipt must be a JSON object"]
    missing, unknown = REQUIRED - set(r), set(r) - REQUIRED - OPTIONAL
    if missing:
        errs.append(f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        errs.append(f"unknown fields: {', '.join(sorted(unknown))}")
    if missing:
        return errs
    if r["schema_version"] != 1:
        errs.append("schema_version must be 1")
    if r["surface"] != surface:
        errs.append(f"surface is {r['surface']!r} but the receipt lives under evals/{surface}/")
    for key in ("change", "measured_on"):
        if not isinstance(r[key], str) or not r[key].strip():
            errs.append(f"{key} must be a non-empty string")
    if r["rung"] not in kernel.RUNGS:
        errs.append(f"rung must be one of the kernel ladder {sorted(kernel.RUNGS)}")

    # Model + harness + adapter, in the execution contract's own field set.
    a = r["adapter"]
    if not isinstance(a, dict) or set(a) != contract.ADAPTER_FIELDS:
        errs.append(f"adapter must carry exactly {sorted(contract.ADAPTER_FIELDS)}")
    else:
        try:
            if a["surface"] in contract.SURFACES:
                contract._validate_adapter(a)
            else:
                for field in contract.ADAPTER_FIELDS - {"configuration_fingerprint"}:
                    contract._string(a[field], f"adapter {field}")
                contract._digest(a["configuration_fingerprint"], "adapter configuration_fingerprint")
        except contract.ContractError as exc:
            errs.append(str(exc))

    c = r["cases"]
    if not isinstance(c, dict) or not {"total", "train", "test", "should_not_fire", "sources"} <= set(c):
        errs.append("cases must carry total, train, test, should_not_fire, sources")
    else:
        if not (_int(c["total"], 1) and _int(c["train"], 0) and _int(c["test"], 1)):
            errs.append("cases: total and test must be positive integers, train a non-negative integer")
        elif c["train"] + c["test"] != c["total"]:
            errs.append(f"cases: train {c['train']} + test {c['test']} != total {c['total']}")
        if not _int(c["should_not_fire"], 1):
            errs.append("cases.should_not_fire must be at least 1: an eval with no should-not-fire cases rewards firing on everything")
        elif _int(c["total"], 1) and c["should_not_fire"] > c["total"]:
            errs.append("cases.should_not_fire cannot exceed cases.total")
        src = c["sources"]
        if not isinstance(src, list) or not src or not set(src) <= CASE_SOURCES:
            errs.append(f"cases.sources must be a non-empty subset of {sorted(CASE_SOURCES)}")
        elif not set(src) & REAL_SOURCES:
            errs.append("cases.sources must include production_trace or human_judged_hard_case: real traffic and human-judged hard cases come first")

    s = r["split"]
    if not isinstance(s, dict) or not isinstance(s.get("method"), str) or not s["method"].strip():
        errs.append("split.method must name how train and test were drawn")
    elif s.get("sealed_test") is not True:
        errs.append("split.sealed_test must be true: the test split's transcripts are never read while proposing changes")
    if not _int(r["repeats"], 1):
        errs.append("repeats must be a positive integer")

    g = r["grader"]
    grader_ok = False
    if not isinstance(g, dict) or g.get("kind") not in GRADER_KINDS or not isinstance(g.get("validation"), dict):
        errs.append(f"grader needs kind in {sorted(GRADER_KINDS)} and a validation block")
    else:
        v = g["validation"]
        if v.get("graded_twice") is not True:
            errs.append("grader.validation.graded_twice must be true: grade the same outputs twice before trusting the grader")
        if not _num(v.get("agreement"), 0, 1):
            errs.append("grader.validation.agreement must be a number in [0, 1]")
        if v.get("result") not in {"pass", "fail"}:
            errs.append("grader.validation.result must be pass or fail")
        grader_ok = v.get("result") == "pass" and v.get("graded_twice") is True

    if not _num(r["noise_floor"], 0, 1) or not _num(r["min_useful_gain"], 0, 1) or r["min_useful_gain"] == 0:
        errs.append("noise_floor and min_useful_gain must be numbers in [0, 1], min_useful_gain above 0")
        resolvable = False
    else:
        resolvable = r["noise_floor"] < r["min_useful_gain"]

    # Dimensions: measured separately, direction derived from the paired delta.
    dims = r["dimensions"]
    kernel_rows, directions = [], {}
    if not isinstance(dims, list) or not dims:
        errs.append("dimensions must name at least one dimension; a single blended score is not a result")
        dims = []
    for d in dims:
        if not isinstance(d, dict):
            errs.append("each dimension must be an object"); continue
        did = d.get("dimension_id", "?")
        extra = set(d) - kernel.DIMENSION_FIELDS - MEASURE_FIELDS
        if extra or not MEASURE_FIELDS <= set(d):
            errs.append(f"dimension {did}: needs the kernel dimension fields plus baseline, candidate, delta")
            continue
        b = _measure(f"dimension {did} baseline", d["baseline"], errs)
        k = _measure(f"dimension {did} candidate", d["candidate"], errs)
        dl = _measure(f"dimension {did} delta", d["delta"], errs, lo=-1.0, point="value")
        kernel_rows.append({f: d.get(f) for f in kernel.DIMENSION_FIELDS})
        if b and k and dl:
            if abs((k["score"] - b["score"]) - dl["value"]) > 0.011:
                errs.append(f"dimension {did}: delta {dl['value']} does not equal candidate minus baseline "
                            f"({k['score'] - b['score']:.3f})")
            derived = _direction(dl)
            directions[did] = derived
            if d.get("direction_vs_baseline") != derived:
                errs.append(f"dimension {did}: direction_vs_baseline says {d.get('direction_vs_baseline')!r} "
                            f"but the delta interval [{dl['ci_low']}, {dl['ci_high']}] says {derived!r}")
    blockers: list[str] = []
    if kernel_rows:
        try:
            blockers = kernel.critical_dimension_blockers(kernel_rows)
        except kernel.EvalPortfolioError as exc:
            errs.append(f"dimensions: {exc}")
    dim_ids: set[str] = {d["dimension_id"] for d in dims
                         if isinstance(d, dict) and isinstance(d.get("dimension_id"), str)}
    primary = r["primary_dimension"]
    if primary not in dim_ids:
        errs.append(f"primary_dimension {primary!r} is not one of the measured dimensions")

    # User-job stages: every dimension is bound to at least one stage.
    bound: set[str] = set()
    stages = r["stage_results"]
    if not isinstance(stages, list) or not stages:
        errs.append("stage_results must bind the dimensions to user-job stages")
        stages = []
    for st in stages:
        if not isinstance(st, dict) or set(st) != kernel.STAGE_RESULT_FIELDS:
            errs.append(f"stage result must carry exactly {sorted(kernel.STAGE_RESULT_FIELDS)}"); continue
        if st["status"] not in kernel.OUTCOMES:
            errs.append(f"stage {st['stage_id']}: status must be one of {sorted(kernel.OUTCOMES)}")
        ids = st["dimension_ids"] if isinstance(st["dimension_ids"], list) else []
        for unknown_dim in sorted(set(ids) - dim_ids):
            errs.append(f"stage {st['stage_id']} names dimension {unknown_dim} that was not measured")
        bound |= set(ids)
    for unbound in sorted(dim_ids - bound):
        errs.append(f"dimension {unbound} is not bound to any user-job stage")

    cost = r["cost"]
    cheaper = False
    if not isinstance(cost, dict) or not all(_num(cost.get(k), 0) for k in ("baseline_usd_per_case", "candidate_usd_per_case")):
        errs.append("cost must carry baseline_usd_per_case and candidate_usd_per_case (cost at parity is a standing objective)")
    else:
        cheaper = cost["candidate_usd_per_case"] < cost["baseline_usd_per_case"]

    if "offline_suite" in r:
        errs += _offline_suite_errors(r["offline_suite"], root)

    # The verdict has to agree with the numbers.
    verdict = r["verdict"]
    if not isinstance(verdict, dict) or verdict.get("decision") not in DECISIONS or not isinstance(verdict.get("statement"), str):
        errs.append(f"verdict needs decision in {sorted(DECISIONS)} and a statement")
        return errs
    decision, statement = verdict["decision"], verdict["statement"]
    shipping = decision in {"ship", "ship_cost_at_parity"}
    if blockers:
        if decision != "do_not_merge":
            errs.append(f"critical dimension(s) {', '.join(blockers)} failed or regressed: blocking whatever the "
                        f"headline or overall score did; verdict must be do_not_merge")
        for b_id in blockers:
            if b_id not in statement:
                errs.append(f"verdict statement must name the blocking dimension {b_id}")
    primary_dir = directions.get(primary)
    if primary_dir == "regressed" and decision != "do_not_merge":
        errs.append(f"primary dimension {primary} regressed; verdict must be do_not_merge")
    if primary_dir == "equivalent":
        if IN_NOISE_PHRASE not in statement.lower():
            errs.append(f"primary dimension {primary}'s gain is inside the noise (delta interval contains 0); "
                        f"the verdict must say '{IN_NOISE_PHRASE}'")
        if decision == "ship":
            errs.append(f"a gain inside the noise cannot be 'ship'; use do_not_merge, inconclusive, or ship_cost_at_parity")
        if decision == "ship_cost_at_parity" and not cheaper:
            errs.append("ship_cost_at_parity needs the candidate to be cheaper per case than baseline")
    elif decision == "ship_cost_at_parity" and not cheaper:
        errs.append("ship_cost_at_parity needs the candidate to be cheaper per case than baseline")
    if shipping and not grader_ok:
        errs.append("the grader did not pass validation (graded twice, result pass); nothing it scored can ship")
    if shipping and not resolvable:
        errs.append(f"noise floor {r['noise_floor']} is not smaller than the smallest useful gain "
                    f"{r['min_useful_gain']}: this eval cannot see the win it claims")
    return errs


def _offline_suite_errors(block: Any, root: Path) -> list[str]:
    if not isinstance(block, dict) or set(block) != {"path", "suite_digest"}:
        return ["offline_suite must be exactly {path, suite_digest}"]
    path = root / block["path"]
    if not block["path"].startswith("evals/ai/"):
        return ["offline_suite.path must name a suite under evals/ai/ (the ops/ai_eval.py kernel)"]
    try:
        digest = ai_eval.load_suite(path)["_digest"]
    except ai_eval.SuiteError as exc:
        return [f"offline_suite {block['path']}: {exc}"]
    if digest != block["suite_digest"]:
        return [f"offline_suite {block['path']}: suite_digest does not match the suite on disk ({digest})"]
    return []


# ------------------------------------------------------------------ PR body
def parse_no_eval(body: str | None, known: set[str]) -> tuple[dict[str, str], list[str]]:
    found: dict[str, str] = {}
    errs: list[str] = []
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    fence: tuple[str, int] | None = None
    for line in text.replace("\r\n", "\n").split("\n"):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= fence[1] and not marker.group(2).strip():
                fence = None
            continue
        if marker:
            fence = (marker.group(1)[0], len(marker.group(1)))
            continue
        m = NO_EVAL.match(line)
        if not m:
            continue
        surface, reason = m.group(1), m.group(2).strip()
        if surface not in known:
            errs.append(f"no-eval line names unknown surface {surface!r}; registered: {', '.join(sorted(known))}")
            continue
        if PLACEHOLDER_REASONS.match(reason) or len(reason.split()) < MIN_REASON_WORDS:
            errs.append(f"no-eval line for {surface} gives no reason a measurement is impossible "
                        f"(need at least {MIN_REASON_WORDS} words): {reason!r}")
            continue
        found[surface] = reason
    return found, errs


# ------------------------------------------------------------------ git
def git(root: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return p.returncode, (p.stdout or "").strip()


def changed_paths(root: Path, base: str) -> list[str] | None:
    rc, mb = git(root, "merge-base", "HEAD", base)
    if rc != 0 or not mb:
        return None
    rc, out = git(root, "diff", "--name-only", f"{mb}..HEAD")
    return None if rc != 0 else [p for p in out.splitlines() if p]


def pr_body_from_event() -> tuple[bool, str]:
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return False, ""
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    except (KeyError, OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read pull-request event: {exc}") from exc
    if not isinstance(event, dict) or not isinstance(event.get("pull_request"), dict):
        raise GateError("pull-request event has no pull_request object")
    body = event["pull_request"].get("body")
    if body is not None and not isinstance(body, str):
        raise GateError("pull-request event body must be a string or null")
    return True, body or ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--base", default=os.environ.get("CARR_EVAL_RECEIPT_BASE", "origin/main"))
    ap.add_argument("--pr-body-file", help="enforce as a pull request, reading the body from this file")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    try:
        reg = load_registry(root / "evals" / "surfaces.json")
    except GateError as exc:
        print(f"check-eval-receipt: {exc}", file=sys.stderr)
        return 2
    known = {s["id"] for s in reg["surfaces"]}
    try:
        if args.pr_body_file:
            in_pr, body = True, Path(args.pr_body_file).read_text()
        else:
            in_pr, body = pr_body_from_event()
    except (GateError, OSError) as exc:
        print(f"check-eval-receipt: {exc}", file=sys.stderr)
        return 2

    failures: list[str] = []
    advisories: list[str] = []
    for hook in unregistered_context_hooks(root, reg):
        failures.append(f"{hook} emits context into a session but no surface in evals/surfaces.json names it")

    changed = changed_paths(root, args.base)
    if changed is None:
        msg = f"cannot compute the change set against {args.base}"
        if in_pr:
            print(f"check-eval-receipt: {msg}", file=sys.stderr)
            return 2
        print(f"check-eval-receipt: {msg}; nothing to judge here")
        changed = []

    touched: dict[str, list[str]] = {}
    for path in changed:
        for sid in surfaces_for(path, reg):
            touched.setdefault(sid, []).append(path)
    receipts_changed = {p.split("/")[1] for p in changed
                        if re.fullmatch(r"evals/[^/]+/receipt\.json", p)}
    no_eval, line_errs = parse_no_eval(body, known) if in_pr else ({}, [])
    failures += line_errs

    for sid in sorted(receipts_changed):
        rel = f"evals/{sid}/receipt.json"
        if sid not in known:
            failures.append(f"{rel}: {sid} is not a registered surface")
            continue
        try:
            receipt = json.loads((root / rel).read_text())
        except FileNotFoundError:
            continue  # a deleted receipt is not a receipt
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{rel}: unreadable: {exc}")
            continue
        failures += [f"{rel}: {e}" for e in validate_receipt(receipt, sid, root)]
        verdict = receipt.get("verdict") if isinstance(receipt, dict) else None
        decision = verdict.get("decision") if isinstance(verdict, dict) else None
        if sid in touched and decision in ("do_not_merge", "inconclusive"):
            failures.append(f"{rel}: verdict {decision} does not authorize shipping the changed {sid} surface")

    for sid, paths in sorted(touched.items()):
        shown = ", ".join(paths[:4]) + (f" (+{len(paths) - 4} more)" if len(paths) > 4 else "")
        if sid in receipts_changed and (root / f"evals/{sid}/receipt.json").exists():
            print(f"  receipt   {sid}: {shown}")
        elif sid in no_eval:
            print(f"  no-eval   {sid}: {no_eval[sid]}")
        else:
            need = (f"{sid} changed ({shown}) with no evals/{sid}/receipt.json in this change and no "
                    f"'no-eval: {sid}: <reason>' line in the pull-request body. Run /claude-api build-eval "
                    f"then /claude-api hillclimb (evals/README.md).")
            (failures if in_pr else advisories).append(need)

    for a in advisories:
        print(f"  advisory  {a} (not a pull-request run; CI enforces this on the pull request)")
    if failures:
        for f in failures:
            print(f"  FAIL      {f}", file=sys.stderr)
        return 1
    print(f"check-eval-receipt: OK ({len(touched)} surface(s) touched, {len(receipts_changed)} receipt(s) checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
