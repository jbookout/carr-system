#!/usr/bin/env python3
"""rule-gold-label-selftest.py -- acceptance test for the v2 rule-delivery
benchmark: the labeller (ops/rule_gold_label.py, CLI tools/rule-gold-label.py),
the committed fixture (ops/fixtures/rule-delivery-eval/cases.v2.json with its
labels and adjudications), the regression intake
(tools/rule-delivery-eval-intake.py), and the harness's v2 additions (split
filtering, per-rule-class and fully-served scoring).

Offline: no Jev request, no record-layer call, no write outside a throwaway
directory.

WHAT IS PROVEN:
  1. The bands: p >= YES_AT gold, p <= NO_AT not, between is borderline, and a
     borderline pair with no written adjudication stops the build rather than
     silently becoming "not gold".
  2. The committed gold is REPRODUCIBLE: rebuilding it from the committed
     first-pass probabilities and adjudications gives exactly the fixture's
     gold, for every case.
  3. Every adjudication carries a written reason, and every borderline pair in
     the committed probabilities has exactly one.
  4. The split is fixed by seed: recomputing it reproduces every case's split;
     each stratum holds out ceil(30%); a later case is placed by hash without
     moving any existing case.
  5. The fixture is big enough and clean: at least 200 cases, all eight strata
     with at least 20 each, every gold id a live rule the probabilities cover,
     and no email, phone, money figure, hostname URL, IP, credential path or
     token anywhere in it.
  6. Rule classes follow the ordered questions: every live rule gets exactly one
     of the four, layer0 is always_on, control is gate_named.
  7. The harness scores v2: load_cases filters by split, per-class counts and
     the fully-served share match hand counts, and the CLI runs on the train
     split and writes the class table.
  8. The regression intake: a live miss becomes a case with the missed rule
     gold, borderline rules disputed, a hash-placed split; a copied prompt, a
     copied tool-call input, a record name (never echoed), a bare host, a
     machine name, a key or ssh path, a scrub hit, an unknown rule or a
     duplicate is refused; every refusal happens BEFORE any Jev request (the
     CLI's main() is run with a stub client that counts requests); the name
     check fails closed; the command line appends to a fixture copy.
  9. The universal-trigger policy: the rules UNIVERSAL_POLICY settles carry
     exactly the policy's label on every case.
 10. Doctrine scoring sets aside refs outside a case's labelled shortlist and
     does not score paths that deliver no doctrine; the harness refuses a
     slug-shaped doctrine ref; with no --split a v2 file is scored on test.
"""
import copy
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "ops" / "rule_gold_label.py"
EVAL = REPO / "ops" / "rule_delivery_eval.py"
FIX_DIR = REPO / "ops" / "fixtures" / "rule-delivery-eval"
FIXTURE = FIX_DIR / "cases.v2.json"
LABELS = FIX_DIR / "labels.v2.json"
ADJ = FIX_DIR / "adjudications.v2.jsonl"
DADJ = FIX_DIR / "doctrine-adjudications.v2.jsonl"
EVAL_CLI = REPO / "tools" / "rule-delivery-eval.py"
INTAKE_CLI = REPO / "tools" / "rule-delivery-eval-intake.py"

sys.path.append(str(REPO / "lib"))
from selftest_harness import Checker  # noqa: E402

CHECKER = Checker()
check = CHECKER.check


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probs_from_labels(labels):
    """labels.v2.json stores one list per case in `rules` order (compact)."""
    rules = labels["rules"]
    return {cid: {rid: row[i] for i, rid in enumerate(rules) if row[i] is not None}
            for cid, row in labels["cases"].items()}


