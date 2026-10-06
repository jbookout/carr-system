import argparse
import json
from pathlib import Path
import re
import sys

BOARD_LIMIT = 300
ACTION = r"(?:replace|repair|restore|update|add|implement|wire|migrate|backfill|change|remove|rename|repoint|fix|inspect|validate)"
LABEL = re.compile(r"(?:\*\*)?\b(?:THE\s+FIX|FIX)(?:\*\*)?\s*(?::|\bis\b)\s*(.+)", re.I)
CONCRETE = re.compile(rf"^({ACTION})\s+(.+)$", re.I)


def named_fix(body):
    for paragraph in body.splitlines():
        paragraph = paragraph.strip()
        labelled = LABEL.search(paragraph)
        candidate = labelled.group(1).strip() if labelled else paragraph
        match = CONCRETE.match(candidate)
        if not match:
            continue
        target = match.group(2).strip()
        if len(target) < 12 or len(target.split()) < 3:
            continue
        if re.fullmatch(r"(?:the |this |a )?(?:bug|problem|issue|failure|thing)\.?", target, re.I):
            continue
        return candidate
    return None


def collect(read):
    board = read("loop-board", {"kind": "open_loop", "owner": "claude", "status": "open", "limit": BOARD_LIMIT})
    if (not isinstance(board, dict) or type(board.get("count")) is not int
            or not isinstance(board.get("loops"), list) or board["count"] != len(board["loops"])):
        raise ValueError("loop-board did not return the full-row board contract")
    output = []
    for candidate in board["loops"]:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("number"), str):
            raise ValueError("loop-board entry lacks its loop number")
        if not isinstance(candidate.get("owner"), str):
            raise ValueError("loop-board entry lacks its owner")
        if candidate.get("kind") != "open_loop" or candidate["owner"].lower() != "claude" or candidate.get("status") != "open":
            continue
        result = read("read-loop", {"number": candidate["number"], "kind": "open_loop"})
        row = result.get("loop") if isinstance(result, dict) else None
        if (not isinstance(row, dict) or row.get("number") != candidate["number"]
                or not isinstance(row.get("body"), (str, type(None)))
                or "body" not in row or type(row.get("version")) is not int
                or not isinstance(row.get("owner"), str)):
            raise ValueError("read-loop did not return the requested loop with its body and version")
        if row.get("kind") != "open_loop" or row.get("owner", "").lower() != "claude" or row.get("status") != "open":
            continue
        fix = named_fix(row["body"] or "")
        if fix:
            output.append({"number": row["number"], "kind": row["kind"], "version": row["version"],
                           "title": row.get("title") or candidate.get("label"), "body": row["body"], "fix": fix,
                           "domain": row.get("domain"), "blocker_class": row.get("blocker_class"),
                           "blocker_detail": row.get("blocker_detail")})
    return {"schema": "routine-builder-loop-feed.v1", "count": len(output), "loops": output,
            "possibly_truncated": board["count"] >= BOARD_LIMIT,
            "source": "loop-board + read-loop", "writes": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only JSON of open Claude-owned loops with a concrete fix.")
    parser.add_argument("--fixture", type=Path, help="JSON with board and details keyed by loop number; never accesses live records")
    args = parser.parse_args(argv)
    if args.fixture:
        fixture = json.loads(args.fixture.read_text())
        def read(verb, values):
            if verb == "loop-board": return fixture["board"]
            if verb == "read-loop": return fixture["details"][values["number"]]
            raise ValueError("unsupported fixture read")
    else:
        from lib.record_call import call_verb
        def read(verb, values):
            reply = call_verb(verb, values)
            if not reply.ok:
                raise RuntimeError(f"{verb} read failed")
            return reply.reply
    try:
        print(json.dumps(collect(read), sort_keys=True, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, RuntimeError) as exc:
        print(f"loop-feed: {type(exc).__name__}: read unavailable or malformed", file=sys.stderr)
        return 1
