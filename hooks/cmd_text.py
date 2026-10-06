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
import shlex


SHELL_BOUNDARIES = frozenset({';', '&&', '||', '|', '&', '|&'})
SHELL_REDIRECTS = frozenset({'<', '>', '>>', '>|', '<>', '<&', '>&', '<<', '<<<', '&>', '&>>'})


def shell_tokens(command):
    """Preserve command boundaries; consume continuations and unquoted IO numbers."""
    def normalize(match):
        token = match.group()
        if token == '\\\n':
            return ''
        if token.startswith('"'):
            return token.replace('\\\n', '')
        if token == '\n':
            return ';'
        if token.isdigit():
            return ''
        return token
    command = re.sub(r"'[^']*'|\"(?:\\[\s\S]|[^\"\\])*\"|\\[\s\S]|\n|(?<!\S)\d+(?=[<>])", normalize, command)
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>')
    lexer.whitespace_split = True
    lexer.commenters = ''
    return list(lexer)


def shell_operands(tokens):
    """Separate all redirects from command words, retaining file output targets."""
    words, outputs = [], []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in SHELL_REDIRECTS:
            if index + 1 >= len(tokens):
                raise ValueError('missing redirection target')
            target = tokens[index + 1]
            if token in {'>', '>>', '>|', '<>', '&>', '&>>'} or (token == '>&' and not target.isdigit() and target != '-'):
                outputs.append(target)
            index += 2
        else:
            words.append(token)
            index += 1
    return words, outputs

# A heredoc opener: <<EOF, <<-EOF, <<'EOF', <<"EOF".
_HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# The flags that carry PROSE, and whose quoted argument is therefore data.
#
# `-m/--message` was the original entry. The rest joined it on 2026-08-14 after
# the fourth false refusal in a day: opening a pull request whose --body
# DESCRIBED the very command being fixed
#
#     gh pr create --title "..." --body "...git stash drop <ref>..."
#
# was refused as though it were running one. Same category error the module
# exists to end, arriving through a flag nobody had listed. The workaround —
# writing the body to a file — is exactly the outcome the docstring above warns
# about, because it teaches whoever hits it that the gate is noise.
#
# LONG FORMS ONLY for the additions, deliberately. Single letters collide across
# tools (`-b` is a branch to git and a body elsewhere, `-d` is delete to one and
# description to another), and a collision here would make the gate stop looking
# at a real command. `-m` keeps its short form because it is unambiguous in
# every tool this repo drives.
#
# Unquoted arguments are NOT stripped, for the reason the original comment gave:
# a bare `--body dont-run-git-add-A` is one shell word with no spaces, so it
# cannot hide a command anyway, and matching it loosely would swallow the real
# text after it. An unterminated quote matches nothing and so strips nothing,
# which is the fail-closed direction.
_PROSE_FLAGS = ("-m", "--message", "--body", "--title", "--comment",
                "--notes", "--description", "--subject")
_DASH_M_RE = re.compile(
    r"(?:" + "|".join(re.escape(f) for f in _PROSE_FLAGS) + r")"
    r"\s+(?:'[^']*'|\"[^\"]*\")")