def read_adjudications():
    return [json.loads(line) for line in ADJ.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_bands(gl):
    check("band: at YES_AT is gold", gl.band(gl.YES_AT) == "gold")
    check("band: at NO_AT is not", gl.band(gl.NO_AT) == "not")
    check("band: between is borderline", gl.band((gl.YES_AT + gl.NO_AT) / 2) == "borderline")
    probs = {"c1": {"r1": 0.9, "r2": 0.5, "r3": 0.1}}
    try:
        gl.gold_sets(probs, [])
        check("unadjudicated borderline stops the build", False)
    except ValueError:
        check("unadjudicated borderline stops the build", True)
    got = gl.gold_sets(probs, [{"case": "c1", "rule": "r2", "gold": True, "reason": "x" * 20}])
    check("adjudication decides the borderline pair", got == {"c1": ["r1", "r2"]}, got)
    got = gl.gold_sets(probs, [{"case": "c1", "rule": "r2", "gold": False, "reason": "x" * 20}])
    check("an adjudicated no stays out", got == {"c1": ["r1"]}, got)


def test_fixture(gl, ev):
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = doc["cases"]
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    probs = probs_from_labels(labels)
    adjud = read_adjudications()
    check("fixture has at least 200 cases", len(cases) >= 200, len(cases))
    counts = {}
    for case in cases:
        counts[case["stratum"]] = counts.get(case["stratum"], 0) + 1
    check("all eight v2 strata present", set(counts) == set(ev.STRATA_V2), counts)
    check("every stratum has at least 20 cases", min(counts.values()) >= 20, counts)
    live = set(labels["rules"])
    check("labels cover all live rules (195 on the labelling date)", len(live) >= 190, len(live))
    base = [c for c in cases if c.get("origin") != "live-miss"]
    check("every base case has first-pass probabilities for every live rule",
          all(len(probs.get(c["id"], {})) == len(live) for c in base))
    unknown = sorted({rid for c in cases for rid in c["gold"] if rid not in live})
    check("every gold id is a labelled live rule", not unknown, unknown)
    # 2. reproducible gold
    rebuilt = gl.gold_sets({c["id"]: probs[c["id"]] for c in base}, adjud)
    diffs = [c["id"] for c in base if rebuilt[c["id"]] != c["gold"]]
    check("gold rebuilds exactly from probabilities + adjudications", not diffs, diffs[:5])
    # 3. adjudications
    keys = [(a["case"], a["rule"]) for a in adjud]
    check("no pair adjudicated twice", len(keys) == len(set(keys)))
    border = {(c, r) for c, r, _p in gl.borderlines({c["id"]: probs[c["id"]] for c in base})}
    check("every borderline pair is adjudicated, and only those",
          border == set(keys), (len(border), len(set(keys))))
    short = [k for k, a in zip(keys, adjud) if len((a.get("reason") or "").split()) < 6]
    check("every adjudication has a written reason", not short, short[:5])
    # 4. split
    splits = gl.assign_splits(base, seed=doc["split"]["seed"])
    moved = [c["id"] for c in base if splits[c["id"]] != c["split"]]
    check("split recomputes from the seed", not moved, moved[:5])
    for stratum, n in counts.items():
        held = sum(1 for c in base if c["stratum"] == stratum and c["split"] == "test")
        want = math.ceil(gl.TEST_FRACTION * sum(1 for c in base if c["stratum"] == stratum))
        check(f"stratum {stratum} holds out ceil(30%)", held == want, (held, want))
    extra = [{"id": f"later-{i}", "stratum": "engineering"} for i in range(40)]
    before = gl.assign_splits(base, seed=doc["split"]["seed"])
    placed = {e["id"]: gl.split_for_new_case(e["id"], doc["split"]["seed"]) for e in extra}
    check("a later case never moves an existing one",
          before == gl.assign_splits(base, seed=doc["split"]["seed"]))
    check("later cases land in both splits by hash", set(placed.values()) == {"train", "test"})
    # 5. hygiene
    raw = FIXTURE.read_text(encoding="utf-8") + ADJ.read_text(encoding="utf-8")
    hits = gl.scrub_findings(raw)
    check("fixture and adjudications carry nothing the scrub refuses", not hits, hits[:5])
    check("fixture declares itself paraphrased", doc.get("provenance", "").startswith("paraphrased"))
    tool_ok = all(isinstance(c.get("tool_calls"), list) and all(
        isinstance(t, dict) and isinstance(t.get("tool_name"), str) for t in c["tool_calls"])
        for c in cases)
    check("tool calls use the v1 shape", tool_ok)
    loaded = ev.load_cases(str(FIXTURE))
    check("harness loads the v2 fixture", len(loaded) == len(cases))
    check("fixture documents both target sets",
          set((doc.get("targets") or {})) == {"gold", "gold_doctrine"})
    # doctrine: the second target rebuilds from its shortlist probabilities
    # and its own written adjudications, exactly as the rule gold does.
    dadj = [json.loads(line) for line in DADJ.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    dprobs = labels.get("doctrine") or {}
    drebuilt = gl.gold_sets({c["id"]: dprobs.get(c["id"], {}) for c in base}, dadj)
    ddiffs = [c["id"] for c in base if drebuilt[c["id"]] != c["gold_doctrine"]]
    check("doctrine gold rebuilds from shortlist probabilities + adjudications",
          not ddiffs, ddiffs[:5])
    check("doctrine gold is labelled (not empty across the set)",
          sum(len(c["gold_doctrine"]) for c in cases) > 0)
    # Slugs and section keys carry person and practice names; committed refs
    # must be the store's opaque ids.
    opaque = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}"
                        r"#[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    all_refs = ({r for c in cases for r in c["gold_doctrine"]}
                | {r for v in dprobs.values() for r in v} | {a["rule"] for a in dadj})
    bad_refs = sorted(r for r in all_refs if not opaque.match(r))
    check("every committed doctrine ref is an opaque '<doc id>#<section id>'",
          not bad_refs, bad_refs[:3])
    dborder = {(c, r) for c, r, _p in gl.borderlines({c["id"]: dprobs.get(c["id"], {})
                                                        for c in base})}
    check("every borderline doctrine pair is adjudicated, and only those",
          dborder == {(a["case"], a["rule"]) for a in dadj})
    check("every doctrine adjudication has a written reason",
          all(len((a.get("reason") or "").split()) >= 6 for a in dadj))
    # the guidance-deferred reporting group
    group = set(((doc.get("rule_groups") or {}).get("guidance_deferred") or {}).get("rules") or [])
    manifest = {rid for rid, g in gl.rule_groups(str(REPO)).items() if g == "guidance_deferred"}
    check("fixture tags the guidance-deferred group from the manifest",
          group == manifest & live and len(group) >= 90, len(group))
    check("each case's deferred tag is its gold within the group",
          all(c["gold_guidance_deferred"] == [r for r in c["gold"] if r in group] for c in base))
    # 9. the universal-trigger policy is applied uniformly
    off_policy = [(c["id"], rid) for c in base for rid, want in gl.policy_labels(c).items()
                  if rid in live and (rid in c["gold"]) != want]
    check("universal-trigger rules carry exactly the policy label on every case",
          not off_policy, off_policy[:5])
    return doc, labels


