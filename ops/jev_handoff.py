"""jev_handoff.py — Jev reads a message bound for Joe and answers one question:
does it hand him a computer action the session could have done itself?

WHY (Joe, 2026-09-23): "you need to add a jev check that makes you run commands
yourself before you ask me to do it and then decide to run it yourself later.
it seems like this happens a lot. if you can run it yourself you should do that
before you ever ask me."

The same session had just done exactly that: it put `brew trust` in a fence for
Joe, conduct-stop-gate.py blocked the turn, and the session then ran the command
itself without a refusal. The keyword gate caught that one because the command
sat in a shell fence. It misses the same handoff written as prose ("trust the
tap in Homebrew, then install it"), and it had no voice at all in the one place
that is genuinely BEFORE Joe reads anything: an AskUserQuestion call.

WHAT THE CALLERS DO WITH THE ANSWER. A handoff is allowed through only when the
harness actually refused this session that command this turn — the attempt-first
exemption in hooks/conduct_patterns.py. So the judgment never has to decide
whether the session COULD run something; the permission system decides that,
by being asked. Jev only decides whether the text asks Joe to do it.

ACTING, NOT SHADOW, AND WHY THAT IS SAFE HERE. jev_judge.py makes shadow the
default because a gate that blocks on an unvalidated threshold can cost a
partner a blocked afternoon. The cost of a false positive in THIS place is the
session trying a command before asking, which is the behaviour Joe ordered. So
callers act at the high-confidence end only (YES_AT) and every judgment is still
recorded beside the keyword decision, so the agreement log exists from day one.

A LIBRARY. No shebang and no main guard, for the reason jev_judge.py gives.
Never raises: a judgment that cannot be obtained returns None and the caller
falls back to the keyword patterns it already had.
"""

import importlib.util
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KIND = "command_handoff"
YES_AT = 0.80
# Stop hooks carry a 15 s budget in ops/config/hooks.json and the conduct gate
# does other work in the same process. A judgment later than this has cost more
# than the handoff it would have caught.
TIMEOUT_SECONDS = 6.0
MAX_CHARS = 6000

# Only text with some sign of a computer action is worth a paid call. Broad on
# purpose: this is a pre-filter for spend, not the detector.
CUE = re.compile(
    r"`|\b(run|execute|paste|type|install|reinstall|uninstall|terminal|shell|command"
    r"|brew|npm|pip|git|sudo|trust|enable|disable|toggle|settings?|preferences"
    r"|restart|reboot|relaunch|download|click|open)\b", re.I)

QUESTION_TEXT = (
    "Does `message` ask the human reader to personally carry out an action on "
    "their computer (running a terminal command, installing or trusting "
    "software, changing a local setting or config file, restarting a program) "
    "that an AI assistant with shell access on that same computer could have "
    "carried out itself?")
TRUE_TEXT = ("The message hands the human a computer action to perform "
             "themselves, as an instruction or as a question asking them to do it.")
FALSE_TEXT = ("The message only reports results, explains, or asks for a "
              "decision, a fact, or a step only a person can take (typing a "
              "password, Face ID, a browser sign-in, a payment, a signature).")


def _sibling(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(REPO, "ops", f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worth_asking(text):
    return bool(text and text.strip() and CUE.search(text))


def judge(text, *, surface, existing_decision=None, judge_module=None):
    """Probability (0..1) that `text` hands Joe an action, or None.

    `surface` names the caller ("stop" or "ask") for the agreement log, and
    `existing_decision` is what the keyword patterns decided, recorded beside
    Jev's answer. None means no judgment: nothing worth asking, or Jev was
    unavailable. The caller treats None exactly as it treated every message
    before this module existed.
    """
    if not worth_asking(text):
        return None
    try:
        jj = judge_module or _sibling("jev_judge")
    except Exception:
        return None
    try:
        tsc = jj._client()
        questions = {"hands_off": tsc.noul(QUESTION_TEXT, true=TRUE_TEXT,
                                           false=FALSE_TEXT)}
        answer = jj.judge({"message": text[-MAX_CHARS:]}, questions,
                          timeout=TIMEOUT_SECONDS)
    except Exception as exc:
        try:
            jj.record(KIND, surface, None, existing_decision, error=exc)
        except Exception:
            pass
        return None
    jj.record(KIND, surface, answer, existing_decision)
    try:
        prob = float(answer["answers"]["hands_off"]["noul"])
    except Exception:
        return None
    return prob if 0.0 <= prob <= 1.0 else None


def hands_off(text, **kwargs):
    """True only when Jev is confident the text hands Joe an action."""
    prob = judge(text, **kwargs)
    return prob is not None and prob >= YES_AT
