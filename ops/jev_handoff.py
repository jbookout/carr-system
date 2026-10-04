"""Exact action/capability evidence for handoffs. Ambiguous prose needs review."""
def evaluate_handoff(evidence):
    """Decide from the exact attempted action, never inferred message intent.

    A timeout is not a denial. Available capability and permission with no
    matching attempt establish unattempted work. Unknown evidence abstains.
    """
    evidence = evidence if isinstance(evidence,dict) else {}
    action = evidence.get("action")
    if not isinstance(action,str) or not action.strip():
        return {"status":"needs_review","reason":"exact attempted action required"}
    attempts = [row for row in evidence.get("attempts",[]) if isinstance(row,dict) and row.get("action") == action]
    if attempts and attempts[-1].get("status") in {"permission_denied","human_auth_required"}:
        return {"status":"human_required","action":action,"reason":"matching action denial"}
    if attempts:
        status = "attempted" if attempts[-1].get("status") == "completed" else "needs_review"
        return {"status":status,"action":action,"reason":"matching attempt; throughput errors are not denials"}
    if evidence.get("capability") == "available" and evidence.get("permission") == "allowed":
        return {"status":"unattempted","action":action,"reason":"available permitted action has no matching attempt"}
    return {"status":"needs_review","action":action,"reason":"capability or permission evidence missing"}


def judge(text, *, surface, existing_decision=None, judge_module=None, evidence=None):
    # Kept for the two real hook callers. None means the prose is ambiguous;
    # their existing exact-command/denial predicates remain authoritative.
    result = evaluate_handoff(evidence)
    if result["status"] == "unattempted":
        return 1.0
    if result["status"] in {"attempted","human_required"}:
        return 0.0
    return None


def hands_off(text, **kwargs):
    return judge(text,**kwargs) == 1.0