def test_scrub(gl):
    # Assembled at run time so this file does not itself carry the shapes the
    # repository's own hygiene checks look for.
    at, dot = "@", "."
    for text, name in (("mail someone" + at + "example" + dot + "org now", "email"),
                       ("call " + "-".join(("555", "555", "0100")), "phone"),
                       ("asking " + "$" + "32/sf", "dollar"),
                       ("about 4,500 sf of space", "square_feet"),
                       ("open https://" + "internal" + dot + "host" + dot + "example/x", "url_host"),
                       ("source ~/" + dot + "config/app/key" + dot + "env", "credential_path")):
        check(f"scrub catches {name}", any(h[0] == name for h in gl.scrub_findings(text)),
              gl.scrub_findings(text))
    check("scrub passes example.com and awk positionals",
          not gl.scrub_findings("see https://example.com/a and awk '{print $1}'"))
    home = "~/"
    for text, name in (
            ("ssh into build-box" + dot + "local and restart", "bare_host"),
            ("the portal at app" + dot + "somecorp" + dot + "com is down", "bare_host"),
            ("the node on the " + "tail" + "a1b2" + dot + "ts" + dot + "net mesh", "bare_host"),
            ("run it on sams" + "-mac" + "-studio tonight", "machine_name"),
            ("copy " + home + dot + "ssh/" + "id_" + "ed25519 over", "credential_path"),
            ("the deploy key in keys/deploy" + dot + "pem", "credential_path"),
            ("append to authorized" + "_keys on the box", "credential_path")):
        check(f"scrub catches {name}: {text[:28]}",
              any(h[0] == name for h in gl.scrub_findings(text)), gl.scrub_findings(text))
    check("scrub passes file names and plain words",
          not gl.scrub_findings("edit report.json and cases.v2.json, then open the Mac Studio "
                                "notes and the key rule list"))
    # names come from the record; a hit is reported without echoing the name
    terms = gl.record_name_terms([{"name": "Quillfeather Family Dental", "kind": "practice"},
                                  {"name": "Ada Brightwater", "kind": "person"},
                                  {"name": "Suite expansion", "kind": "deal"}])
    check("record names: full names and distinctive tokens, generic words dropped",
          {"Quillfeather Family Dental", "Quillfeather", "Brightwater", "Ada Brightwater"}
          <= set(terms) and "Family" not in terms and "Dental" not in terms
          and "Suite" not in terms, terms)
    hits = gl.scrub_findings("draft a note to brightwater about the renewal", terms)
    check("a record name is caught case-insensitively and withheld in the finding",
          hits == [("name", "<withheld>")], hits)
    check("a name inside another word is not a hit",
          not gl.scrub_findings("the brightwaterline report", terms))
    check("shared-run counts consecutive words",
          gl.longest_shared_run("please merge the pull request now", "merge the pull request") == 4)


