"""Implementation of grok-run.sh; stdout contains only joined Grok text."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

MODEL = "grok-4.7"
PREFIX = "Do not call any CARR or record-layer tool; do not write anything unless asked."


class PreflightError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def command(argv, timeout):
    # Never echo subprocess diagnostics: they may contain authentication data.
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL)


def version_info(text):
    match = re.search(r"\b(\d+\.\d+\.\d+)(?:-([0-9A-Za-z.-]+))?", text)
    if not match:
        raise PreflightError("grok-run: cannot determine CLI version")
    release = tuple(int(part) for part in match[1].split("."))
    # npm's latest channel ships stable releases; prereleases sort below stable.
    return match[0], (*release, not bool(match[2]))


def installed_version():
    result = command(["grok", "--version"], 20)
    if result.returncode:
        raise PreflightError("grok-run: grok --version failed")
    return version_info(result.stdout)


def preflight():
    version, rank = installed_version()
    try:
        latest = command(["npm", "view", "@xai-official/grok", "version"], 20)
    except (OSError, subprocess.TimeoutExpired):
        latest = None
    if latest is None or latest.returncode:
        print("grok-run: npm registry unreachable; continuing with installed CLI", file=sys.stderr)
    else:
        target, target_rank = version_info(latest.stdout.strip())
        if rank < target_rank:
            upgrade = command(["npm", "install", "-g", f"@xai-official/grok@{target}"], 180)
            if upgrade.returncode:
                raise PreflightError("grok-run: CLI upgrade failed")
            version, rank = installed_version()
            if rank < target_rank:
                raise PreflightError("grok-run: CLI still behind after upgrade")
    models = command(["grok", "models"], 60)
    message = models.stdout + models.stderr
    if re.search(r"not authenticated|unauthenticated|authentication required|sign.?in|grok login", message, re.I):
        raise PreflightError("Grok needs sign-in: a human runs grok login", 3)
    if models.returncode:
        raise PreflightError("grok-run: grok models preflight failed")
    return version


def parse_output(lines, cli_version):
    chunks = []
    end = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            end = {"stopReason": "invalid_stream"}
            break
        if not isinstance(event, dict):
            end = {"stopReason": "invalid_stream"}
            break
        if event.get("type") == "text" and isinstance(event.get("data"), str):
            chunks.append(event["data"])
        elif event.get("type") == "end":
            end = event
    usage = end.get("modelUsage", {})
    models = sorted(usage) if isinstance(usage, dict) else []
    receipt = {
        "requested_model": MODEL, "actual_models": models,
        "stopReason": end.get("stopReason"), "num_turns": end.get("num_turns"),
        "cost_usd": end.get("total_cost_usd", end.get("cost_usd")), "cli_version": cli_version,
    }
    code = 0
    if receipt["stopReason"] != "end_turn":
        code = 4
    elif not any(model.startswith(MODEL) for model in models):
        code = 5
    return "".join(chunks), receipt, code


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Exit 3: sign-in required; 4: incomplete run; 5: wrong model. "
        "GROK_RUN_RECEIPT selects a receipt file instead of stderr. "
        "GROK_RUN_FAKE_NDJSON replays a fixture without calling Grok/npm."))
    parser.add_argument("--effort", choices=("low", "medium", "high"), default="high")
    parser.add_argument("--max-turns", type=int, default=60)
    parser.add_argument("--writable", action="store_true")
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file", type=Path)
    args = parser.parse_args()
    if args.max_turns < 1:
        parser.error("--max-turns must be a positive integer")
    try:
        requested_prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else args.prompt
        fixture = os.environ.get("GROK_RUN_FAKE_NDJSON")
        if fixture:
            with open(fixture, encoding="utf-8") as stream:
                output, receipt, code = parse_output(stream, "fixture")
        else:
            cli_version = preflight()
            result = subprocess.run([
                "grok", "--model", MODEL, "--reasoning-effort", args.effort,
                "--max-turns", str(args.max_turns), "--always-approve",
                "--sandbox", "workspace" if args.writable else "read-only",
                "--output-format", "streaming-json", "--print", PREFIX + "\n\n" + requested_prompt,
            ], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            output, receipt, code = parse_output(result.stdout.splitlines(), cli_version)
            if result.returncode and not code:
                code = 4
        serialized = json.dumps(receipt, sort_keys=True) + "\n"
        if os.environ.get("GROK_RUN_RECEIPT"):
            Path(os.environ["GROK_RUN_RECEIPT"]).write_text(serialized, encoding="utf-8")
        else:
            sys.stderr.write(serialized)
        if output:
            sys.stdout.write(output + ("" if output.endswith("\n") else "\n"))
        return code
    except PreflightError as error:
        print(error, file=sys.stderr)
        return error.code
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"grok-run: {type(error).__name__}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
