import fcntl
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lib.rule_recall import delivery_counts

ACTION = ("on breach: open/update one dedup loop · owner orchestrator · remediation inspect "
          "each listed rule's event, adapter and full-text receipt; repair route or propose "
          "rewrite/retirement; retain boot · verify per-rule benchmark and real-turn recall "
          "100% · auto-clear only when every active rule has a full-text delivery in 14 days")


def run_verb(root, name, payload):
    result = subprocess.run([str(root / "run.sh"), "call", name, json.dumps(payload)],
                            cwd=root, capture_output=True, text=True, timeout=35)
    if result.returncode:
        raise RuntimeError(f"{name} failed ({result.returncode})")
    answer = json.loads(result.stdout[result.stdout.find("{"):])
    if answer.get("error") or answer.get("ok") is False:
        raise RuntimeError(f"{name} did not confirm")
    return answer


def check_recall(rows, rules, state_path, run, *, now=None):
    now = now or datetime.now(timezone.utc).isoformat()
    measured = delivery_counts(rows, rules, now, 14)
    if not measured["readable"]:
        return f"UNAVAILABLE rule recall — no dated full-text receipts in 14 days; warning retained · {ACTION}"
    zero = measured["zero"]
    subjects = "; ".join(f"{rules[rid]} ({rid})" for rid in zero)
    line = (f"{'WARN' if zero else 'OK'} rule recall — {len(zero)}/{len(rules)} active rules "
            f"with zero observed full-text deliveries in 14 days" + (f": {subjects}" if zero else "") + f" · {ACTION}")
    path = Path(state_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with Path(str(path) + ".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(path.read_text()) if path.exists() else {}
            body = ("Rule delivery silence: " + subjects + ". " + ACTION +
                    ". A zero is observed silence, not authority to retire a rule or proof it had a binding moment.")
            signature = ",".join(zero)
            def mutate(name, args):
                args["idempotency_key"] = str(uuid.uuid5(uuid.NAMESPACE_URL,
                    f"carr-rule-recall:{state.get('loop_id', 'new')}:{name}:{signature}:{now[:10]}"))
                answer = run(name, args)
                if answer.get("ok") is not True:
                    raise RuntimeError(f"{name} did not confirm write")
                return answer
            def version():
                row = run("read-loop", {"loop_id": state["loop_id"]})
                row = row.get("loop", row)
                if row.get("loop_id") != state["loop_id"] or type(row.get("version")) is not int:
                    raise RuntimeError("loop version unavailable")
                return row["version"]
            if zero and not state.get("loop_id"):
                answer = mutate("add-loop", {"kind": "open_loop", "domain": "system", "owner": "claude",
                    "marker": "none", "body": body, "blocker": "capability",
                    "blocker_detail": "The health runner cannot repair installed client routes or perform a source PR; the orchestrator must execute that delivery"})
                if not answer.get("loop_id"):
                    raise RuntimeError("missing loop id")
                state = {"loop_id": answer["loop_id"], "zero": zero}
            elif zero and zero != state.get("zero"):
                mutate("update-loop", {"loop_id": state["loop_id"], "base_version": version(), "body": body})
                state["zero"] = zero
            elif not zero and state.get("loop_id"):
                mutate("close-loop", {"loop_id": state["loop_id"], "base_version": version(), "resolution": "done",
                    "outcome": "Auto-cleared: every active rule has a dated full-text delivery in the last 14 days."})
                state = {}
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(state) + "\n")
            os.replace(temp, path)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        line += f" · loop action FAILED ({type(exc).__name__})"
    return line


def check_local(root):
    """Use installed ledgers; do not export statements, inputs or transcript bodies."""
    root = Path(root)
    live = run_verb(root, "standing-context", {"detail": "full"})
    rules = {r["id"]: r["statement"].split(".", 1)[0][:120]
             for r in live["shared_rules"] + live["personal_rules"]}
    logs = [Path.home() / ".config/carr/claude-rule-delivery.jsonl"]
    for name in ("rule-trigger-delivery", "rule-route-delivery", "rule-boot-delivery"):
        logs.extend((root / "out").glob(name + "*.jsonl*"))
    rows = []
    for path in logs:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except ValueError:
                    continue
    return check_recall(rows, rules, root / "out/rule-recall-loop.json",
                        lambda name, args: run_verb(root, name, args))