def test_classes(gl, labels):
    classes = gl.rule_classes(str(REPO), labels["rules"])
    check("every live rule gets one of the four classes",
          set(classes) == set(labels["rules"]) and set(classes.values()) <= set(gl.RULE_CLASSES),
          set(classes.values()))
    emap = json.loads((REPO / "ops" / "config" / "rule-enforcement-map.json").read_text())
    layers = emap["rule_load_layers"]
    wrong = [rid for rid, cls in classes.items()
             if (layers.get(rid, {}).get("load_layer") == "layer0") != (cls == "always_on")
             or (layers.get(rid, {}).get("load_layer") == "control") != (cls == "gate_named")]
    check("layer0 is always_on and control is gate_named, and only those", not wrong, wrong[:5])
    check("all four classes occur in the live corpus",
          set(classes.values()) == set(gl.RULE_CLASSES), sorted(set(classes.values())))


def _uuid(n):
    return f"{n:08x}-0000-4000-8000-{n:012x}"


REF_A, REF_B, REF_C, REF_Z, REF_UNLAB = (f"{_uuid(i)}#{_uuid(100 + i)}" for i in range(1, 6))

META = {"a": {"layer": "layer0", "packs": []}, "p": {"layer": "pack", "packs": ["x"]},
        "q": {"layer": "pack", "packs": ["x"]}}
CLASSES = {"a": "always_on", "p": "topic", "q": "action_point"}


