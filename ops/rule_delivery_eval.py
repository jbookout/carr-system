"""rule_delivery_eval.py — score every rule-delivery path against gold labels.

WHY THIS EXISTS (Joe, 2026-09-26: "I'm not convinced that our rule system is
effective enough in its current state"). Rules reach a session by several
independent paths, and until this file none of them had a labelled precision
or recall: the drift observer logs "needed but not loaded" from a keyword
classifier, the Jev selectors log what they surfaced, and the nightly replay
is deterministic with its judge stubbed. Each log measures a path against
itself. This measures every path against the same hand-labelled answer key.

WHAT A CASE IS. A situation — the partner's prompt (or a machine
notification), plus, when there is one, the tool calls the session made in
that turn — and the gold set of rule ids whose condition actually binds
there. `disputed` ids are excluded from both sides of the count.

REPLAY, NOT RE-IMPLEMENTATION (Jev, architecture_or_design, 2026-09-26: 1.00
for replaying real code over re-implementing it or scoring old logs). Each
adapter calls the production selection function itself:

  prompt_compiled   ops/rule_trigger_delivery.advise with the Jev judgment
                    disabled (compiled prompt_regex rows + always-on only),
                    then the hook's own pack-layer filter
                    (lib/rule_delivery_preuse.semantic_delivery);
  prompt_full       the same call with the budgeted Jev judgment live — what
                    the UserPromptSubmit hook actually delivers;
  jev_rule_select   ops/jev_rule_select.select at its floor (the legacy
                    two-stage selector; still the Flash path);
  jit_pretooluse    hooks/rule-pack-preuse-reselection.py's own
                    matched_triggers()/_matches() on each tool call;
  layered_triggers  the same hook's route rail: routed_rule_ids() over
                    ops/config/rule-routes.v1.json, unioned with the compiled
                    table rows it carries in the same door call, on each tool
                    call (exact matches; no dedupe state is read). Charged for
                    gold of every layer, since routes cover every layer. The
                    boot layer is scored beside it in a follow-up;
  drift_shadow      hooks/rule-pack-drift-gate.py as shipped: mode shadow
                    loads nothing;
  drift_if_acting   the same gate's evaluate(): the packs it says the turn
                    needed, delivered as those packs' pack-layer rules;
  boot_layer0       the layer-zero rules standing-context loads at boot.

DRY RUN. Nothing here writes a production log, cache or audit file: the
selector log goes to os.devnull, no session id is passed (so no dedupe or
verdict cache is read or written), Jev's per-call receipts go to a sink the
caller names, and jev_judge.record() is replaced by a no-op on the harness's
private copy of the module (its outage row would otherwise land in
out/jev-judge.jsonl). The selftest snapshots GUARDED_OUT_FILES before and
after a run and fails on any change. The standing-context door the hooks
call afterwards only fetches rule TEXT for ids already chosen, so it is not
replayed.

RESPONSIBILITY UNIVERSES. A path is charged only for gold rules it is
responsible for: the prompt and JIT paths for pack-layer rules (layer zero is
already loaded at boot; control-layer rules are delivered by the gate that
enforces them), the legacy selector and the system rows for everything. A
delivered rule that is not gold is a false positive wherever it came from.

METRICS (Jev, verification_selection, 2026-09-26): the headline is SYSTEM
recall — the union of what actually reaches a session (0.84 over per-path
micro numbers); per-stratum recall (0.88); notification turns scored apart
from the pooled human numbers (0.85); pack-level scoring for the
pack-granular drift observer (0.86).

A LIBRARY. The command line is tools/rule-delivery-eval.py; this file carries
no entrypoint construct, for the sealed-inventory reason
ops/typesafe_client.py documents.
"""

import concurrent.futures as cf
import importlib.util
import json
import os
import re
from collections import Counter

