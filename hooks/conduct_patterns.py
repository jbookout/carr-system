#!/usr/bin/env python3
"""conduct_patterns.py — the SHARED classifier for conduct gates.

ONE HOME for the offload / soft-wait / command-handoff patterns and for the
exemption tests, imported by BOTH gates that need them:

    hooks/conduct-stop-gate.py   Stop      catches it in free prose
    hooks/escalation-gate.py     PreToolUse catches it as an AskUserQuestion

They are two moments on the same behaviour, so they must not carry two copies
of the rule (consolidation bias, rule d367188d; single source of truth, rule
0f38532e). A second copy is how hooks/SETTINGS-BLOCK.md silently drifted to
describing two hooks while four were live.

Underscored filename ON PURPOSE: `conduct-stop-gate.py` is not importable
(hyphens), so the shared half lives here where both can `import conduct_patterns`.

Full rationale for each pattern class, the verification log for blocking Stop,
and the escape-hatch design are documented in conduct-stop-gate.py's docstring.
Fixtures: ops/conduct-gate-selftest.py.
"""
import re

# ─────────────────────────────────────────────────────────────────────────────
# PATTERN CLASSES
# ─────────────────────────────────────────────────────────────────────────────

# (1) Decision offload — a question or menu pointed at the partner.
OFFLOAD = [
    ("should_i",      re.compile(r"\bshould I\b", re.I)),
    ("want_me_to",    re.compile(r"\b(do|would) you want me to\b", re.I)),
    ("would_you_like", re.compile(r"\bwould you like me to\b", re.I)),
    ("which_prefer",  re.compile(r"\bwhich (one )?(do you|would you)\s+(prefer|like|want|pick|choose)", re.I)),
    ("let_me_know",   re.compile(r"\blet me know (if|whether|which|what|how)\b", re.I)),
    ("your_call",     re.compile(r"\b(your call|up to you|your preference|you decide|which way do you want)\b", re.I)),
    ("shall_i",       re.compile(r"\bshall I\b", re.I)),
    ("ok_if_i",       re.compile(r"\b(is it ok if I|are you ok with me|any objection)\b", re.I)),
    ("want_me_prefix", re.compile(r"^\s*want me to\b", re.I | re.M)),
    ("option_menu",   re.compile(r"^\s*(?:\*\*)?(?:Option\s+)?[A-C]\s*[\).:]\s+\S", re.M)),
]

# (3) Soft wait — offload with no question mark. The council's #1 predicted
# bypass; these exist because "?" alone is a losing filter.
SOFT_WAIT = [
    ("hold_until",    re.compile(r"\b(I'?ll|I will|I am going to|I'?m going to)\s+(hold|wait|pause|stand by|hold off)\b[^.]{0,60}\b(you|your|joe)\b", re.I)),
    ("pending_your",  re.compile(r"\b(pending|awaiting|waiting (on|for))\s+(your|joe'?s)\s+(input|answer|call|decision|preference|approval|confirmation|go[- ]ahead|reply|response|direction|sign[- ]?off)", re.I)),
    ("confirm_before", re.compile(r"\b(confirm|approve|sign off|weigh in|green[- ]?light)\b[^.]{0,40}\bbefore I\b", re.I)),
    ("not_proceeding", re.compile(r"\b(not|won'?t|will not) (proceed|move|continue|go ahead)\b[^.]{0,50}\b(without|until)\b[^.]{0,30}\b(you|your|joe)\b", re.I)),
    ("say_the_word",  re.compile(r"\b(say the word|just say|give me the (go|nod)|tell me to)\b", re.I)),
    ("parked_pending", re.compile(r"\bpark(ed|ing)?\b[^.]{0,40}\b(you|your|joe)\b", re.I)),
]

# (2) Command handoff — the e313a3ca failure, in every costume the council named.
FENCE = re.compile(r"```[ \t]*(bash|sh|zsh|shell|console|terminal|command)\b", re.I)
# A fenced block whose first token is a command the session already holds.
CMD_WORDS = (r"git|npm|npx|pnpm|yarn|node|python3?|pip3?|brew|curl|psql|"
             r"chmod|mkdir|cd|ls|cat|make|docker|wrangler|launchctl|"
             r"\./run\.sh|run\.sh|bin/|\./bin/")
BARE_FENCE_CMD = re.compile(r"```[ \t]*\n[ \t]*(?:\$\s*)?(?:" + CMD_WORDS + r")\b", re.I)
HANDOFF_PROSE = [
    ("run_this",      re.compile(r"\b(run|execute) (this|these|the following|it)\b", re.I)),
    ("paste_this",    re.compile(r"\bpaste (this|these|it|the following)\b", re.I)),
    ("in_terminal",   re.compile(r"\b(in|open|from) (your )?(the )?(terminal|iterm|shell|command line)\b", re.I)),
    ("you_run",       re.compile(r"\byou'?(ll| will| can| should) (need to )?run\b", re.I)),
    ("go_ahead_run",  re.compile(r"\bgo ahead and run\b", re.I)),
]

# ─────────────────────────────────────────────────────────────────────────────
# EXEMPTIONS — read off the HUMAN's own turn, never self-granted.
# ─────────────────────────────────────────────────────────────────────────────
HUMAN_WANTS_COMMAND = re.compile(
    r"\b(show|give|paste|what'?s|what is|write)\b[^.?!]{0,40}\b(command|script|snippet|one[- ]liner|syntax)\b"
    r"|\bhow do I (run|install|start|build)\b"
    r"|\bi'?ll run (it|this|them)\b"
    r"|\b(don'?t|do not) run\b", re.I)

HUMAN_WANTS_CHOICE = re.compile(
    r"\b(what are (my|the) options|lay out the options|give me options|which (would|do) you recommend"
    r"|what do you (think|recommend|suggest)|ask me|my call|let me (decide|choose|pick)"
    r"|walk me through|talk me through|/options|/decide|/crux|/redteam|/premortem|/council)\b", re.I)

# A protected-class decision genuinely belongs to Joe (rule aa411351) and must
# still be able to reach him. External-effect vocabulary.
#
# MONEY IS DETECTED BY THE FIGURE AS WELL AS BY THE WORD. The keyword list alone
# missed "Should I let it renew at $240?" in the selftest — a plain spend
# question carrying none of spend/pay/invoice/purchase. A currency amount, a
# subscription/renewal/plan/rate word, or a per-month/per-year unit is itself
# the money signal. Grok's chair made the general form of this point: for a
# broker, money is not only payment rails, it is any number that binds.
PROTECTED = re.compile(
    r"\b(client|prospect|landlord|listing agent|tenant|vendor|broker|doctor|practice owner"
    r"|LOI|letter of intent|PSA|lease|proposal|counter|RFP"
    r"|send|email|publish|post|tweet|linkedin|facebook|instagram"
    r"|spend|pay|paid|invoice|budget|purchase|fee|commission|pricing"
    r"|subscription|subscribe|renews?|renewal|billing"
    r"|delete|destroy|drop table|force[- ]push|revoke)\b"
    r"|[$£€]\s?\d"
    r"|\b\d+\s?(usd|dollars?)\b"
    r"|\b(per|a)\s(month|year|seat|user)\b", re.I)
