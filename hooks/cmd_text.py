#!/usr/bin/env python3
"""cmd_text.py — tell a shell COMMAND apart from the prose it carries.

Shared verbatim by hooks/git-writer-gate.py and hooks/staging-attribution-gate.py,
which both pattern-match a Bash command string and both refused prose that merely
DESCRIBED a dangerous command.

WHY IT EXISTS. Three refusals on 2026-08-14, all false:
  * a .gitignore comment explaining why a whole-tree staging command is banned
  * the commit message for a git-writer-gate fix, whose subject IS which git
    commands are dangerous, so it necessarily quotes them
  * the very next commit message, for the fix to that

A heredoc body and a quoted -m message are DATA. The shell hands them to a
program as bytes; it never executes them. Matching them is not caution, it is a
category error — and the effect is perverse: it makes documenting a gate the
hardest thing to do inside that gate's own history, and it teaches whoever hits
it that the gate is noise to be worked around. A gate people learn to route
around has already stopped working.

WHY IT IS SHARED RATHER THAN COPIED. Two gates with two copies of "what counts as
inert" drift, and the drift is silent because each copy still passes its own
tests. That is the same shape as the per-file git scrubs consolidated into
ops/git_env.py hours earlier the same day.

IT FAILS CLOSED. This function decides what a gate is allowed to STOP looking at,
so every ambiguity resolves toward scanning more, not less: a heredoc body is
skipped only from its opening line to a line that is EXACTLY the delimiter, and
an unterminated or malformed heredoc strips nothing at all.
"""
import re

# A heredoc opener: <<EOF, <<-EOF, <<'EOF', <<"EOF".
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# A quoted -m/--message argument. Unquoted -m words are NOT stripped: a bare
# `-m dont-run-git-add-A` is one shell word with no spaces, so it cannot contain
# a command anyway, and matching it loosely would strip real text after it.
_DASH_M_RE = re.compile(r"(?:-m|--message)\s+(?:'[^']*'|\"[^\"]*\")")


def strip_inert_text(cmd):
    """Return `cmd` with heredoc bodies and quoted messages replaced.

    The replacement keeps the opener and the delimiter line, so anything AFTER
    the heredoc is still scanned — only the body between the markers is inert.
    """
    out = cmd
    for m in _HEREDOC_RE.finditer(cmd):
        delim = m.group(2)
        lines = out.split("\n")
        start = next((i for i, line in enumerate(lines) if m.group(0) in line), None)
        if start is None:
            continue
        end = next((j for j in range(start + 1, len(lines))
                    if lines[j].strip() == delim), None)
        if end is None:
            continue  # unterminated — scan the whole thing rather than guess
        out = "\n".join(lines[:start + 1] + lines[end:])
    return _DASH_M_RE.sub("-m <message>", out)
