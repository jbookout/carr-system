"""Run isolated source studies and record their lifecycle on the local board."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import fcntl
import hashlib
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
BOARD = "source-studies"


def execute(command, folder, output, log, timeout, phase):
    # A timed-out retrieval must not leave Grok or its children running.
    with subprocess.Popen(command, cwd=folder, stdin=subprocess.DEVNULL,
                          stdout=output, stderr=log, start_new_session=True) as child:
        try:
            return child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            raise RuntimeError(f"{phase} timed out after {timeout:g}s") from None


def board(*args):
    # Board CLI uses read/modify/write: serialize both threads and invocations.
    directory = Path(os.environ.get("PROGRESS_BOARD_ROOT", ROOT / "out")) / "boards"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".source-studies.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (directory / f"{BOARD}.json").exists():
            subprocess.run([sys.executable, str(ROOT / "tools/progress_board.py"),
                            "init", BOARD, "--title", "Source application studies"],
                           cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, check=True)
        subprocess.run([sys.executable, str(ROOT / "tools/progress_board.py"), *args],
                       cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, check=True)


def study(url, brief, day, retrieval_timeout, study_timeout, grok_timeout_option):
    parsed = urlsplit(url)
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", parsed.netloc + parsed.path).strip("-.")[:90]
    digest = hashlib.sha256(url.encode()).hexdigest()[:10]
    folder = Path(tempfile.mkdtemp(prefix=f"{label}-{digest}-", dir=day))
    report = folder / "report.md"
    task_id = f"{day.name}-{folder.name}"
    card = ["task", BOARD, task_id, "--title", f"Study {url}",
            "--executor", "Codex gpt-6.1-sol / high"]
    board(*card, "--status", "running", "--note", f"Retrieving {url}; report: {report}")
    try:
        retrieval = folder / "retrieval.txt"
        if (parsed.hostname or "").lower() in {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}:
            command = [str(ROOT / "bin/grok-run.sh"), "--effort", "high", "--prompt",
                       f"Retrieve {url} in full, including the full thread, quoted posts, linked articles, "
                       "and attached media. Return raw source text with live URLs and identify anything "
                       "unread. Retrieval only; do not summarize, grade, or draft applications."]
            if grok_timeout_option:
                command.extend(["--timeout-seconds", str(math.ceil(retrieval_timeout))])
        else:
            command = ["curl", "--fail", "--location", "--silent", "--show-error", "--", url]
        with retrieval.open("wb") as output, (folder / "retrieval.log").open("wb") as log:
            code = execute(command, folder, output, log, retrieval_timeout, "retrieval")
        if code:
            raise RuntimeError(f"retrieval failed (exit {code}); see {folder / 'retrieval.log'}")
        if not retrieval.stat().st_size:
            raise RuntimeError("retrieval returned no source content")
        prompt = (brief + f"\n\nTHE SOURCE FOR THIS JOB: {url}\n"
                  f"Its retrieval is {retrieval}. Write your application plan to {report}.\n"
                  f"CARR repository: {ROOT}. Keep downloads inside {folder / 'downloads'}.\n")
        with (folder / "codex.log").open("wb") as log:
            code = execute(["codex", "exec", "-m", "gpt-6.1-sol", "-c",
                                     'model_reasoning_effort="high"', "--skip-git-repo-check",
                                     "--sandbox", "danger-full-access", prompt],
                           folder, log, subprocess.STDOUT, study_timeout, "study")
        if code:
            raise RuntimeError(f"study failed (exit {code}); see {folder / 'codex.log'}")
        if not report.is_file() or not report.read_text(encoding="utf-8").strip():
            raise RuntimeError(f"missing report: {report}")
    except (OSError, RuntimeError) as error:
        board(*card, "--status", "blocked", "--note", f"{error}; report: {report}")
        print(f"BLOCKED {url}: {error}", file=sys.stderr)
        return False
    board(*card, "--status", "done", "--note", f"Application plan: {report}")
    print(f"DONE {url}: {report}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+", help="HTTP(S) sources, one study per URL")
    args = parser.parse_args()
    try:
        retrieval_timeout = float(os.environ.get("STUDY_RETRIEVAL_TIMEOUT_SECONDS", "600"))
        study_timeout = float(os.environ.get("STUDY_CODEX_TIMEOUT_SECONDS", "3600"))
        if not all(math.isfinite(t) and t > 0 for t in (retrieval_timeout, study_timeout)):
            raise ValueError
    except ValueError:
        parser.error("study timeouts must be positive finite seconds")
    for url in args.urls:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            parser.error(f"expected an HTTP(S) URL: {url}")
    brief = (ROOT / "ops/prompts/source-study-brief.md").read_text(encoding="utf-8")
    grok_timeout_option = False
    if any((urlsplit(url).hostname or "").lower() in
           {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"} for url in args.urls):
        try:
            help_result = subprocess.run([str(ROOT / "bin/grok-run.sh"), "--help"],
                                         stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
            grok_timeout_option = help_result.returncode == 0 and "--timeout-seconds" in help_result.stdout
        except (OSError, subprocess.TimeoutExpired):
            pass
        if not grok_timeout_option:
            print("Grok --timeout-seconds depends on draft PR 1427; using the outer retrieval timeout.", file=sys.stderr)
    day = ROOT / "out/source-studies" / datetime.now().date().isoformat()
    day.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda url: study(url, brief, day, retrieval_timeout,
                                                  study_timeout, grok_timeout_option), args.urls))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