CASES_SCHEMA = "rule-delivery-eval-cases/v1"
REPORT_SCHEMA = "rule-delivery-eval-report/v1"
STRATA = ("engineering", "deals_clients", "comms", "scheduling", "notifications")
MACHINE_STRATA = ("notifications",)
EVAL_SESSION = "rule-delivery-eval"

# Files a dry run must never create or modify, relative to <repo>/out.
GUARDED_OUT_FILES = (
    "rule-trigger-delivery.jsonl", "rule-prompt-delivered.json",
    "jev-rule-select.jsonl", "jev-rule-select-cache.json", "jev-judge.jsonl",
    "rule-delivery-shadow.jsonl", "jev-calls.jsonl",
)

PACK_LAYER = "pack"


# ------------------------------------------------------------------ loading

def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cases(path):
    """Validated cases from a cases file (schema CASES_SCHEMA) or a JSONL file
    of case objects. Raises ValueError on anything malformed."""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if str(path).endswith(".jsonl"):
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        doc = json.loads(text)
        if doc.get("schema") != CASES_SCHEMA:
            raise ValueError(f"{path}: schema is not {CASES_SCHEMA}")
        rows = doc.get("cases")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path}: no cases")
    seen = set()
    cases = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError(f"{path}: case without an id")
        if row["id"] in seen:
            raise ValueError(f"{path}: duplicate case id {row['id']}")
        seen.add(row["id"])
        if row.get("stratum") not in STRATA:
            raise ValueError(f"{row['id']}: unknown stratum {row.get('stratum')!r}")
        if not isinstance(row.get("prompt"), str):
            raise ValueError(f"{row['id']}: prompt must be a string")
        gold = row.get("gold")
        if not isinstance(gold, list) or not all(isinstance(g, str) for g in gold):
            raise ValueError(f"{row['id']}: gold must be a list of rule ids")
        calls = row.get("tool_calls") or []
        if not isinstance(calls, list) or not all(
                isinstance(c, dict) and isinstance(c.get("tool_name"), str) for c in calls):
            raise ValueError(f"{row['id']}: tool_calls must be [{{tool_name, tool_input}}]")
        cases.append({"id": row["id"], "stratum": row["stratum"], "prompt": row["prompt"],
                      "tool_calls": calls, "gold": sorted(set(gold)),
                      "disputed": sorted(set(row.get("disputed") or []))})
    return cases


def rule_meta(repo, corpus=None):
    """{rule id: {"layer", "packs"}} from the reviewed map. With `corpus` (a
    list of live rule ids), rules the map does not tag are added as
    "untagged" so a gold label on them is still counted."""
    path = os.path.join(repo, "ops", "config", "rule-enforcement-map.json")
    with open(path, "r", encoding="utf-8") as handle:
        layers = json.load(handle).get("rule_load_layers") or {}
    meta = {rid: {"layer": row.get("load_layer") or "untagged",
                  "packs": sorted(row.get("packs") or [])}
            for rid, row in layers.items() if isinstance(row, dict)}
    for rid in corpus or ():
        meta.setdefault(rid, {"layer": "untagged", "packs": []})
    return meta


def universes(meta, names):
    pack = {rid for rid, row in meta.items() if row["layer"] == PACK_LAYER}
    everything = set(meta)
    layer0 = {rid for rid, row in meta.items() if row["layer"] == "layer0"}
    table = {"prompt_compiled": pack, "prompt_full": pack, "jit_pretooluse": pack,
             "drift_shadow": pack, "drift_if_acting": pack, "boot_layer0": layer0,
             "layered_triggers": everything,
             "jev_rule_select": everything, "system_moment": everything,
             "system_moment_packlayer": pack,
             "system_moment_plus_drift": everything, "system_scoped_boot": everything}
    return {name: table.get(name, everything) for name in names}


# ------------------------------------------------------------------ dry-run Jev

