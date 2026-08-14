#!/usr/bin/env python3
"""Fail-closed, exact-SHA GitHub merge pilot for narrow unattended changes.

The GitHub workflow keeps this file on the protected default branch whenever it
holds a write-capable token. Pull-request code runs only in a separate read-only
job. This module uses the GitHub REST API directly so there is one decision
engine for scheduled and manual runs and no shell-built merge command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


class PolicyError(ValueError):
    pass


class PilotRefusal(RuntimeError):
    pass


SAFE_ALLOWED_PREFIXES = {
    "mcp-server/test/",
    "control-room/fixtures/",
    "workspace/fixtures/",
}
HARD_FORBIDDEN_PREFIXES = (
    ".github/", "hooks/", "ops/config/", "ops/githooks/", "bin/",
    "migrations/", "db/", "mcp-server/src/", "control-room/contracts/",
    "workspace/contracts/",
)
HARD_FORBIDDEN_EXACT = {
    "requirements.txt", "requirements.lock", "package.json", "package-lock.json",
    "mcp-server/package.json", "mcp-server/package-lock.json",
    "mcp-server/wrangler.toml", "ops/automerge_pilot.py",
    "ops/automerge-pilot-selftest.py",
}
SENSITIVE_PATH_TOKENS = {
    "auth", "identity", "capability", "permission", "secret", "credential",
    "deploy", "release", "migration", "schema", "approval", "policy",
    "security", "incident", "council",
}
BLOCKING_LABEL_TOKENS = {
    "do-not-merge", "security", "privacy", "incident", "migration",
    "decision", "council", "release", "deployment",
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def parse_time(value):
    if not isinstance(value, str):
        raise PolicyError("timestamp must be a string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError as exc:
        raise PolicyError(f"invalid timestamp: {value}") from exc


def _business_days(start, end):
    count = 0
    cursor = start.date()
    while cursor < end.date():
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def _canonical_digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def validate_policy(value, now=None):
    now = now or datetime.now(timezone.utc)
    required = {
        "schema_version", "policy_id", "status", "owner", "repository",
        "base_branch", "merge_method", "required_label", "allowed_approvers",
        "required_check", "required_repository_control", "commit_title_prefix",
        "pilot", "change_limits", "post_merge",
    }
    missing = sorted(required - set(value))
    if missing:
        raise PolicyError("missing policy fields: " + ", ".join(missing))
    if value["schema_version"] != 1 or value["status"] != "experimental":
        raise PolicyError("only experimental schema version 1 is supported")
    if value["repository"] != "jbookout/carr-system" or value["base_branch"] != "main":
        raise PolicyError("pilot is bound to jbookout/carr-system main")
    if value["merge_method"] != "squash":
        raise PolicyError("only squash merge is permitted")
    if value["commit_title_prefix"] != "[carr-overnight-automerge-pilot-v1]":
        raise PolicyError("pilot commit marker cannot change")
    if value["allowed_approvers"] != ["jbookout"]:
        raise PolicyError("only the repository owner may approve this pilot")
    if value["required_check"].get("name") != "ops/ci.sh --strict":
        raise PolicyError("required check must remain ops/ci.sh --strict")
    if value["required_check"].get("app_slug") != "github-actions":
        raise PolicyError("required check source must remain github-actions")
    if value["required_check"].get("workflow_file") != "ci.yml":
        raise PolicyError("required workflow must remain ci.yml")
    if value["required_check"].get("workflow_name") != "CI":
        raise PolicyError("required workflow name must remain CI")
    repository_control = value["required_repository_control"]
    if repository_control != {
        "ruleset_id": 20824501,
        "strict_required_status_checks_policy": True,
    }:
        raise PolicyError("pilot requires strict protected-main ruleset 20824501")

    start = parse_time(value["pilot"].get("not_before"))
    end = parse_time(value["pilot"].get("expires_at"))
    if end <= start:
        raise PolicyError("pilot expiry must follow its start")
    if _business_days(start, end) > 5:
        raise PolicyError("pilot window exceeds five business days")
    if now >= end:
        raise PolicyError("pilot policy expired")
    if value["pilot"].get("max_total_merges") not in (1, 2, 3):
        raise PolicyError("pilot merge cap must be between one and three")
    if value["pilot"].get("max_candidates_per_run") != 1:
        raise PolicyError("pilot must allow exactly one candidate per run")

    limits = value["change_limits"]
    allowed = set(limits.get("allowed_prefixes", []))
    forbidden = tuple(limits.get("forbidden_prefixes", [])) + HARD_FORBIDDEN_PREFIXES
    for candidate in allowed:
        if any(candidate.startswith(item) or item.startswith(candidate) for item in forbidden):
            raise PolicyError(f"allowed prefix overlaps forbidden prefix: {candidate}")
    if not allowed or not allowed.issubset(SAFE_ALLOWED_PREFIXES):
        raise PolicyError("allowed prefixes exceed the fixed safe set")
    if limits.get("allowed_ops_selftests") is not False:
        raise PolicyError("ops selftest wildcard is not permitted")
    if limits.get("max_files", 0) > 8 or limits.get("max_changed_lines", 0) > 250:
        raise PolicyError("change limits exceed the pilot ceiling")
    return value


def load_policy(path, now=None):
    value = json.loads(Path(path).read_text())
    validate_policy(value, now=now)
    value = dict(value)
    value["digest"] = _canonical_digest({k: v for k, v in value.items() if k != "digest"})
    return value


def _path_allowed(filename, policy):
    limits = policy["change_limits"]
    if filename in HARD_FORBIDDEN_EXACT or filename in set(limits.get("forbidden_exact", [])):
        return False
    if filename.startswith(HARD_FORBIDDEN_PREFIXES):
        return False
    if any(filename.startswith(prefix) for prefix in limits.get("forbidden_prefixes", [])):
        return False
    parts = {part.lower() for part in re.split(r"[/_.-]+", filename) if part}
    if parts & SENSITIVE_PATH_TOKENS:
        return False
    if filename in set(limits.get("allowed_exact", [])):
        return True
    if any(filename.startswith(prefix) for prefix in limits.get("allowed_prefixes", [])):
        return True
    return False


def _latest_reviews(reviews):
    latest = {}
    for review in reviews:
        login = ((review.get("user") or {}).get("login") or "").lower()
        submitted = review.get("submitted_at") or ""
        if login and (login not in latest or submitted >= (latest[login].get("submitted_at") or "")):
            latest[login] = review
    return latest


def _required_check(policy, snapshot):
    required = policy["required_check"]
    matches = [
        run for run in snapshot.get("check_runs", [])
        if run.get("name") == required["name"]
        and (run.get("app") or {}).get("slug") == required["app_slug"]
        and run.get("head_sha") == snapshot["head"]["sha"]
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda run: run.get("completed_at") or "")[-1]


def _required_workflow_run(policy, snapshot):
    required = policy["required_check"]
    matches = [
        run for run in snapshot.get("workflow_runs", [])
        if run.get("name") == required["workflow_name"]
        and run.get("path") == f".github/workflows/{required['workflow_file']}"
        and run.get("head_sha") == snapshot["head"]["sha"]
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda run: run.get("updated_at") or "")[-1]


def evaluate_candidate(policy, snapshot, *, now=None, prior_merges=None, enabled=False):
    now = now or datetime.now(timezone.utc)
    prior_merges = prior_merges or []
    reasons = []
    start = parse_time(policy["pilot"]["not_before"])
    end = parse_time(policy["pilot"]["expires_at"])
    if not enabled:
        reasons.append("pilot_disabled")
    if now < start:
        reasons.append("pilot_not_started")
    if now >= end:
        reasons.append("pilot_expired")
    if len(prior_merges) >= policy["pilot"]["max_total_merges"]:
        reasons.append("pilot_merge_cap_reached")
    if any(item.get("ci_conclusion") != "success" for item in prior_merges):
        reasons.append("prior_pilot_merge_unhealthy")

    if snapshot.get("state") != "open":
        reasons.append("pr_not_open")
    if snapshot.get("draft"):
        reasons.append("draft_pr")
    if snapshot.get("base", {}).get("ref") != policy["base_branch"]:
        reasons.append("wrong_base_branch")
    if snapshot.get("mergeable") is None:
        reasons.append("mergeability_unknown")
    elif snapshot.get("mergeable") is not True:
        reasons.append("not_mergeable")
    if snapshot.get("mergeable_state") != "clean":
        reasons.append("mergeable_state_not_clean")
    labels = {str(label).lower() for label in snapshot.get("labels", [])}
    if policy["required_label"].lower() not in labels:
        reasons.append("required_label_missing")
    if any(token in label for token in BLOCKING_LABEL_TOKENS for label in labels):
        reasons.append("blocking_label_present")
    if snapshot.get("review_comments", 0):
        reasons.append("review_comments_present")

    head_sha = snapshot.get("head", {}).get("sha", "")
    if not SHA_RE.fullmatch(head_sha):
        reasons.append("invalid_head_sha")
    if not SHA_RE.fullmatch(snapshot.get("test_merge_sha") or ""):
        reasons.append("invalid_test_merge_sha")
    latest = _latest_reviews(snapshot.get("reviews", []))
    if any(review.get("state") == "CHANGES_REQUESTED" for review in latest.values()):
        reasons.append("changes_requested")

    files = snapshot.get("files", [])
    limits = policy["change_limits"]
    if not files:
        reasons.append("changed_files_missing")
    if len(files) > limits["max_files"]:
        reasons.append("changed_file_limit_exceeded")
    changed_lines = sum(int(item.get("additions") or 0) + int(item.get("deletions") or 0) for item in files)
    if changed_lines > limits["max_changed_lines"]:
        reasons.append("changed_lines_limit_exceeded")
    for item in files:
        if item.get("status") not in {"added", "modified"}:
            reasons.append("unsupported_file_status")
        if item.get("patch") is None:
            reasons.append("binary_or_uninspectable_change")
        if not _path_allowed(item.get("filename", ""), policy):
            reasons.append("changed_path_not_allowed")

    check = _required_check(policy, snapshot)
    completed = None
    if check is None:
        reasons.append("required_check_missing")
    else:
        if check.get("status") != "completed" or check.get("conclusion") != "success":
            reasons.append("required_check_not_successful")
        completed = parse_time(check.get("completed_at")) if check.get("completed_at") else None
        if completed is None or now - completed > timedelta(hours=policy["required_check"]["max_age_hours"]):
            reasons.append("required_check_stale")

    approval_url = None
    marker = f"/carr-automerge {head_sha}"
    allowed_approvers = {item.lower() for item in policy["allowed_approvers"]}
    for comment in snapshot.get("approval_comments", []):
        login = ((comment.get("user") or {}).get("login") or "").lower()
        created = parse_time(comment.get("created_at")) if comment.get("created_at") else None
        if (
            login in allowed_approvers
            and comment.get("author_association") == "OWNER"
            and (comment.get("body") or "").strip() == marker
            and completed is not None
            and created is not None
            and created >= completed
            and now - created <= timedelta(hours=policy["required_check"]["max_age_hours"])
        ):
            approval_url = comment.get("html_url")
            break
    if approval_url is None:
        reasons.append("exact_sha_approval_missing")

    workflow_run = _required_workflow_run(policy, snapshot)
    workflow_completed = None
    if workflow_run is None:
        reasons.append("required_workflow_run_missing")
    else:
        if workflow_run.get("status") != "completed" or workflow_run.get("conclusion") != "success":
            reasons.append("required_workflow_run_not_successful")
        workflow_completed = parse_time(workflow_run.get("updated_at")) if workflow_run.get("updated_at") else None
        if workflow_completed is None or now - workflow_completed > timedelta(hours=policy["required_check"]["max_age_hours"]):
            reasons.append("required_workflow_run_stale")

    approval_floor = max(item for item in (completed, workflow_completed) if item is not None) if any(
        item is not None for item in (completed, workflow_completed)
    ) else None
    if approval_url is not None:
        matching_comments = [
            comment for comment in snapshot.get("approval_comments", [])
            if comment.get("html_url") == approval_url
        ]
        approved_at = parse_time(matching_comments[0]["created_at"]) if matching_comments else None
        if approval_floor is None or approved_at is None or approved_at < approval_floor:
            reasons.append("exact_sha_approval_missing")
            approval_url = None

    unique_reasons = sorted(set(reasons))
    return {
        "eligible": not unique_reasons,
        "reason_codes": unique_reasons,
        "pr_number": snapshot.get("number"),
        "head_sha": head_sha or None,
        "base_sha": snapshot.get("base", {}).get("sha"),
        "test_merge_sha": snapshot.get("test_merge_sha"),
        "changed_files": [item.get("filename") for item in files],
        "required_check_url": check.get("html_url") if check else None,
        "required_workflow_url": workflow_run.get("html_url") if workflow_run else None,
        "approval_url": approval_url,
    }


def select_candidate(decisions, max_candidates=1):
    eligible = sorted((item for item in decisions if item.get("eligible")), key=lambda item: item["pr_number"])
    if len(eligible) > max_candidates:
        return {
            "eligible": False,
            "reason_codes": ["eligible_candidate_count_exceeded"],
            "eligible_pr_numbers": [item["pr_number"] for item in eligible],
            "candidate_decisions": decisions,
        }
    if not eligible:
        return {
            "eligible": False,
            "reason_codes": ["no_eligible_candidate"],
            "candidate_decisions": decisions,
        }
    chosen = dict(eligible[0])
    chosen["candidate_decisions"] = decisions
    return chosen


def verify_checkout_parents(parents, expected_base, expected_head):
    if len(parents) != 3 or set(parents[1:]) != {expected_base, expected_head}:
        raise PilotRefusal("checkout_parent_mismatch")
    return parents[0]


def merge_payload(head_sha, method, title_prefix, number):
    if method != "squash":
        raise PolicyError("only squash merge is permitted")
    if not SHA_RE.fullmatch(head_sha):
        raise PolicyError("conditional merge needs an exact 40-character SHA")
    return {
        "sha": head_sha,
        "merge_method": "squash",
        "commit_title": f"{title_prefix} PR #{number}",
    }


def execute_conditional_merge(api, number, expected_head, expected_base, expected_test_merge, policy, now=None):
    current = api.get_snapshot(number)
    if current.get("head", {}).get("sha") != expected_head:
        raise PilotRefusal("head_sha_changed")
    if current.get("base", {}).get("sha") != expected_base:
        raise PilotRefusal("base_sha_changed")
    if current.get("test_merge_sha") != expected_test_merge:
        raise PilotRefusal("test_merge_sha_changed")
    decision = evaluate_candidate(policy, current, now=now, prior_merges=[], enabled=True)
    if not decision["eligible"]:
        raise PilotRefusal("candidate_no_longer_eligible:" + ",".join(decision["reason_codes"]))
    api.assert_repository_control(policy)
    return api.merge(number, merge_payload(
        expected_head, policy["merge_method"], policy["commit_title_prefix"], number
    ))


class GitHubApi:
    def __init__(self, repo, token, api_url="https://api.github.com"):
        if repo != "jbookout/carr-system":
            raise PolicyError("GitHub client is bound to jbookout/carr-system")
        if not token:
            raise PolicyError("GITHUB_TOKEN is required")
        self.repo = repo
        self.token = token
        self.api_url = api_url.rstrip("/")

    def request(self, method, path, data=None):
        url = self.api_url + path
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("Authorization", "Bearer " + self.token)
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise PilotRefusal(f"github_http_{exc.code}:{detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PilotRefusal("github_transport_error:" + str(exc)[:200]) from exc

    def get(self, path):
        return self.request("GET", path)

    def assert_repository_control(self, policy):
        expected = policy["required_repository_control"]
        ruleset = self.get(f"/repos/{self.repo}/rulesets/{expected['ruleset_id']}")
        reasons = []
        if ruleset.get("enforcement") != "active" or ruleset.get("target") != "branch":
            reasons.append("ruleset_not_active_branch_control")
        includes = (((ruleset.get("conditions") or {}).get("ref_name") or {}).get("include") or [])
        if includes != ["refs/heads/main"]:
            reasons.append("ruleset_main_scope_changed")
        if ruleset.get("bypass_actors"):
            reasons.append("ruleset_bypass_present")
        types = {item.get("type") for item in ruleset.get("rules", [])}
        if not {"deletion", "non_fast_forward"}.issubset(types):
            reasons.append("ruleset_history_protection_missing")
        status_rules = [item for item in ruleset.get("rules", []) if item.get("type") == "required_status_checks"]
        if len(status_rules) != 1:
            reasons.append("ruleset_required_check_shape_changed")
        else:
            params = status_rules[0].get("parameters") or {}
            contexts = [item.get("context") for item in params.get("required_status_checks", [])]
            if params.get("strict_required_status_checks_policy") is not True:
                reasons.append("ruleset_strict_update_missing")
            if contexts != [policy["required_check"]["name"]]:
                reasons.append("ruleset_required_check_changed")
        if reasons:
            raise PilotRefusal("repository_control_mismatch:" + ",".join(sorted(reasons)))
        return ruleset

    def get_check_runs(self, sha):
        encoded = urllib.parse.quote(sha, safe="")
        return self.get(f"/repos/{self.repo}/commits/{encoded}/check-runs?per_page=100").get("check_runs", [])

    def get_workflow_runs(self, sha, workflow="ci.yml"):
        encoded_sha = urllib.parse.quote(sha, safe="")
        encoded_workflow = urllib.parse.quote(workflow, safe="")
        result = self.get(
            f"/repos/{self.repo}/actions/workflows/{encoded_workflow}/runs"
            f"?head_sha={encoded_sha}&per_page=100"
        )
        return result.get("workflow_runs", [])

    def get_all(self, path, max_pages=10):
        items = []
        separator = "&" if "?" in path else "?"
        for page in range(1, max_pages + 1):
            batch = self.get(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(batch, list):
                raise PilotRefusal("github_pagination_shape_invalid")
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise PilotRefusal("github_pagination_limit_reached")

    def get_snapshot(self, number):
        root = f"/repos/{self.repo}"
        pr = self.get(f"{root}/pulls/{number}")
        files = self.get(f"{root}/pulls/{number}/files?per_page=100")
        reviews = self.get(f"{root}/pulls/{number}/reviews?per_page=100")
        comments = self.get(f"{root}/issues/{number}/comments?per_page=100")
        checks = self.get_check_runs(pr["head"]["sha"])
        workflow_runs = self.get_workflow_runs(pr["head"]["sha"])
        return {
            "number": pr["number"],
            "state": pr["state"],
            "draft": pr.get("draft", False),
            "mergeable": pr.get("mergeable"),
            "mergeable_state": pr.get("mergeable_state"),
            "base": {"ref": pr["base"]["ref"], "sha": pr["base"]["sha"]},
            "head": {"sha": pr["head"]["sha"]},
            "test_merge_sha": pr.get("merge_commit_sha"),
            "labels": [item["name"] for item in pr.get("labels", [])],
            "review_comments": pr.get("review_comments", 0),
            "files": files,
            "reviews": reviews,
            "approval_comments": comments,
            "check_runs": checks,
            "workflow_runs": workflow_runs,
        }

    def candidate_numbers(self, base, label):
        pulls = self.get_all(
            f"/repos/{self.repo}/pulls?state=open&base={urllib.parse.quote(base)}"
        )
        return sorted(
            pr["number"] for pr in pulls
            if label.lower() in {item["name"].lower() for item in pr.get("labels", [])}
        )

    def prior_merges(self, policy):
        pulls = self.get_all(
            f"/repos/{self.repo}/pulls?state=closed&base=main&sort=updated&direction=desc"
        )
        start = parse_time(policy["pilot"]["not_before"])
        prior = []
        for pr in pulls:
            if not pr.get("merged_at") or parse_time(pr["merged_at"]) < start:
                continue
            sha = pr.get("merge_commit_sha")
            if not sha:
                continue
            commit = self.get(f"/repos/{self.repo}/commits/{urllib.parse.quote(sha, safe='')}")
            title = (((commit.get("commit") or {}).get("message") or "").splitlines() or [""])[0]
            if not title.startswith(policy["commit_title_prefix"] + " PR #"):
                continue
            runs = self.get_workflow_runs(sha, policy["required_check"]["workflow_file"])
            matches = [
                item for item in runs
                if item.get("name") == policy["required_check"]["workflow_name"]
                and item.get("path") == f".github/workflows/{policy['required_check']['workflow_file']}"
                and item.get("head_sha") == sha
            ]
            latest = sorted(matches, key=lambda item: item.get("updated_at") or "")[-1] if matches else None
            prior.append({
                "number": pr["number"],
                "merge_commit_sha": sha,
                "ci_conclusion": latest.get("conclusion") if latest else None,
            })
        return prior

    def merge(self, number, payload):
        result = self.request("PUT", f"/repos/{self.repo}/pulls/{number}/merge", payload)
        if not result.get("merged") or not result.get("sha"):
            raise PilotRefusal("conditional_merge_refused:" + str(result.get("message") or "unknown"))
        return result

    def branch_head(self, branch):
        return self.get(f"/repos/{self.repo}/branches/{urllib.parse.quote(branch)}")["commit"]["sha"]

    def dispatch_workflow(self, workflow, ref):
        encoded = urllib.parse.quote(workflow, safe="")
        return self.request(
            "POST",
            f"/repos/{self.repo}/actions/workflows/{encoded}/dispatches",
            {"ref": ref},
        )


def _write_outputs(path, values):
    if not path:
        return
    with Path(path).open("a") as fh:
        for key, value in values.items():
            rendered = "" if value is None else str(value).lower() if isinstance(value, bool) else str(value)
            fh.write(f"{key}={rendered}\n")


def _write_summary(path, title, audit):
    if not path:
        return
    with Path(path).open("a") as fh:
        fh.write(f"## {title}\n\n")
        fh.write(f"- Outcome: `{audit.get('outcome', 'unknown')}`\n")
        fh.write(f"- Policy: `{audit.get('policy_id')}` / `{audit.get('policy_digest')}`\n")
        if audit.get("pr_number"):
            fh.write(f"- PR: `#{audit['pr_number']}` at `{audit.get('head_sha')}`\n")
        for reason in audit.get("reason_codes", []):
            fh.write(f"- Refusal: `{reason}`\n")


def _save(path, audit):
    Path(path).write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")


def _base_audit(policy, event):
    return {
        "schema_version": 1,
        "event": event,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "policy_id": policy["policy_id"],
        "policy_digest": policy["digest"],
        "repository": policy["repository"],
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }


def command_plan(args):
    now = datetime.now(timezone.utc)
    policy = load_policy(args.policy, now=now)
    if args.repo != policy["repository"]:
        raise PolicyError("runtime repository does not match policy")
    enabled = str(args.enabled).lower() == "true"
    if not enabled:
        audit = _base_audit(policy, "plan")
        audit.update({
            "eligible": False,
            "outcome": "disabled",
            "reason_codes": ["pilot_disabled"],
            "candidate_decisions": [],
            "prior_pilot_merges": [],
        })
        _save(args.output, audit)
        _write_summary(args.summary, "Overnight merge pilot plan", audit)
        _write_outputs(args.github_output, {"eligible": False, "policy_digest": policy["digest"]})
        return 0
    api = GitHubApi(args.repo, args.token)
    api.assert_repository_control(policy)
    prior = api.prior_merges(policy)
    numbers = api.candidate_numbers(policy["base_branch"], policy["required_label"])
    decisions = [
        evaluate_candidate(policy, api.get_snapshot(number), now=now, prior_merges=prior, enabled=enabled)
        for number in numbers
    ]
    chosen = select_candidate(decisions, policy["pilot"]["max_candidates_per_run"])
    audit = _base_audit(policy, "plan")
    audit.update(chosen)
    audit["prior_pilot_merges"] = prior
    audit["outcome"] = "eligible" if chosen["eligible"] else "refused"
    _save(args.output, audit)
    _write_summary(args.summary, "Overnight merge pilot plan", audit)
    _write_outputs(args.github_output, {
        "eligible": chosen["eligible"],
        "pr_number": chosen.get("pr_number"),
        "head_sha": chosen.get("head_sha"),
        "base_sha": chosen.get("base_sha"),
        "policy_digest": policy["digest"],
        "merge_ref": f"refs/pull/{chosen['pr_number']}/merge" if chosen.get("pr_number") else None,
    })
    return 0


def command_verify_checkout(args):
    parents = args.parents.split()
    merge_sha = verify_checkout_parents(parents, args.expected_base, args.expected_head)
    _write_outputs(args.github_output, {"verified_merge_sha": merge_sha})
    return 0


def _wait_for_post_merge(api, policy, merge_sha):
    timeout = policy["post_merge"]["timeout_seconds"]
    poll = policy["post_merge"]["poll_seconds"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runs = [
            item for item in api.get_workflow_runs(
                merge_sha, policy["required_check"]["workflow_file"]
            )
            if item.get("name") == policy["required_check"]["workflow_name"]
            and item.get("path") == f".github/workflows/{policy['required_check']['workflow_file']}"
            and item.get("head_sha") == merge_sha
        ]
        completed = [item for item in runs if item.get("status") == "completed"]
        if completed:
            latest = sorted(completed, key=lambda item: item.get("updated_at") or "")[-1]
            return {
                "status": "verified" if latest.get("conclusion") == "success" else "failed",
                "conclusion": latest.get("conclusion"),
                "check_url": latest.get("html_url"),
            }
        time.sleep(poll)
    return {"status": "inconclusive", "conclusion": None, "check_url": None}


def command_execute(args):
    now = datetime.now(timezone.utc)
    policy = load_policy(args.policy, now=now)
    if policy["digest"] != args.expected_policy_digest:
        raise PilotRefusal("policy_digest_changed")
    if str(args.enabled).lower() != "true":
        raise PilotRefusal("pilot_disabled")
    api = GitHubApi(args.repo, args.token)
    api.assert_repository_control(policy)
    prior = api.prior_merges(policy)
    numbers = api.candidate_numbers(policy["base_branch"], policy["required_label"])
    decisions = [
        evaluate_candidate(policy, api.get_snapshot(number), now=now, prior_merges=prior, enabled=True)
        for number in numbers
    ]
    chosen = select_candidate(decisions, policy["pilot"]["max_candidates_per_run"])
    if not chosen["eligible"] or chosen.get("pr_number") != args.pr_number:
        raise PilotRefusal("planned_candidate_no_longer_unique_and_eligible")
    if chosen["head_sha"] != args.expected_head:
        raise PilotRefusal("head_sha_changed")
    if chosen["base_sha"] != args.expected_base:
        raise PilotRefusal("base_sha_changed")
    if chosen.get("test_merge_sha") != args.independent_merge_sha:
        raise PilotRefusal("test_merge_sha_changed")

    result = execute_conditional_merge(
        api, args.pr_number, args.expected_head, args.expected_base,
        args.independent_merge_sha, policy, now
    )
    merge_sha = result["sha"]
    audit = _base_audit(policy, "merge")
    audit.update({
        "outcome": "merged_verification_pending",
        "reason_codes": [],
        "pr_number": args.pr_number,
        "head_sha": args.expected_head,
        "base_sha": args.expected_base,
        "independent_merge_sha": args.independent_merge_sha,
        "merge_sha": merge_sha,
        "main_readback_sha": None,
        "post_merge": {"status": "pending", "conclusion": None, "check_url": None},
        "next_action": "wait for exact-SHA post-merge CI",
    })
    _save(args.output, audit)

    try:
        branch_head = api.branch_head(policy["base_branch"])
        audit["main_readback_sha"] = branch_head
        if branch_head != merge_sha:
            raise PilotRefusal("post_merge_main_readback_mismatch")
        # GITHUB_TOKEN-generated events do not recursively start ordinary
        # workflows. workflow_dispatch is the supported exception, so request
        # existing CI explicitly and bind its check run to the merge SHA.
        api.dispatch_workflow("ci.yml", policy["base_branch"])
        post = _wait_for_post_merge(api, policy, merge_sha)
        if post["status"] != "verified":
            raise PilotRefusal("post_merge_verification_not_green")
        audit.update({
            "outcome": "merged_and_verified",
            "post_merge": post,
            "next_action": "none",
        })
    except PilotRefusal as exc:
        audit.update({
            "outcome": "merged_requires_attention",
            "reason_codes": [str(exc).split(":", 1)[0]],
            "next_action": "lane automatically refuses subsequent merges; owner review required",
        })
    _save(args.output, audit)
    _write_summary(args.summary, "Overnight merge pilot result", audit)
    return 0 if audit["outcome"] == "merged_and_verified" else 3


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--policy", default=str(Path(__file__).parent / "config" / "automerge-pilot-policy.v1.json"))
    sub = root.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    plan.add_argument("--enabled", default="false")
    plan.add_argument("--output", required=True)
    plan.add_argument("--github-output")
    plan.add_argument("--summary")
    plan.set_defaults(handler=command_plan)

    verify = sub.add_parser("verify-checkout")
    verify.add_argument("--expected-base", required=True)
    verify.add_argument("--expected-head", required=True)
    verify.add_argument("--parents", required=True)
    verify.add_argument("--github-output")
    verify.set_defaults(handler=command_verify_checkout)

    execute = sub.add_parser("execute")
    execute.add_argument("--repo", required=True)
    execute.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    execute.add_argument("--enabled", default="false")
    execute.add_argument("--pr-number", type=int, required=True)
    execute.add_argument("--expected-head", required=True)
    execute.add_argument("--expected-base", required=True)
    execute.add_argument("--independent-merge-sha", required=True)
    execute.add_argument("--expected-policy-digest", required=True)
    execute.add_argument("--output", required=True)
    execute.add_argument("--summary")
    execute.set_defaults(handler=command_execute)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except (PolicyError, PilotRefusal, json.JSONDecodeError, OSError) as exc:
        print(f"automerge-pilot: REFUSED: {exc}", file=sys.stderr)
        output = getattr(args, "output", None)
        if output:
            audit = {
                "schema_version": 1,
                "event": getattr(args, "command", "unknown"),
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "outcome": "refused",
                "reason_codes": [str(exc).split(":", 1)[0]],
                "repository": getattr(args, "repo", None),
                "pr_number": getattr(args, "pr_number", None),
                "head_sha": getattr(args, "expected_head", None),
                "base_sha": getattr(args, "expected_base", None),
                "run_id": os.environ.get("GITHUB_RUN_ID"),
                "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            }
            _save(output, audit)
            _write_summary(getattr(args, "summary", None), "Overnight merge pilot refusal", audit)
        _write_outputs(getattr(args, "github_output", None), {"eligible": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
