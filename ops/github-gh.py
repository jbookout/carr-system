#!/usr/bin/env python3
"""Optional gh wrapper for existing scripts; installing it is a separate action.

GET pagination uses one budgeted CLI request per page. Other gh subcommands
may make several hidden requests; invocation counts are a lower bound. Provider
holds still apply. This wrapper neither installs itself nor changes credentials.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.github_reader import GitHubReader, GitHubUnreadable


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    real = os.environ.get("GH_LIMITER_REAL")
    if not real:
        candidates = [shutil.which("gh"), "/opt/homebrew/bin/gh", "/usr/local/bin/gh"]
        real = next((p for p in candidates if p and Path(p).resolve() != Path(__file__).resolve() and os.access(p, os.X_OK)), None)
    if not real:
        print("GitHub CLI unavailable", file=sys.stderr)
        return 1
    reader = GitHubReader(gh=real, retry_delays=())
    try:
        if args and args[0] == "api" and "--paginate" in args:
            parser_args = args[1:]
            path, query, slurp = None, None, False
            while parser_args:
                arg = parser_args.pop(0)
                if arg == "--paginate":
                    continue
                if arg == "--slurp":
                    slurp = True
                elif arg in ("--jq", "-q") and parser_args:
                    query = parser_args.pop(0)
                elif arg.startswith("-") or path is not None:
                    raise GitHubUnreadable("Bounded pagination supports GET paths, --slurp and --jq only", kind="invalid_response")
                else:
                    path = arg
            if not path:
                raise GitHubUnreadable("Bounded pagination requires a GET path", kind="invalid_response")
            if query and not shutil.which("jq"):
                raise GitHubUnreadable("jq unavailable; paginated read stopped", kind="invalid_response")
            pages = reader.api(path, paginate=True, slurp=True)
            outputs = []
            for payload in ([pages] if slurp else pages):
                output = json.dumps(payload) + "\n"
                if query:
                    result = subprocess.run(["jq", "-r", query], input=output, capture_output=True, text=True)
                    if result.returncode:
                        raise GitHubUnreadable("Invalid jq filter", kind="invalid_response")
                    output = result.stdout
                outputs.append(output)
            output = "".join(outputs)
        else:
            output = reader.text(args)
        sys.stdout.write(output)
        return 0
    except GitHubUnreadable as exc:
        # Local refusals deliberately omit gh's provider-error prefixes.
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
