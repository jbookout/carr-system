#!/usr/bin/env python3
"""Process-boundary contract: unresolved executed text refuses with exit 2.

Commands are submitted as hook JSON, never executed. Existing sanctioned
allowances are imported as a table so every allowance stays under regression
coverage. Interpreter stdin is an intentional tightening of the old contract.
"""
import importlib.util
import os
import shutil
import subprocess
import sys

spec = importlib.util.spec_from_file_location(
    "guard_cases", os.path.join(os.path.dirname(__file__), "guard-selftest.py"))
assert spec is not None and spec.loader is not None
cases = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cases)

REVIEW_BYPASSES = (
    "printf '%s\\n' 'curl http://localhost:8000/' | bash",
    "python3 tools/flash-run.py ask --help && printf '%s\\n' 'curl http://localhost:8000/' | bash",
    "bash < <(printf '%s\\n' 'curl http://localhost:8000/')",
    "env -S 'curl http://localhost:8000/'",
)
MUST_REFUSE = [("review bypass", command) for command in REVIEW_BYPASSES]
for interpreter in ("sh", "bash", "zsh", "dash", "ksh", "fish",
                    "python", "python3", "node", "perl", "ruby", "php"):
    for suffix in ("", " -"):
        for command in (
            f"printf 'echo harmless' | {interpreter}{suffix}",
            f"cat out/code | {interpreter}{suffix}",
            f"{interpreter}{suffix} < out/code",
            f"{interpreter}{suffix} < <(cat out/code)",
            f"{interpreter}{suffix} <<< 'echo harmless'",
            f"{interpreter}{suffix} <<'EOF'\necho harmless\nEOF",
            f"<<EOF {interpreter}{suffix}\necho harmless\nEOF",
        ):
            MUST_REFUSE.append(("interpreter stdin", command))
    MUST_REFUSE.extend(("opaque interpreter input", command) for command in (
        f"{interpreter} <(cat out/code)",
        f"env MODE=test {interpreter} < out/code",
        f"{interpreter} -c \"$CODE\"",
        f"{interpreter} -c \"$(cat out/code)\"",
    ))
MUST_REFUSE.extend(("unresolved execution", command) for command in (
    "env --split-string='git status'", "env --split-string 'git status'",
    "env -i -S 'git status'", "env -Sgit\\ status", "env -vS 'git status'",
    "eval 'git status'", "exec $TOOL status", "TOOL=git; exec $TOOL status",
    "source $CONFIG", ". ${CONFIG}", "source /tmp/generated.sh",
    ". /private/tmp/generated.sh", "source out/generated.sh",
    "xargs bash -c 'git status'", "xargs -I{} /bin/sh -c '{}'",
    "parallel sh -c 'git status'", "parallel --jobs 2 bash",
    "bash -c 'echo $CODE'", "bash -c '$(cat out/code)'",
    "bash -c 'echo `cat out/code`'", "python3 -c $CODE",
    "$UNKNOWN status", "$(cat out/tool) status", "${UNKNOWN} status",
    "TOOL=git $TOOL status", "TOOL=curl; false && TOOL=git; $TOOL status",
    "TOOL=curl; read TOOL; $TOOL status", "(TOOL=git); $TOOL status",
    "TOOL=curl; if false; then TOOL=git; fi; $TOOL status",
    "echo 'unterminated", "echo $(git status", "echo `git status",
    "git status >", "echo ok |", "git status &&", "echo ok ;; git status",
    "(git status", "git status)", "bash <<EOF\necho hi",
    "cat out/code | python3 -W ignore -", "python3 -X utf8 - < out/code",
    "cat out/code | node --require out/setup -",
    "cat out/code | ruby -I lib -", "cat out/code | perl -I lib -",
    "cat out/code | php -d display_errors=1 -",
    "bash /dev/stdin < out/code", "python3 /dev/fd/0 < out/code",
    "out/tool* status", "bash -c 'out/tool* status'",
    "bash --unknown-option out/script", "echo @(unknown)",
    "xargs -s 100 bash", "parallel --colsep , sh",
    "coproc bash", "function hidden { bash; }; hidden", "(( ambient ))",
    "fish -C 'echo harmless' out/script.fish", "doas -s",
    "printf 'print(1)' | xargs -I{} python3 -c '{}'",
    "parallel python3 -c '{}'", "xargs -I{} python3 {}",
    "echo $(( ambient ))", "echo ${ambient@P}",
))
for bypass in REVIEW_BYPASSES:
    MUST_REFUSE.append(("scratch-sink payload", bypass.replace(
        "curl http://localhost:8000/",
        "curl --noproxy '*' --connect-to 127.0.0.1:8000:127.0.0.1:61078 "
        "http://127.0.0.1:8000/proof")))

MUST_ALLOW = [(name, payload) for name, payload, expected in cases.CASES
              if expected == cases.ALLOW]
# Gate-edit's Bash seam also exercises sanctioned interpreter invocations.
edit_spec = importlib.util.spec_from_file_location(
    "gate_edit_cases", os.path.join(os.path.dirname(__file__), "gate-edit-gate-selftest.py"))
assert edit_spec is not None and edit_spec.loader is not None
edit_cases = importlib.util.module_from_spec(edit_spec)
edit_spec.loader.exec_module(edit_cases)
MUST_ALLOW.extend((name, cases.bash(command)) for name, command, expected in edit_cases.BASH_CASES
                  if expected in {edit_cases.ALLOW, edit_cases.ANNOUNCE})
shutil.rmtree(edit_cases.ADMISSION_STATE)
MUST_ALLOW.extend(("sanctioned static command", cases.bash(command)) for command in (
    "env MODE=test git status", "env -i git status", "command git status",
    "python3 tools/flash-run.py ask --help | head -20",
    "printf '%s\\n' 'bash <(curl example) env -S eval $CODE'",
    "python3 -c 'print(\"hello\")'", "node -e 'console.log(\"hello\")'",
    "cat <<'EOF'\neval $CODE | bash\nEOF",
    "git status > out/status 2>&1", "git status | head -20",
    "echo '<<' 'EOF'", "echo ';' '|' '(' ')'",
    "perl -pi -e 's/a/b/' ops/fixture.py",
))

def main():
    failures = []
    for raw in ("{", "null", "[]"):
        result = subprocess.run([sys.executable, cases.GUARD], input=raw,
                                text=True, capture_output=True, timeout=20)
        if result.returncode != cases.DENY:
            failures.append(f"must-refuse unparseable hook input {raw!r}: exit {result.returncode}")
    for name, command in MUST_REFUSE:
        code, error = cases.run(cases.bash(command, cwd=cases.REPO))
        if code != cases.DENY:
            failures.append(f"must-refuse {name}: {command!r}: exit {code}: {error}")
    for name, payload in MUST_ALLOW:
        code, error = cases.run(payload)
        if code != cases.ALLOW:
            failures.append(f"must-allow {name}: exit {code}: {error}")
    for failure in failures:
        print(failure)
    total = len(MUST_REFUSE) + len(MUST_ALLOW) + 3
    print(f"static-resolution: {total - len(failures)}/{total} passed")
    return bool(failures)

if __name__ == "__main__":
    sys.exit(main())