class JevProxy:
    """The typesafe client with every request's call receipt sent to a sink.

    Question builders are the real ones; only ask() is wrapped, so the
    production selector builds exactly the questions it builds live."""

    def __init__(self, tsc, calls_log):
        self._tsc = tsc
        self.calls_log = calls_log
        self.noul, self.choice, self.score = tsc.noul, tsc.choice, tsc.score

    def ask(self, state, questions, **kwargs):
        kwargs["calls_log"] = self.calls_log
        return self._tsc.ask(state, questions, **kwargs)


def _quiet_judge(repo, tag):
    """A private jev_judge whose record() writes nothing."""
    module = _load(os.path.join(repo, "ops", "jev_judge.py"), f"jev_judge_quiet_{tag}")
    module.record = lambda *args, **kwargs: {}
    return module


def _quiet_rule_trigger_delivery(repo, tag):
    """A private ops/rule_trigger_delivery whose Jev siblings cannot log."""
    rtd = _load(os.path.join(repo, "ops", "rule_trigger_delivery.py"), f"rtd_eval_{tag}")
    original = rtd._sibling

    def sibling(name):
        module = original(name)
        if name == "jev_rule_select":
            inner = module._sibling
            module._sibling = (lambda n: _quiet_judge(repo, tag) if n == "jev_judge"
                               else inner(n))
        return module
    rtd._sibling = sibling
    return rtd


# ------------------------------------------------------------------ adapters

def _synthetic_turn(case):
    """Transcript-shaped records for one case: the prompt, then the calls."""
    user = {"type": "user", "message": {"role": "user", "content": case["prompt"]}}
    if case["prompt"].lstrip().startswith("<task-notification>"):
        user["origin"] = {"kind": "task-notification"}
    records = [user]
    if case["tool_calls"]:
        records.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"eval-{i}", "name": call["tool_name"],
             "input": call.get("tool_input")}
            for i, call in enumerate(case["tool_calls"])]}})
    return records


def _raise(*_args, **_kwargs):
    raise RuntimeError("Jev judgment disabled for this adapter")