def test_harness_v2(ev):
    cases = [
        {"id": "t1", "stratum": "chat_only", "prompt": "a", "tool_calls": [], "gold": ["a", "p"],
         "disputed": [], "split": "train"},
        {"id": "t2", "stratum": "tour_maps", "prompt": "b", "tool_calls": [], "gold": ["q"],
         "disputed": [], "split": "test"},
        {"id": "t3", "stratum": "notifications", "prompt": "<task-notification>", "tool_calls": [],
         "gold": [], "disputed": [], "split": "test"},
    ]
    cases[0]["gold_doctrine"] = [REF_A, REF_B]
    cases[2]["gold_doctrine"] = [REF_C]
    deliveries = {"sys": {"t1": {"rules": {"a", "p"}, "doctrine": {REF_A, REF_Z, REF_UNLAB}},
                          "t2": {"rules": {"p"}}, "t3": {"rules": {"p"}}},
                  "rules_only": {"t1": {"rules": {"a"}}, "t2": {"rules": set()},
                                 "t3": {"rules": set()}}}
    groups = {"a": "other", "p": "guidance_deferred", "q": "guidance_deferred"}
    # REF_Z was on t1's labelled shortlist (judged not gold); REF_UNLAB was not.
    report = ev.score(cases, deliveries, {"sys": set(META), "rules_only": set(META)}, META,
                      classes=CLASSES, groups=groups,
                      doctrine_labelled={"t1": {REF_A, REF_B, REF_Z}, "t3": {REF_C}})
    row = report["paths"]["sys"]
    # doctrine over all cases: tp REF_A; fp REF_Z; fn REF_B, REF_C; REF_UNLAB set aside.
    check("doctrine is scored apart from rules",
          row["doctrine"]["tp"] == 1 and row["doctrine"]["fp"] == 1
          and row["doctrine"]["fn"] == 2 and row["doctrine"]["recall"] == round(1 / 3, 4),
          row["doctrine"])
    check("a delivered doctrine ref outside the case's labelled shortlist is set aside",
          row["doctrine"]["outside_labelled"] == 1, row["doctrine"])
    check("a path that delivers no doctrine is not charged doctrine misses",
          report["paths"]["rules_only"]["doctrine"] is None, report["paths"]["rules_only"])
    check("the doctrine table says which paths do not deliver doctrine",
          "does not deliver doctrine" in ev.render_markdown(report, {}, paths=["sys", "rules_only"]))
    check("doctrine misses reach the notification stratum too",
          row["doctrine_by_stratum"]["notifications"]["fn"] == 1, row["doctrine_by_stratum"])
    # groups: other tp a; deferred tp p (t1), fp p (t2), fn q (t2).
    check("the guidance-deferred group is reported apart",
          row["by_group"]["guidance_deferred"] == {**row["by_group"]["guidance_deferred"],
                                                  "tp": 1, "fp": 1, "fn": 1}
          and row["by_group"]["other"]["recall"] == 1.0, row["by_group"])
    # human: t1 tp a,p; t2 fp p, fn q. By class: always_on tp1; topic tp1 fp1; action_point fn1.
    check("per-class counts match hand count",
          row["by_class"]["always_on"]["tp"] == 1 and row["by_class"]["topic"]["tp"] == 1
          and row["by_class"]["topic"]["fp"] == 1 and row["by_class"]["action_point"]["fn"] == 1
          and row["by_class"]["action_point"]["recall"] == 0.0, row["by_class"])
    check("fully-served share: 1 of 2 owing cases",
          row["cases_fully_served"] == {"cases_owing": 2, "fully_served": 1, "share": 0.5},
          row["cases_fully_served"])
    check("notifications get their own stratum row",
          row["by_stratum"]["notifications"]["cases"] == 1, row["by_stratum"])
    md = ev.render_markdown(report, {}, paths=["sys"])
    check("markdown carries the class, group and doctrine tables and v2 strata",
          "rule class" in md and "rule group" in md and "Doctrine" in md
          and "tour_maps" in md and "notifications" in md)
    with tempfile.TemporaryDirectory(prefix="rule-gold-label-selftest-") as tmp:
        path = Path(tmp) / "c.json"
        path.write_text(json.dumps({"schema": ev.CASES_SCHEMA_V2, "cases": cases}))
        check("load_cases keeps only the train split",
              [c["id"] for c in ev.load_cases(str(path), split="train")] == ["t1"])
        check("load_cases keeps only the test split",
              [c["id"] for c in ev.load_cases(str(path), split="test")] == ["t2", "t3"])
        refs = copy.deepcopy(cases)
        refs[0]["gold_doctrine"] = [REF_A]
        path.write_text(json.dumps({"schema": ev.CASES_SCHEMA_V2, "cases": refs}))
        check("an opaque doctrine section ref is carried through",
              ev.load_cases(str(path))[0]["gold_doctrine"] == [REF_A])
        for label, bad_ref in (("a malformed doctrine ref", "not a ref"),
                               ("a slug-shaped doctrine ref (slugs carry names)",
                                "engineering-workflow-sop#02-before-you-push")):
            refs[0]["gold_doctrine"] = [bad_ref]
            path.write_text(json.dumps({"schema": ev.CASES_SCHEMA_V2, "cases": refs}))
            try:
                ev.load_cases(str(path))
                check(f"the harness refuses {label}", False)
            except ValueError:
                check(f"the harness refuses {label}", True)
        bad = copy.deepcopy(cases)
        bad[0]["split"] = "dev"
        path.write_text(json.dumps({"schema": ev.CASES_SCHEMA_V2, "cases": bad}))
        try:
            ev.load_cases(str(path))
            check("an unknown split is refused", False)
        except ValueError:
            check("an unknown split is refused", True)


