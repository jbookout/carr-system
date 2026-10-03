"""Gold corpus contract and privacy transform for local system-work capture.

Gold fixtures are derived from source assertions, never from provider answers.
Traffic receipts intentionally have no gold and require separate adjudication.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

from ops import business_data_patterns as privacy
from tools.judge.paired_eval import digest, freeze

SCHEMA = "carr-judge-system-work-gold/v1"
ROOT = Path(__file__).resolve().parents[2]


def redact(value):
    """Replace sensitive spans in keys and values with synthetic stand-ins.

    Uses the shared public-fixture scanner and its local client roster. If a
    roster match survives span replacement, discard that string as a whole.
    Unknown names cannot be inferred by regex; capture requires a local roster.
    """
    replacements = {}
    def text(value):
        # Normalize machine paths before generic privacy patterns.
        value = re.sub(r"/(?:Users|home)/[^/\s]+", "{{HOME}}", value)
        value = re.sub(r"(?i)\b(?:joe|dell|bookout|mccraney|booko)\b", "synthetic_operator", value)
        names = privacy.roster()
        if names:
            # Match token spans once, rather than scanning text with one regex
            # per roster entry. Preserve case and punctuation outside each span.
            tokens = []
            for word in re.finditer(r"[a-zA-Z0-9’']+", value):
                offset = word.start()
                for piece in re.finditer(r"[A-Z]?[a-z’']+|[A-Z]+(?![a-z])|[0-9]+", word.group()):
                    tokens.append((piece.group().lower().replace("'", "").replace("’", ""), offset + piece.start(), offset + piece.end()))
            spans = []
            for length in names.lengths:
                for index in range(len(tokens) - length + 1):
                    if " ".join(t[0] for t in tokens[index:index + length]) in names.full:
                        spans.append((tokens[index][1], tokens[index + length - 1][2]))
            for lo, hi in sorted(set(spans), reverse=True):
                value = value[:lo] + "[synthetic_person]" + value[hi:]
            if names.hits(value):
                return "[synthetic_private_text]"
        for kind, pattern in privacy.PATTERNS.items():
            def replace(match):
                if kind == "uuid" and match.group(0).lower() in privacy.ALLOWED_UUIDS:
                    return match.group(0)
                key = (kind, match.group(0))
                if key not in replacements:
                    replacements[key] = f"[synthetic_{kind}_{len(replacements) + 1}]"
                return replacements[key]
            value = pattern.sub(replace, value)
        return value
    def walk(node):
        if isinstance(node, str):
            return text(node)
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            result = {}
            for key, item in node.items():
                if re.fullmatch(r"(?i)(?:api[_-]?key|token|secret|password|passwd|authorization|credential|credentials|access_token|refresh_token)", str(key)):
                    continue
                safe = text(str(key))
                if safe in result:
                    raise ValueError("redaction key collision")
                result[safe] = walk(item)
            return result
        return copy.deepcopy(node)
    result = walk(value)
    if privacy.scan_value(result):
        raise ValueError("redaction left private content")
    return result


def normalized_state(value):
    """Fingerprint whitespace/case variants without relying on group claims."""
    if isinstance(value, str):
        return " ".join(value.casefold().split())
    if isinstance(value, dict):
        return {key: normalized_state(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalized_state(item) for item in value]
    return value


def validate(corpus, *, source_root=None):
    if corpus.get("schema") != SCHEMA:
        raise ValueError("invalid corpus schema")
    cases = corpus.get("cases")
    frozen = freeze(cases)
    if corpus.get("corpus_sha256") != frozen["corpus_sha256"]:
        raise ValueError("corpus digest mismatch")
    groups, inputs, sources = {}, {}, {}
    for case in cases:
        if case.get("split") not in {"dev", "final"}:
            raise ValueError("invalid split")
        if not isinstance(case.get("group"), str) or not case["group"]:
            raise ValueError("missing source scenario group")
        if not isinstance(case.get("purpose"), str) or not case["purpose"]:
            raise ValueError("missing purpose")
        for key, book in [(case["group"], groups), (digest(normalized_state(case["request"]["state"])), inputs)]:
            if key in book and book[key] != case["split"]:
                raise ValueError("split leakage: source family or frozen state crosses splits")
            book[key] = case["split"]
        if not isinstance(case.get("label_basis"), str) or not case["label_basis"]:
            raise ValueError("missing gold label basis")
        source = case.get("source_ref", {})
        if (not isinstance(source.get("path"), str) or
                not isinstance(source.get("locator"), str) or not source["locator"] or
                not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256", "")))):
            raise ValueError("missing hash-bound source ref")
        if source_root is not None:
            path = (Path(source_root) / source["path"]).resolve()
            if not path.is_relative_to(Path(source_root).resolve()):
                raise ValueError("source escapes repository")
            if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError("source digest mismatch")
        questions, gold = case["request"]["questions"], case.get("gold")
        if not isinstance(gold, dict) or set(gold) != set(questions):
            raise ValueError("gold must cover every question")
        for qid, question in questions.items():
            if not isinstance(question.get("instructions"), str) or not question["instructions"]:
                raise ValueError("missing question instructions")
            kind, label = question.get("type"), gold[qid]
            if kind == "noul":
                if type(label) is not bool:
                    raise ValueError("noul gold must be boolean")
            elif kind == "choice":
                criteria = question.get("criteria")
                if not isinstance(criteria, dict) or len(criteria) < 2 or label not in criteria:
                    raise ValueError("choice gold outside criteria")
            elif kind == "score":
                criteria = question.get("criteria")
                if (not isinstance(criteria, list) or len(criteria) < 2 or
                        type(label) is not int or not 0 <= label < len(criteria)):
                    raise ValueError("score gold outside criteria")
            else:
                raise ValueError("unknown question shape")
        if source_root is not None:
            family = verify_source_gold(case, source_root)
            if family in sources and sources[family] != case["split"]:
                raise ValueError("split leakage: authenticated source scenario crosses splits")
            sources[family] = case["split"]
        if privacy.scan_value(case):
            raise ValueError("private content in corpus")
    return corpus



def verify_source_gold(case, root):
    """Authenticate the assertion, not merely the existence of its file."""
    ref = case["source_ref"]
    row = None
    path = Path(root) / ref["path"]
    locator = ref["locator"]
    if ref["path"].endswith("adjudications.v2.jsonl"):
        line = int(locator.split(";", 1)[0].split(":", 1)[1])
        row = json.loads(path.read_text().splitlines()[line - 1])
        if f"case:{row['case']};rule:{row['rule']}" != locator.split(";", 1)[1]:
            raise ValueError("source locator mismatch")
        expected = {"binds": row["gold"]}
        from ops.rule_gold_label import adjudication_case_binding
        inputs = json.loads((Path(root) / case["input_ref"]["path"]).read_text())["cases"]
        original = next(item for item in inputs if item["id"] == row["case"])
        if row.get("case_binding") != adjudication_case_binding(original):
            raise ValueError("source adjudication input binding mismatch")
        family = (ref["path"], row["case"])
    elif ref["path"].endswith("gate-scenarios.jsonl"):
        line = int(locator.split(";", 1)[0].split(":", 1)[1])
        row = json.loads(path.read_text().splitlines()[line - 1])
        if locator.split(";", 1)[1] != "id:" + row["id"]:
            raise ValueError("source locator mismatch")
        verdict = row["expect"]
        kind = case["request"]["questions"]["intervention"]["type"]
        expected = {"intervention": verdict if kind == "choice" else
                    {"allow": 0, "announce": 1, "deny": 2, "reopen": 2}[verdict]}
        family = (ref["path"], row["gate"])
    elif ref["path"].endswith("jev-code-review-pilot/corpus.v1.json"):
        row = next(item for item in json.loads(path.read_text())["items"] if item["id"] == locator)
        expected = row["labels"]
        family = (ref["path"], row["path"])
    elif ref["path"].endswith("jev-intake-selftest.py"):
        levels = {"PickEffortTests.test_high_needs_both_a_high_score_and_high_confidence": 2,
                  "PickEffortTests.test_moderate_score_is_medium": 1,
                  "PickEffortTests.test_low_score_is_low": 0}
        choices = {"PickExampleTests.test_a_matching_example_is_returned": {"best_example": "ex-1"},
                   "PickExampleTests.test_none_fits_when_jev_says_so": {"best_example": "none of these fits"}}
        if locator in levels:
            expected = {"difficulty": levels[locator]}
        elif locator in choices:
            expected = choices[locator]
        elif locator in {"PickContextTests.test_central_plus_needed_files_are_returned_ordered", "PickContextTests.test_none_of_these_and_no_yes_files_reports_none_found"}:
            matched = locator.endswith("test_central_plus_needed_files_are_returned_ordered")
            expected = {"central": "ops/widget_loader.py" if matched else "no single file is the natural place to start"}
            for qid in case["request"]["questions"]:
                if qid.startswith("needs::"):
                    expected[qid] = matched and qid == "needs::ops/widget_loader.py"
        else:
            raise ValueError("unknown asserted intake scenario")
        if "def " + locator.split(".")[-1] not in path.read_text():
            raise ValueError("missing source assertion")
        family = (ref["path"], locator.split(".", 1)[0])
    else:
        raise ValueError("unsupported gold source")
    if case["gold"] != expected:
        raise ValueError("source gold does not match corpus label")
    for field in ("input_ref", "contract_ref", "inventory_ref", "replay_ref", "replay_manifest_ref", "projection_ref"):
        if field in case:
            extra = case[field]
            bound = (Path(root) / extra["path"]).resolve()
            if not bound.is_relative_to(Path(root).resolve()) or hashlib.sha256(bound.read_bytes()).hexdigest() != extra["sha256"]:
                raise ValueError("source input digest mismatch")
    if case["request"] != source_request(case, root, row):
        raise ValueError("source request does not match authenticated transformation")
    return family


def source_request(case, root, row=None):
    """Bind evaluated inputs to an authenticated, reviewed source projection.

    Projections freeze rule text, question contracts, and redacted historical
    code excerpts unavailable in an archive. They are source artifacts, not
    caller hashes or provider answers. Turns and replay inputs are reconstructed
    from their original assertions. Changing a projection requires source review.
    """
    ref = case.get("projection_ref", {})
    if (case.get("transformation") != "source-projection/v1" or
            ref.get("path") != "ops/fixtures/judge-provider/source-projections.v1.json" or
            ref.get("locator") != case["source_ref"]["locator"]):
        raise ValueError("source request lacks declared projection")
    projection = json.loads((Path(root) / ref["path"]).read_text())
    if projection.get("schema") != "carr-judge-source-projections/v1":
        raise ValueError("source request projection schema mismatch")
    source = case["source_ref"]["path"]
    if source.endswith("adjudications.v2.jsonl"):
        original = next(x for x in json.loads((Path(root) / case["input_ref"]["path"]).read_text())["cases"]
                        if x["id"] == row["case"])
        if case["input_ref"]["locator"] != original["id"]:
            raise ValueError("source request input locator mismatch")
        rule = projection["rules"].get(row["rule"] + ":" + case["rule_sha256"])
        if rule is None:
            raise ValueError("source request rule snapshot mismatch")
        state = redact({"turn": {"prompt": original["prompt"], "tool_calls": original.get("tool_calls", [])}})
        state["rule"] = rule
        questions = projection["questions"]["binding"]
    elif source.endswith("gate-scenarios.jsonl"):
        contract = case["contract_ref"]
        if contract["path"] != "hooks/" + row["gate"]:
            raise ValueError("source request gate contract mismatch")
        replay = json.loads((Path(root) / case["replay_manifest_ref"]["path"]).read_text())
        environment = copy.deepcopy(projection["replay_environment"])
        environment["clock_utc"] = row.get("clock", replay["pinned_utc"])
        environment["default_turn_prompt"] = replay["turn_prompt"]
        environment["env"] = row.get("env", {})
        inputs = {k: v for k, v in row.items() if k not in {"id", "gate", "matcher", "expect", "why", "clock", "clock_label"}}
        state = redact({"gate_contract": (Path(root) / contract["path"]).read_text(),
                        "event": row["event"], "input": inputs, "replay_environment": environment})
        kind = case["request"]["questions"]["intervention"]["type"]
        questions = projection["questions"]["intervention-" + kind]
    elif source.endswith("jev-code-review-pilot/corpus.v1.json"):
        frozen = projection["pilot"][row["id"]]
        if (frozen["source"] != case["frozen_source_ref"] or
                frozen["source"]["path"] != row["path"] or
                frozen["source"]["revision"] != json.loads((Path(root) / source).read_text())["pinned_commit"]):
            raise ValueError("source request historical code binding mismatch")
        state = frozen["state"]
        questions = {qid: projection["questions"]["pilot"][qid] for qid in row["labels"]}
    else:
        request = projection["intake"].get(case["source_ref"]["locator"])
        if request is None:
            raise ValueError("source request intake projection mismatch")
        return request
    return {"state": state, "questions": questions, "model": "jev-1.13.0"}


def load_corpus(path, *, split=None, source_root=None):
    corpus = validate(json.loads(Path(path).read_text()), source_root=ROOT if source_root is None else source_root)
    if split is None:
        return corpus
    if split not in {"dev", "final"}:
        raise ValueError("invalid split")
    # Return the existing paired runner contract, with only the chosen split.
    return freeze([case for case in corpus["cases"] if case["split"] == split])