def build_adapters(repo, *, jev="off", client_factory=None, calls_log=os.devnull):
    """Adapters for every path. `jev` is "off" (deterministic paths only) or
    "live" (adds prompt_full and jev_rule_select). `client_factory(calls_log)`
    returns the Jev client; default is JevProxy over ops/typesafe_client."""
    import sys
    repo = str(repo)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from lib import rule_delivery_preuse as preuse
    from pathlib import Path
    repo_path = Path(repo)
    meta = rule_meta(repo)
    layer0 = {rid for rid, row in meta.items() if row["layer"] == "layer0"}
    pack_layer = {rid for rid, row in meta.items() if row["layer"] == PACK_LAYER}

    def prompt_delivery(rtd, text, **kwargs):
        rows = rtd.advise(text, session_id=None, log_path=os.devnull, **kwargs)
        ids, packs = preuse.semantic_delivery(repo_path, [row["id"] for row in rows])
        return {"rules": set(ids), "packs": set(packs)}

    rtd_off = _quiet_rule_trigger_delivery(repo, "off")

    def prompt_compiled(case):
        return prompt_delivery(rtd_off, case["prompt"], rank=_raise, ask=_raise)

    hook = _load(os.path.join(repo, "hooks", "rule-pack-preuse-reselection.py"),
                 "preuse_hook_eval")

    def jit(case):
        rules = set()
        for index, call in enumerate(case["tool_calls"]):
            payload = {"hook_event_name": "PreToolUse", "tool_name": call["tool_name"],
                       "tool_input": call.get("tool_input"), "session_id": EVAL_SESSION,
                       "tool_use_id": f"eval-{case['id']}-{index}"}
            if hook._matches(payload):
                rules.update(hook.scheduled_rule_ids())
                continue
            rows = hook.matched_triggers(payload)
            if rows:
                rules.update(preuse.merge_trigger_delivery(rows)[2])
        return {"rules": rules, "packs": {p for r in rules for p in meta.get(r, {}).get("packs", [])}}

    def layered(case):
        rules = set()
        for index, call in enumerate(case["tool_calls"]):
            payload = {"hook_event_name": "PreToolUse", "tool_name": call["tool_name"],
                       "tool_input": call.get("tool_input"), "session_id": EVAL_SESSION,
                       "tool_use_id": f"eval-{case['id']}-{index}"}
            if hook._matches(payload):
                # The scheduled rail still owns a background Bash call.
                rules.update(hook.scheduled_rule_ids())
                continue
            routed = hook.routed_rule_ids(payload)
            rows = hook.matched_triggers(payload)
            table = preuse.merge_trigger_delivery(rows)[2] if rows else []
            rules.update(routed)
            rules.update(table)
        return {"rules": rules, "packs": {p for r in rules for p in meta.get(r, {}).get("packs", [])}}

    drift = _load(os.path.join(repo, "hooks", "rule-pack-drift-gate.py"), "drift_gate_eval")
    triggers, members, _digest = drift.load_packs()

    def drift_needed(case):
        return drift.evaluate(_synthetic_turn(case), triggers, members)["needed"]

    def drift_shadow(case):
        drift_needed(case)  # the gate still runs; in shadow it loads nothing
        return {"rules": set(), "packs": set()}

    def drift_acting(case):
        needed = set(drift_needed(case))
        rules = {rid for pack in needed for rid in members.get(pack, []) if rid in pack_layer}
        return {"rules": rules, "packs": needed}

    def boot(case):
        return {"rules": set(layer0), "packs": set()}

    adapters = [
        {"name": "prompt_compiled", "select": prompt_compiled, "jev": False},
        {"name": "jit_pretooluse", "select": jit, "jev": False,
         "applies": lambda case: bool(case["tool_calls"])},
        {"name": "layered_triggers", "select": layered, "jev": False,
         "applies": lambda case: bool(case["tool_calls"])},
        {"name": "drift_shadow", "select": drift_shadow, "jev": False},
        {"name": "drift_if_acting", "select": drift_acting, "jev": False},
        {"name": "boot_layer0", "select": boot, "jev": False},
    ]
    if jev == "live":
        if client_factory is None:
            tsc = _load(os.path.join(repo, "ops", "typesafe_client.py"), "tsc_eval")
            client_factory = lambda sink: JevProxy(tsc, sink)  # noqa: E731
        client = client_factory(calls_log)
        rtd_live = _quiet_rule_trigger_delivery(repo, "live")
        jrs = _load(os.path.join(repo, "ops", "jev_rule_select.py"), "jrs_eval")
        quiet = _quiet_judge(repo, "jrs")

        def prompt_full(case):
            return prompt_delivery(rtd_live, case["prompt"], client=client)

        def legacy(case):
            rows = jrs.select(case["prompt"], client=client, judge=quiet,
                              cache_path=None, session_id=None)
            rules = {row["id"] for row in rows if row.get("probability") is not None}
            return {"rules": rules,
                    "packs": {p for r in rules for p in meta.get(r, {}).get("packs", [])}}

        adapters += [{"name": "prompt_full", "select": prompt_full, "jev": True},
                     {"name": "jev_rule_select", "select": legacy, "jev": True}]
    elif jev != "off":
        raise ValueError("jev must be 'off' or 'live'")
    return adapters