def test_cli_train():
    with tempfile.TemporaryDirectory(prefix="rule-gold-label-selftest-") as tmp:
        result = subprocess.run(
            [sys.executable, str(EVAL_CLI), "--cases", str(FIXTURE), "--split", "train",
             "--jev", "off", "--out-dir", tmp],
            capture_output=True, text=True, timeout=600, cwd=str(REPO))
        check("eval CLI runs on the v2 train split", result.returncode == 0, result.stderr[-1500:])
        report = Path(tmp) / "report.json"
        if report.exists():
            data = json.loads(report.read_text())
            check("report is train-only", data.get("split") == "train")
            check("report has per-class rows",
                  data["paths"]["system_scoped_boot"].get("by_class") is not None)
        result = subprocess.run(
            [sys.executable, str(EVAL_CLI), "--cases", str(FIXTURE), "--jev", "off",
             "--out-dir", tmp],
            capture_output=True, text=True, timeout=600, cwd=str(REPO))
        data = json.loads(report.read_text()) if report.exists() else {}
        check("with no --split, a v2 file is scored on test only",
              result.returncode == 0 and data.get("split") == "test"
              and data.get("cases") == sum(1 for c in json.loads(FIXTURE.read_text())["cases"]
                                           if c["split"] == "test"),
              (result.returncode, data.get("split"), data.get("cases")))


def test_intake(gl, doc, labels):
    live = set(labels["rules"])
    base = doc["cases"][0]
    rid = next(r for r in labels["rules"] if r not in base["gold"])
    probs = {r: 0.1 for r in live}
    probs[rid] = 0.2
    other = next(r for r in labels["rules"] if r != rid)
    probs[other] = 0.5
    case = gl.intake_case(doc, base["id"], rid, live_rules=live, probs=probs)
    check("intake: missed rule is gold", rid in case["gold"], case["gold"])
    check("intake: borderline rules are disputed, not guessed", other in case["disputed"])
    check("intake: split placed by hash",
          case["split"] == gl.split_for_new_case(case["id"], gl.DEFAULT_SEED))
    check("intake: marked as a live miss", case["origin"] == "live-miss"
          and case["live_ref"] == base["id"])
    source = "please push the branch and open the pull request against main today"
    for label, kwargs in (
            ("copied prompt", {"prompt": "push the branch and open the pull request against main",
                               "stratum": "engineering", "source_text": source}),
            ("scrub hit", {"prompt": "email the landlord at someone" + "@" + "example.org",
                           "stratum": "deals_clients"}),
            ("missing paraphrase", {})):
        try:
            gl.intake_case(doc, "live-ref-1", rid, live_rules=live, **kwargs)
            check(f"intake refuses a {label}", False)
        except ValueError:
            check(f"intake refuses a {label}", True)
    try:
        gl.intake_case(doc, base["id"], "notarule", live_rules=live)
        check("intake refuses an unknown rule", False)
    except ValueError:
        check("intake refuses an unknown rule", True)
    ok = gl.intake_case(doc, "live-ref-1", rid, live_rules=live, source_text=source,
                        prompt="Ship the feature branch up and raise a review request.",
                        stratum="engineering")
    check("intake accepts a real paraphrase (partial without Jev)",
          ok["labels"] == "partial" and ok["gold"] == [rid])
    dup = copy.deepcopy(doc)
    dup["cases"].append(case)
    try:
        gl.intake_case(dup, base["id"], rid, live_rules=live, probs=probs)
        check("intake refuses a miss already in the benchmark", False)
    except ValueError:
        check("intake refuses a miss already in the benchmark", True)
    # The verbatim guard covers the tool calls as well as the prompt.
    try:
        gl.intake_case(doc, "live-ref-1", rid, live_rules=live, source_text=source,
                       prompt="Ship the feature branch up and raise a review request.",
                       stratum="engineering",
                       tool_calls=[{"tool_name": "SendMessage",
                                    "tool_input": {"to": "orchestrator", "message": source}}])
        check("intake refuses a tool-call input copied from the live turn", False)
    except ValueError as exc:
        check("intake refuses a tool-call input copied from the live turn",
              "tool-call input" in str(exc), str(exc))
    names = gl.record_name_terms([{"name": "Ada Brightwater", "kind": "person"}])
    try:
        gl.intake_case(doc, "live-ref-1", rid, live_rules=live, extra_names=names,
                       prompt="Draft the renewal note for Brightwater.", stratum="deals_clients")
        check("intake refuses a record name without echoing it", False)
    except ValueError as exc:
        check("intake refuses a record name without echoing it",
              "name" in str(exc) and "Brightwater" not in str(exc), str(exc))
    with tempfile.TemporaryDirectory(prefix="rule-gold-label-selftest-") as tmp:
        fix = Path(tmp) / "cases.v2.json"
        fix.write_text(FIXTURE.read_text(encoding="utf-8"))
        corpus = Path(tmp) / "corpus.json"
        corpus.write_text(json.dumps({"rules": [{"id": r, "statement": "s"} for r in live]}))
        names_file = Path(tmp) / "names.json"
        names_file.write_text(json.dumps([{"name": "Ada Brightwater", "kind": "person"}]))
        result = subprocess.run(
            [sys.executable, str(INTAKE_CLI), "--case-id", base["id"], "--missed-rule", rid,
             "--fixture", str(fix), "--corpus", str(corpus), "--no-label",
             "--names-file", str(names_file)],
            capture_output=True, text=True, timeout=120, cwd=str(REPO))
        check("intake CLI exits zero", result.returncode == 0, result.stderr[-800:])
        after = json.loads(fix.read_text())
        check("intake CLI appended exactly one case",
              len(after["cases"]) == len(doc["cases"]) + 1
              and after["cases"][-1]["missed_rule"] == rid)
        test_intake_order(gl, doc, rid, fix, corpus, names_file, Path(tmp))


