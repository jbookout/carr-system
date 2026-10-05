"""Verify action-specific coverage without corpus or benchmark labels."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.rule_delivery_precision import select


def prompt(text):
    return set(select(ROOT, {'hook_event_name': 'UserPromptSubmit', 'prompt': text}, [], []))


def tool(name, args):
    return set(select(ROOT, {'hook_event_name': 'PreToolUse', 'tool_name': name,
                             'tool_input': args}, [], []))


assert {'2b66211d', 'c20dc3d5'} <= prompt('Do an independent adversarial review of the PR. Report evidence for every finding.')
assert {'4a53ff82', 'a7784a18', 'e65efc68'} <= prompt('Implement the hook in a worktree and deliver one PR with negative tests.')
assert 'ca841807' in tool('mcp__carr__log_decision', {'title': 'An operating decision'})
assert 'ca841807' not in tool('mcp__carr__read_doctrine', {'document': 'engineering-workflow'})
assert {'4a9188f3', 'bbffc139'} <= prompt('No, that is wrong. The system should never pause its work on a login.')
assert 'e65efc68' not in prompt('Read the hook file and explain the parser.')
config = json.loads((ROOT / 'out/orch/ruleprecision/candidate-config.json').read_text())
payload = {'hook_event_name': 'UserPromptSubmit', 'prompt': 'No, that is wrong. The system should never pause its work on a login.'}
assert {'4a9188f3', 'bbffc139'} <= set(select(ROOT, payload, [], [], config))
assert '113b3833' not in tool('mcp__carr__standing_context', {'detail': 'boot', 'page': 1})
assert '113b3833' in tool('Read', {'file_path': 'lib/acceptance_checks.py'})
assert 'ca841807' not in tool('Bash', {'command': 'printf "%s" "./run.sh call log-decision"'})
assert 'c20dc3d5' not in prompt('<task-notification><summary>Subagent "Renderer" finished</summary><result>Report. PR: ...</result></task-notification>')
assert 'c20dc3d5' not in prompt('<task-notification><summary>Background job "Wait for CI" finished</summary><result>Completed with exit status zero and no other result evidence available.</result></task-notification>')
assert 'c20dc3d5' in prompt('<task-notification><summary>Subagent "Review Engineer" finished</summary><result>REQUEST_CHANGES. The claim is false because the parser drops the source version before validation.</result></task-notification>')
assert select(ROOT, {'hook_event_name': 'PreToolUse'}, ['new-rule'], [], {'refine': False, 'add_actions': False}) == ['new-rule']
print('calibration action contracts PASS')