def run_adapters(cases, adapters, *, workers=1):
    """({path: {case id: {"rules": set, "packs": set|None}}}, {path: {case id: error}}).

    A raising adapter is recorded and scored as delivering nothing — which is
    what the production hooks do when their selector fails."""
    deliveries, errors = {}, {}
    for adapter in adapters:
        applies = adapter.get("applies") or (lambda case: True)
        todo = [case for case in cases if applies(case)]
        out, errs = {}, {}

        def one(case, adapter=adapter):
            try:
                result = adapter["select"](case)
                return case["id"], {"rules": set(result.get("rules") or ()),
                                    "packs": (set(result["packs"]) if result.get("packs")
                                              is not None else None)}, None
            except Exception as exc:  # noqa: BLE001 - recorded, scored as empty
                return case["id"], {"rules": set(), "packs": set()}, type(exc).__name__
        if workers > 1 and adapter.get("jev"):
            with cf.ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(one, todo))
        else:
            results = [one(case) for case in todo]
        for case_id, result, error in results:
            out[case_id] = result
            if error:
                errs[case_id] = error
        deliveries[adapter["name"]] = out
        errors[adapter["name"]] = errs
    return deliveries, errors


SYSTEM_ROWS = ("system_moment", "system_moment_packlayer", "system_moment_plus_drift",
               "system_scoped_boot")


def add_system_rows(deliveries):
    """The system rows, built from the per-path deliveries.

    WHY "MOMENT" AND NOT "EVERYTHING IN CONTEXT". While ops.rule_delivery_policy
    is 'shadow', standing-context recites EVERY active rule at boot as a gist
    line (mcp-server/src/doctrine.js, the `enforcing` branch), so availability
    is total by construction and says nothing. What varies is whether the
    binding rule's full text is put in front of the session at the moment it
    binds, which is what the prompt and JIT paths do. So:

      system_moment            prompt path (the Jev judgment when run, else
                               the compiled rows) plus JIT, charged for gold
                               of every layer;
      system_moment_packlayer  the same deliveries, charged only for
                               pack-layer gold (the layer those paths serve);
      system_moment_plus_drift the same plus the drift observer acting;
      system_scoped_boot       the moment paths plus layer zero: what a session
                               would hold if the scoped boot were enforced and
                               it declared no pack.
    """
    prompt = deliveries.get("prompt_full") or deliveries.get("prompt_compiled") or {}
    jit = deliveries.get("jit_pretooluse") or {}
    boot = deliveries.get("boot_layer0") or {}
    drift = deliveries.get("drift_if_acting") or {}
    rows = {name: {} for name in SYSTEM_ROWS}
    for case_id in set(prompt) | set(boot) | set(drift):
        moment = set(prompt.get(case_id, {}).get("rules") or ())
        moment |= jit.get(case_id, {}).get("rules") or set()
        rows["system_moment"][case_id] = {"rules": moment, "packs": None}
        rows["system_moment_packlayer"][case_id] = {"rules": moment, "packs": None}
        rows["system_moment_plus_drift"][case_id] = {
            "rules": moment | (drift.get(case_id, {}).get("rules") or set()), "packs": None}
        rows["system_scoped_boot"][case_id] = {
            "rules": moment | (boot.get(case_id, {}).get("rules") or set()), "packs": None}
    deliveries.update(rows)
    return deliveries


# ------------------------------------------------------------------ scoring

def confusion(gold, delivered):
    """(true positives, false positives, misses) as sorted lists."""
    return (sorted(gold & delivered), sorted(delivered - gold), sorted(gold - delivered))


def prf(tp, fp, fn, tp_recall=None):
    """Precision over everything delivered; recall over the path's universe.

    `tp_recall` is the true positives INSIDE the universe. A path may deliver
    a correct rule outside its universe (a JIT row that emits a control-layer
    rule, say): precision credits it, but it must not lift recall above what
    the universe's own gold allows. Defaults to `tp`."""
    tp_recall = tp if tp_recall is None else tp_recall
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp_recall / (tp_recall + fn) if tp_recall + fn else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else (0.0 if precision is not None and recall is not None
                                        else None))
    return {"tp": tp, "tp_in_universe": tp_recall, "fp": fp, "fn": fn,
            "precision": None if precision is None else round(precision, 4),
            "recall": None if recall is None else round(recall, 4),
            "f1": None if f1 is None else round(f1, 4)}