class _StubJev:
    """Stands in for ops/typesafe_client in-process: records every request."""

    def __init__(self):
        self.requests = []

    def noul(self, question, true=None, false=None):
        return {"kind": "noul", "question": question}

    def ask(self, state, questions, **_kwargs):
        self.requests.append(state)
        return {"answers": {qid: {"noul": 0.1} for qid in questions},
                "usage": {"input_tokens": 1}}


def test_intake_order(gl, doc, rid, fix, corpus, names_file, tmp):
    """NOTHING LEAVES THE MACHINE BEFORE THE CHECKS PASS: run the intake's own
    main() with a stub in place of the Jev client and count its requests."""
    cli = load(INTAKE_CLI, "rule_delivery_eval_intake_for_selftest")
    stub = _StubJev()
    real_load = cli._load
    cli._load = lambda name: stub if name == "typesafe_client" else real_load(name)
    before = fix.read_text()
    base = doc["cases"][1]["id"]
    at, dot = "@", "."
    refusing = (
        ("a record name", ["--prompt", "Ask Brightwater to confirm the tour time."]),
        ("an email", ["--prompt", "Mail the draft to someone" + at + "somecorp" + dot + "org."]),
        ("a bare host", ["--prompt", "Restart the worker on build-box" + dot + "local."]),
        ("an ssh key path", ["--prompt", "Copy the file under ~/" + dot + "ssh to the box."]),
    )
    for label, extra in refusing:
        code = cli.main(["--case-id", base, "--missed-rule", rid, "--fixture", str(fix),
                         "--corpus", str(corpus), "--names-file", str(names_file),
                         "--stratum", "deals_clients", "--calls-log", str(tmp / "calls.jsonl"),
                         *extra])
        check(f"intake refuses {label} before any Jev request",
              code == 1 and not stub.requests, (code, len(stub.requests)))
    check("a refused intake leaves the fixture byte-identical", fix.read_text() == before)
    code = cli.main(["--case-id", base, "--missed-rule", rid, "--fixture", str(fix),
                     "--corpus", str(corpus), "--names-file", str(names_file), "--dry-run",
                     "--calls-log", str(tmp / "calls.jsonl")])
    check("an accepted intake does reach Jev (so the order test can fail)",
          code == 0 and len(stub.requests) >= 1, (code, len(stub.requests)))
    missing = tmp / "no-such-names.json"
    code = cli.main(["--case-id", base, "--missed-rule", rid, "--fixture", str(fix),
                     "--corpus", str(corpus), "--names-file", str(missing), "--no-label"])
    check("intake refuses when the name check cannot run (fails closed)", code == 2, code)
    cli._load = real_load


def main() -> int:
    gl = load(LIB, "rule_gold_label_for_selftest")
    ev = load(EVAL, "rule_delivery_eval_for_gold_selftest")
    test_bands(gl)
    test_scrub(gl)
    doc, labels = test_fixture(gl, ev)
    test_classes(gl, labels)
    test_harness_v2(ev)
    test_cli_train()
    test_intake(gl, doc, labels)
    return CHECKER.summary(limit=20)


if __name__ == "__main__":
    raise SystemExit(main())
