#!/usr/bin/env python3
"""Deterministic, self-contained Job Passport HTML artifact renderer.

This is deliberately a projection renderer, not an agent skill or an authority
source. It consumes an already validated CARR projection and emits static HTML
with native disclosure controls; no network, third-party UI, raw transcript,
or model inference is involved.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

import execution_contract as contract


def _text(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _short(value: str | None) -> str:
    return _text(str(value or "unknown").split(":", 1)[-1].replace("-", " ").replace("_", " "))


def render_job_passport_html(projection: Any, behavior_audit: Any | None = None) -> str:
    """Render only a validated projection into dependency-free accessible HTML."""
    value = contract.validate_observatory_projection(projection)
    lane = value["attempt_lane"]
    staffing = lane["actual_staffing"]
    state = value["state"]
    observation = value["observed_movement"]
    audit = contract.validate_product_behavior_verification(behavior_audit) if behavior_audit is not None else None
    if audit is not None and (audit["binding"]["work_request_id"] != value["work_request_id"] or audit["binding"]["projection_digest"] != value["projection_digest"]):
        raise contract.ContractError("behavior audit does not bind this exact visual projection")
    nodes = "".join(
        f'<button type="button" class="node{ " current" if item["current"] else "" }" '
        f'aria-label="Component {_text(item["component_ref"])}">{_short(item["component_ref"])}'
        f'{" · current" if item["current"] else ""}</button>'
        for item in value["component_map"]
    ) or '<span class="empty">No declared component.</span>'
    timeline = "".join(
        f'<li><b>{item["sequence"]}. {_short(item["event_type"])}</b> · {_text(item["occurred_at"])}'
        f'{" · declared " + _short(item["declared_step_ref"]) if item["declared_step_ref"] else ""}'
        f'{" · observed " + _short(item["observed_component_ref"] or item["observed_resource_ref"]) if item["observed_component_ref"] or item["observed_resource_ref"] else ""}'
        f' · {_text(item["state"])}</li>' for item in value["timeline"]
    ) or "<li>No observed progress event.</li>"
    evidence = "".join(f"<li>{_text(ref)}</li>" for ref in value["evidence_refs"]) or "<li>No evidence reference.</li>"
    deviations = len(observation["deviation_candidates"])
    alignment = (f"{deviations} deviation candidate(s); review required." if deviations else
                 f'{_text(observation["coverage_state"].replace("_", " "))} declared coverage; '
                 f'{_text(observation["activity_fidelity"].replace("_", " "))} observation; '
                 f'uncertainty: {_text(observation["uncertainty"].replace("_", " "))}.')
    if audit is None:
        audit_html = ""
    else:
        counts = {state: sum(item["status"] == state for item in audit["items"]) for state in ("passed", "failed", "blocked", "planned")}
        findings = "".join(
            f'<li><b>{_text(row["severity"])} {_text(row["finding_id"])}:</b> actual {_text(row["actual"])}; expected {_text(row["expected"])}; '
            f'{_text(row["decision"])} / {_text(row["post_fix_status"])}.</li>' for row in audit["findings"]
        ) or "<li>No root-cause-deduped finding is open.</li>"
        audit_html = f'''<details><summary>Behavior audit · {_text(audit["audit_state"].replace("_", " "))}</summary>
<p>{counts["passed"]} passed · {counts["failed"]} failed · {counts["blocked"]} blocked · {counts["planned"]} awaiting live verification.</p>
<ul>{''.join(f'<li>{_text(item["item_id"])} · {_text(item["status"])} · {_text(item["expected"])} · evidence: {_text(", ".join(item["evidence_refs"]))}</li>' for item in audit["items"])}</ul>
<h2>Findings</h2><ul>{findings}</ul></details>'''
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Passport · {_text(value["work_request_id"])}</title>
<style>
:root{{--ground:#07090c;--panel:#101624;--line:#47608c;--text:#e8edf7;--muted:#aebbd5;--ok:#2dd496;--warn:#f08a2d;--bad:#e24b4a;font:15px system-ui,sans-serif}}*{{box-sizing:border-box}}body{{margin:0;background:var(--ground);color:var(--text);line-height:1.45}}main{{max-width:960px;margin:auto;padding:20px}}header,.staffing,.nodes{{display:flex;gap:10px;flex-wrap:wrap;align-items:baseline}}h1{{margin:0;font-size:1.5rem}}.state{{margin-left:auto;color:var(--ok);font-weight:700}}section,details{{border:1px solid var(--line);background:var(--panel);border-radius:10px;padding:12px;margin-top:12px}}h2{{font-size:1rem;margin:0 0 8px}}.meta{{color:var(--muted);font-size:.9rem}}.staffing>div{{border-left:3px solid var(--line);padding-left:8px;min-width:220px}}label{{display:block;color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.06em}}.node{{border:1px solid var(--line);border-radius:999px;background:#17223a;color:var(--text);padding:5px 9px}}.node.current{{border-color:var(--ok)}}button:focus-visible,summary:focus-visible{{outline:3px solid var(--warn);outline-offset:3px}}li{{margin:5px 0}}@media(max-width:560px){{main{{padding:10px}}.state{{margin-left:0;width:100%}}}}@media(prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
</style></head><body><main data-projection-digest="{_text(value["projection_digest"])}" data-work-request-id="{_text(value["work_request_id"])}">
<header><div><h1>Job Passport · {_text(value["work_request_id"])}</h1><p class="meta">Projection {_text(value["projection_digest"])} · canonical state v{value["source_state"]["state_version"]} · generated {_text(value["generated_at"])}</p></div><span class="state">{_text(state["progress"].replace("_", " "))} / {_text(state["verification"].replace("_", " "))}</span></header>
<section><h2>Persistent identity and actual staffing</h2><div class="staffing"><div><label>Persistent profile</label>{_text(lane["persistent_profile"]["display_label"])}</div><div><label>Actual model / harness</label>{_short(staffing["surface"])} · {_short(staffing["model_id"])} · {_short(staffing["harness_id"])}<br><span class="meta">{_text(staffing["adapter_id"])} {_text(staffing["adapter_version"])}</span></div></div></section>
<section><h2>Declared topology and current location</h2><div class="nodes">{nodes}</div><p class="meta">Declared phase: {_text(value["declared_intent"]["plan_step_refs"][0] if value["declared_intent"]["plan_step_refs"] else "not supplied")}</p></section>
<section><h2>Observed movement</h2><p>{alignment}</p><ol>{timeline}</ol></section>
<details><summary>Evidence and verified-checkpoint posture</summary><ul>{evidence}</ul><p>{"Independently verified complete; a replacement can begin only from a verified checkpoint." if state["verification"] == "verified_success" else "Executor evidence is not a canonical promotion; no verified handoff is implied."}</p></details>
{audit_html}
</main></body></html>'''


def build_visual_artifact(envelope: Any, receipt: Any, projection: Any, behavior_audit: Any | None = None) -> tuple[str, dict[str, Any]]:
    """Return HTML and the receipt-compatible provenance descriptor for it."""
    bound = contract.validate_execution_envelope(envelope)
    completed = contract.validate_attempt_receipt(receipt, bound)
    view = contract.validate_observatory_projection(projection)
    if view["work_request_id"] != bound["work_request_id"] or view["attempt_lane"]["attempt_id"] != completed["attempt_id"]:
        raise contract.ContractError("visual artifact projection does not bind its envelope and attempt")
    if view["source_state"]["state_version"] != bound["state_binding"]["state_version"] or view["source_state"]["canonical_record_digest"] != bound["state_binding"]["canonical_record_digest"]:
        raise contract.ContractError("visual artifact projection does not bind its exact canonical state")
    document = render_job_passport_html(view, behavior_audit)
    artifact = {
        "artifact_ref": "artifact:job-passport-html", "media_type": "text/html", "self_contained": True,
        "external_service_dependency": False, "visual_form": "topology",
        "source_binding": {"work_request_id": bound["work_request_id"], "plan_revision_digest": bound["plan_revision"]["digest"],
                           "state_version": bound["state_binding"]["state_version"], "canonical_record_digest": bound["state_binding"]["canonical_record_digest"],
                           "projection_schema_version": view["schema_version"], "projection_digest": view["projection_digest"]},
        "generation": {"generating_attempt_id": completed["attempt_id"], "adapter_configuration_fingerprint": bound["server_binding"]["adapter"]["configuration_fingerprint"],
                       "skill_id": "skill:carr-job-passport-renderer", "skill_version": "v1"},
        "generated_at": view["generated_at"], "freshness": {"state": "stale" if view["state"]["progress"] == "stale" else "fresh", "valid_through": view["generated_at"]},
        "redaction_class": "metadata_only", "content_digest": contract.canonical_digest(document), "evidence_refs": view["evidence_refs"],
        "accessibility": {"color_independent_meaning": True, "reduced_motion_supported": True, "responsive_verified": True, "keyboard_accessible": True},
    }
    contract._validate_visual_artifacts([artifact], bound, completed["attempt_id"])
    return document, artifact


def verify_visual_artifact(document: str, artifact: Any) -> bool:
    """Verify content identity and the no-external-service floor before serving."""
    if not isinstance(document, str) or contract.canonical_digest(document) != artifact.get("content_digest"):
        return False
    forbidden = ("<script src=", "<link href=\"http", "<img src=\"http", "fetch(", "XMLHttpRequest")
    return all(token not in document for token in forbidden)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a deterministic CARR Job Passport HTML fixture")
    parser.add_argument("--envelope", type=Path, required=True); parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True); parser.add_argument("--behavior", type=Path)
    parser.add_argument("--html-out", type=Path, required=True)
    parser.add_argument("--artifact-out", type=Path, required=True)
    args = parser.parse_args()
    behavior = json.loads(args.behavior.read_text()) if args.behavior else None
    document, artifact = build_visual_artifact(json.loads(args.envelope.read_text()), json.loads(args.receipt.read_text()), json.loads(args.projection.read_text()), behavior)
    args.html_out.write_text(document, encoding="utf-8")
    args.artifact_out.write_text(json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return 0 if verify_visual_artifact(document, artifact) else 1


if __name__ == "__main__":
    raise SystemExit(main())