def deliveries_from_report(report):
    """Rebuild {path: {case id: {"rules", "packs"}}} from a saved report, so a
    run's deliveries can be re-scored against other labels (a consensus gold
    set, say) without asking Jev again. System rows are rebuilt by the caller."""
    out = {}
    for case_id, paths in (report.get("per_case") or {}).items():
        for name, row in paths.items():
            if name in SYSTEM_ROWS:
                continue
            packs = row.get("packs")
            out.setdefault(name, {})[case_id] = {"rules": set(row.get("raw_delivered")
                                                              or row.get("delivered") or ()),
                                                 "packs": set(packs) if packs is not None
                                                 else None}
    return out


def score(cases, deliveries, path_universes, meta, labelled=None):
    """The report. See the module docstring for what is counted where.

    `labelled`, when given, is the set of rule ids the gold labellers could
    choose from. A delivered id outside it cannot be judged right or wrong, so
    it is set aside (counted per path as `outside_labelled`) rather than
    charged as a false positive."""
    by_id = {case["id"]: case for case in cases}
    report = {"schema": REPORT_SCHEMA, "cases": len(cases),
              "strata": dict(Counter(case["stratum"] for case in cases)),
              "paths": {}, "per_case": {case["id"]: {} for case in cases}}
    for name, per_case in deliveries.items():
        outside = Counter()
        universe = path_universes.get(name, set(meta))
        human = [0, 0, 0, 0]  # tp, fp, fn, tp inside the universe
        strata = {}
        packs = [0, 0, 0]
        packs_scored = False
        notes = {"cases": 0, "cases_with_delivery": 0, "fp": 0, "tp": 0, "fn": 0,
                 "delivered": 0}
        misses, false_pos = Counter(), Counter()
        for case_id, out in per_case.items():
            case = by_id.get(case_id)
            if case is None:
                continue
            disputed = set(case.get("disputed") or ())
            gold_all = set(case["gold"]) - disputed
            gold = gold_all & universe
            raw = set(out["rules"])
            delivered = raw - disputed
            if labelled is not None:
                outside.update(delivered - labelled)
                delivered &= labelled
            tp, fp, fn = confusion(gold_all, delivered)
            fn = [rid for rid in fn if rid in universe]
            tp_u = [rid for rid in tp if rid in universe]
            report["per_case"][case_id][name] = {"delivered": sorted(delivered),
                                                 "raw_delivered": sorted(raw),
                                                 "packs": (sorted(out["packs"])
                                                           if out.get("packs") is not None
                                                           else None),
                                                 "tp": tp, "fp": fp, "fn": fn}
            misses.update(fn)
            false_pos.update(fp)
            if case["stratum"] in MACHINE_STRATA:
                notes["cases"] += 1
                notes["cases_with_delivery"] += 1 if delivered else 0
                notes["fp"] += len(fp)
                notes["tp"] += len(tp)
                notes["fn"] += len(fn)
                notes["delivered"] += len(delivered)
            else:
                human[0] += len(tp)
                human[1] += len(fp)
                human[2] += len(fn)
                human[3] += len(tp_u)
                row = strata.setdefault(case["stratum"], [0, 0, 0, 0, 0])
                row[0] += len(tp)
                row[1] += len(fp)
                row[2] += len(fn)
                row[3] += len(tp_u)
                row[4] += 1
            if out.get("packs") is not None:
                packs_scored = True
                gold_packs = {p for rid in gold if meta.get(rid, {}).get("layer") == PACK_LAYER
                              for p in meta[rid]["packs"]}
                ptp, pfp, pfn = confusion(gold_packs, set(out["packs"]))
                packs[0] += len(ptp)
                packs[1] += len(pfp)
                packs[2] += len(pfn)
        n = notes["cases"]
        report["paths"][name] = {
            "universe_size": len(universe),
            "cases_scored": len(per_case),
            "human": prf(*human),
            "by_stratum": {st: {**prf(*row[:4]), "cases": row[4]}
                           for st, row in sorted(strata.items())},
            "notifications": {**notes,
                              "delivered_mean": round(notes["delivered"] / n, 3) if n else None},
            "packs": prf(*packs) if packs_scored else None,
            "outside_labelled": sorted(outside.items(), key=lambda kv: (-kv[1], kv[0])),
            "misses": sorted(misses.items(), key=lambda kv: (-kv[1], kv[0])),
            "false_positives": sorted(false_pos.items(), key=lambda kv: (-kv[1], kv[0])),
        }
    return report


