#!/usr/bin/env python3
"""Behavior checks for the opt-in, pure precision selector."""
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lib.rule_delivery_precision import select  # noqa: E402


def tool(name, arguments):
    return {"hook_event_name": "PreToolUse", "tool_name": name, "tool_input": arguments}


def prompt(text):
    return {"hook_event_name": "UserPromptSubmit", "prompt": text}


def main():
    cfg = {"refine": True, "add_actions": True}
    assert select(REPO, prompt("Review the rule selector"), ["7e9739f2"], ["7e9739f2"], cfg) == []
    assert "81709f57" not in select(REPO, tool("Agent", {"prompt": "Implement one bounded fix"}), ["81709f57"], [], cfg)
    assert "81709f57" in select(REPO, tool("Agent", {"prompt": "Serve on the red team panel with a distinct lens"}), ["81709f57"], [], cfg)
    assert "8aefcdce" not in select(REPO, tool("Agent", {"prompt": "Run Python source checks"}), ["8aefcdce"], [], cfg)
    assert "8aefcdce" in select(REPO, tool("Agent", {"prompt": "Read Outlook calendar evidence"}), ["8aefcdce"], [], cfg)
    assert "86647daf" not in select(REPO, tool("Bash", {"command": "git checkout -b own-branch"}), ["86647daf"], [], cfg)
    assert "86647daf" in select(REPO, tool("Bash", {"command": "git commit -F /tmp/message"}), ["86647daf"], [], cfg)
    assert "bc9188b4" in select(REPO, tool("Bash", {"command": "git push origin HEAD:feature"}), [], [], cfg)
    assert "bc9188b4" not in select(REPO, tool("Bash", {"command": "rg 'git push' ops"}), [], [], cfg)
    assert "bc9188b4" not in select(REPO, tool("Bash", {"command": "printf 'git push'"}), [], [], cfg)
    assert "4a53ff82" in select(REPO, tool("Edit", {"file_path": "lib/module.py", "old_string": "old", "new_string": "new"}), [], [], cfg)
    assert "4a53ff82" not in select(REPO, tool("Read", {"file_path": "lib/module.py"}), [], [], cfg)
    assert "ede4c735" not in select(REPO, prompt("Review our internal-only engineering report"), ["ede4c735"], [], cfg)
    assert "ede4c735" in select(REPO, prompt("Draft prospect-visible outreach copy"), ["ede4c735"], [], cfg)
    assert "49533583" in select(REPO, prompt("Read Joe's calendar meetings"), [], [], cfg)
    assert "49533583" not in select(REPO, prompt("Explain how Python email parsing works"), [], [], cfg)
    assert select(REPO, prompt("Anything"), ["ffffffff"], [], cfg) == ["ffffffff"]
    original = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git push"}}
    before = repr(original)
    select(REPO, original, ["86647daf"], [], cfg)
    assert repr(original) == before
    print("rule-delivery-precision-selftest: pure action/negative/boot checks passed")


if __name__ == "__main__":
    main()