# Prefix words that run the command after them, each with the options that
# consume the following word. An unlisted option-with-value leaves the value as
# the head, which is not a data command, so a gap here fails closed.
_WRAPPERS = {
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-U"},
    "doas": {"-u", "-C"}, "env": {"-u", "-C", "-S"}, "nice": {"-n"},
    "ionice": {"-c", "-n", "-p"}, "caffeinate": {"-t", "-w"}, "time": {"-f", "-o"},
    "xargs": {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a"},
    "exec": {"-a"}, "stdbuf": set(), "setsid": set(), "nohup": set(), "command": set(),
    "npx": set(), "bunx": set(), "yarn": set(), "pnpm": set(),
}
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish", "ssh", "eval", "source"})


def _command_words(words):
    """`words` from the command head on, past assignments and wrappers."""
    words = list(words)
    while words:
        word = words[0]
        name = word.rsplit("/", 1)[-1]
        if re.match(r"^\w+=", word) or word in {"if", "then", "elif", "else", "do", "!", "{"}:
            words.pop(0)
        elif name in _WRAPPERS:
            words.pop(0)
            while words and words[0].startswith("-"):
                if words.pop(0) in _WRAPPERS[name] and words:
                    words.pop(0)
            if words and words[0] in {"exec", "dlx"}:
                words.pop(0)
        elif word == "timeout" and len(words) > 1:
            words = words[2:]
        else:
            return words
    return []


def _command_head(words):
    return next(iter(_command_words(words)), "")


def feeds_shell(line):
    """Identify command words after lexing, so quoted pipes remain data.

    Behind a wrapper, any bare shell word counts: `sudo -u x bash` and an
    option this module has never seen both still feed a shell.
    """
    try:
        tokens = shell_tokens(line)
    except ValueError:
        return True
    segment = []
    for token in tokens + [";"]:
        if token in SHELL_BOUNDARIES:
            names = [word.rsplit("/", 1)[-1] for word in segment]
            if _command_head(segment).rsplit("/", 1)[-1] in _SHELLS or (
                    names and names[0] in _WRAPPERS and _SHELLS.intersection(names)):
                return True
            segment = []
        else:
            segment.append(token)
    return False


def _rewrite_heredocs(cmd, replace_body):
    """Rewrite each terminated heredoc body through `replace_body`.

    `replace_body(opener_line, quoted, body)` returns the text to put in place
    of the body, or None to leave it as it is. The opener and delimiter lines
    always stay, so everything after the heredoc is still scanned.
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
        body = "\n".join(lines[start + 1:end])
        replacement = replace_body(lines[start], bool(m.group(1)), body)
        if replacement is None:
            continue
        out = "\n".join(lines[:start + 1] + ([replacement] if replacement else []) + lines[end:])
    return out


def strip_inert_text(cmd):
    """Return `cmd` with heredoc bodies and quoted messages replaced.

    The replacement keeps the opener and the delimiter line, so anything AFTER
    the heredoc is still scanned — only the body between the markers is inert.
    """
    def inert_body(opener, quoted, body):
        if feeds_shell(opener):
            return None
        if not quoted and ("$(" in body or "`" in body):
            return None  # unquoted heredocs execute substitutions
        return ""
    out = _rewrite_heredocs(cmd, inert_body)
    def scrub(match):
        argument = match.group(0)
        quoted = argument.split(None, 1)[1]
        if quoted.startswith('"') and ("$(" in quoted or "`" in quoted):
            return argument
        return "-m <message>"
    return _DASH_M_RE.sub(scrub, out)


# ── WHAT THE SHELL WILL RUN AS A COMMAND (2026-10-05) ────────────────────────
#
# strip_inert_text() knows prose FLAGS. It does not know that a grep pattern or
# an agent prompt is data too, so on 2026-10-05 a Grok prompt asking whether
# `wrangler deploy` has a successor, and a `git grep` for that string, were each
# refused as a Cloudflare release. executable_text() returns the text in which a
# command-shaped rule should look for a command word:
#
#   * a quoted argument to a DATA command (one that treats its arguments as
#     bytes: grep, git, gh, echo, an agent CLI taking a prompt) is dropped,
#     except the $(...) and `...` substitutions inside double quotes, which run;
#   * a quoted argument to ANY OTHER command — bash -c, ssh, python -c, a tool
#     this list has never heard of — is unwrapped onto its own line, so a
#     command inside it still starts at a command position;
#   * a heredoc body fed to a shell stays; a quoted one fed to anything else is
#     dropped; an unquoted one keeps only its substitutions.
#
# runs() is what a rule calls. A match in that text counts unless its segment is
# headed by a data command carrying no executable option, so a wrapper or an
# option this module has never seen keeps the match. Requiring a recognised
# command position instead failed open on `nice -n 5` and `sudo -u x` (PR 1578).
#
# It FAILS CLOSED like the rest of this module: an unterminated quote leaves the
# remainder raw, and only commands named below make their arguments inert.
DATA_COMMANDS = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag", "ack", "git", "gh", "echo", "printf",
    "jq", "yq", "cat", "head", "tail", "wc", "sort", "uniq", "cut", "tr", "ls",
    "test", "[", "mkdir", "touch", "diff", "cmp", "tee", "date", "basename",
    "dirname", "realpath", "grok", "claude", "codex",
})
# Wrappers whose prompt argument is handed to an agent, not to this shell.
DATA_COMMAND_SUFFIXES = ("grok-run.sh",)
# Options that make a data command run one of its arguments: git config
# (alias.*, core.pager, core.sshCommand, ...), a search preprocessor, a sort
# compressor. Their presence makes every argument executable.
_EXECUTABLE_OPTION_RE = re.compile(
    r"(?:^|\s)(?:-c\s*\S+=|--config-env[=\s]|--pre[=\s]|--compress-program[=\s])")

_BOUNDARY_RE = re.compile(r"(?:[;&|(\n`]|\$\()")
_SEGMENT_RE = re.compile(r"[;&|(){}\n`]|\$\(")
def _substitution_end(text, start):
    depth, i = 1, start + 2
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"":
            end = _closing_quote(text, i)
            if end is None:
                return len(text)
            i = end + 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if not depth:
                return i
        i += 1
    return len(text)


def _substitutions(text):
    found = []
    i = 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text.startswith("$(", i):
            end = _substitution_end(text, i)
            found.append(text[i + 2:end])
            i = end + 1
        elif text[i] == "`":
            end = i + 1
            while end < len(text) and text[end] != "`":
                end += 2 if text[end] == "\\" else 1
            found.append(text[i + 1:end])
            i = end + 1
        else:
            i += 1
    return found


def _command_word(text_so_far):
    tail = _BOUNDARY_RE.split(text_so_far)[-1]
    try:
        return _command_head(shell_tokens(tail))
    except ValueError:
        return ""


def _is_data_command(word):
    return (word.rsplit("/", 1)[-1] in DATA_COMMANDS
            or word.endswith(DATA_COMMAND_SUFFIXES))


def _closing_quote(text, start):
    quote = text[start]
    i = start + 1
    while i < len(text):
        if quote == '"' and text[i] == "\\":
            i += 2
            continue
        if quote == '"' and text.startswith("$(", i):
            i = _substitution_end(text, i) + 1
            continue
        if text[i] == quote:
            return i
        i += 1
    return None


def _unwrap_quotes(text, executable_args=False):
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            out.append(text[i:i + 2])
            i += 2
            continue
        if c in "'\"":
            end = _closing_quote(text, i)
            if end is None:
                out.append(text[i:])     # unterminated: scan the rest raw
                break
            inner = text[i + 1:end]
            if re.search(r"(?<![\w.])\w+=$", "".join(out)):
                kept = _substitutions(inner) if c == '"' else []
                out.append("x" + "".join("\n" + k + "\n" for k in kept))
            elif not executable_args and _is_data_command(_command_word("".join(out))):
                kept = _substitutions(inner) if c == '"' else []
                out.append(" " + "".join("\n" + k for k in kept) + ("\n_" if kept else ""))
            else:
                # The trailing `_` ends the unwrapped text without opening a
                # new command position for the words that follow the quote.
                out.append("\n" + inner.lstrip("!") + "\n_")
            i = end + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def executable_text(cmd):
    """`cmd` reduced to what the shell will run as commands. See above."""
    def substitutions_only(opener, quoted, body):
        if feeds_shell(opener):
            return None
        return "\n".join(_substitutions(body))
    return _unwrap_quotes(_rewrite_heredocs(strip_inert_text(cmd), substitutions_only),
                          _executes_arguments(cmd))


def _executes_arguments(cmd):
    return feeds_shell(cmd) or bool(_EXECUTABLE_OPTION_RE.search(cmd))


def runs(cmd, pattern):
    """True when the shell would run text matching `pattern`.

    Fails closed: a match counts unless its segment is headed by a data command
    that executes none of its arguments. An unknown wrapper, an option this
    module has not modelled, or text piped into a shell all keep the match.
    """
    # The shell drops an escaping backslash: `wrangler\ deploy` is one word
    # inside an alias value and the same command once that alias runs.
    text = re.sub(r"\\(.)", r"\1", executable_text(cmd), flags=re.S)
    if _executes_arguments(cmd):
        return bool(pattern.search(text))
    for segment in _SEGMENT_RE.split(text):
        if not pattern.search(segment):
            continue
        try:
            words = _command_words(shell_tokens(segment))
        except ValueError:
            return True
        if not words or not _is_data_command(words[0]) or pattern.match(" ".join(words)):
            return True
    return False


# A quoted heredoc whose only reader is `cat > file` or `tee file` is text being
# written to disk. Naming a key's file pattern in that text reads no key — the
# 2026-10-05 builder brief that was refused for exactly that.
_DATA_SINK_OPENER = re.compile(
    r"(?:^|&&|;|\|\|)\s*(?:cat\s*>>?\s*\S+\s*<<-?\s*(['\"])\w+\1"
    r"|cat\s*<<-?\s*(['\"])\w+\2\s*>>?\s*\S+"
    r"|tee(?:\s+-a)?\s+\S+(?:\s*>\s*/dev/null)?\s*<<-?\s*(['\"])\w+\3)\s*$")


def strip_data_heredocs(cmd):
    """`cmd` without the bodies of quoted heredocs written straight to a file."""
    def data_body(opener, quoted, body):
        return "" if quoted and _DATA_SINK_OPENER.search(opener) else None
    return _rewrite_heredocs(cmd, data_body)