def _dig(report_path, dotted):
    value = report_path
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return KeyError
        value = value[part]
    return value


def check_expectations(report, expectations, tolerance=1e-6):
    """Mismatch lines for {path: {"dotted.metric": expected}}. Empty is a pass."""
    problems = []
    for path, wanted in expectations.items():
        got_path = report["paths"].get(path)
        if got_path is None:
            problems.append(f"{path}: not in report")
            continue
        for dotted, expected in wanted.items():
            got = _dig(got_path, dotted)
            same = (got == expected if not isinstance(expected, float) or got is None
                    or got is KeyError
                    else abs(float(got) - expected) <= tolerance)
            if got is KeyError or not same:
                problems.append(f"{path}: {dotted} expected {expected!r}, got "
                                f"{'missing' if got is KeyError else repr(got)}")
    return problems


# ------------------------------------------------------------------ rendering

def one_line(statement, limit=110):
    text = " ".join((statement or "").split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def _pct(value):
    return "–" if value is None else f"{100 * value:.0f}%"


def render_markdown(report, statements=None, *, top=10, paths=None):
    statements = statements or {}
    order = paths or [p for p in SYSTEM_ROWS + ("prompt_full",
                                  "prompt_compiled", "jev_rule_select", "jit_pretooluse",
                                  "layered_triggers",
                                  "drift_shadow", "drift_if_acting", "boot_layer0")
                      if p in report["paths"]]
    lines = [f"Cases: {report['cases']} ({', '.join(f'{k} {v}' for k, v in sorted(report['strata'].items()))})",
             "",
             "| path | cases | human P | human R | human F1 | notif. cases w/ delivery | notif. FP | pack P | pack R |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name in order:
        row = report["paths"][name]
        h, n, pk = row["human"], row["notifications"], row["packs"] or {}
        lines.append(f"| {name} | {row['cases_scored']} | {_pct(h['precision'])} | "
                     f"{_pct(h['recall'])} | {_pct(h['f1'])} | "
                     f"{n['cases_with_delivery']}/{n['cases']} | {n['fp']} | "
                     f"{_pct(pk.get('precision'))} | {_pct(pk.get('recall'))} |")
    strata = [s for s in STRATA if s not in MACHINE_STRATA]
    lines += ["", "Recall by stratum:", "",
              "| path | " + " | ".join(strata) + " |", "|---|" + "---|" * len(strata)]
    for name in order:
        by = report["paths"][name]["by_stratum"]
        lines.append(f"| {name} | " + " | ".join(_pct((by.get(s) or {}).get("recall"))
                                                 for s in strata) + " |")
    for name in [p for p in ("system_moment", "prompt_full") if p in report["paths"]]:
        row = report["paths"][name]
        lines += ["", f"Top misses — {name}:", "", "| rule | cases | summary |", "|---|---|---|"]
        lines += [f"| {rid} | {n} | {one_line(statements.get(rid))} |"
                  for rid, n in row["misses"][:top]]
        lines += ["", f"Top false positives — {name}:", "", "| rule | cases | summary |",
                  "|---|---|---|"]
        lines += [f"| {rid} | {n} | {one_line(statements.get(rid))} |"
                  for rid, n in row["false_positives"][:top]]
    return "\n".join(lines) + "\n"
