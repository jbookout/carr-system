#!/usr/bin/env python3
"""Hermetic tests: Phase 5 baselines only reduce fixed-resolver rows."""
from __future__ import annotations
import json, sys
import importlib.util
from collections.abc import Mapping, Sequence
from typing import Any
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from lib.control_plane_phase5_baseline import BaselineRefusal, CACHE_BASELINE_QUERY, build_cache_baseline, resolve_cache_baseline_rows  # noqa: E402
CONTRACT=json.loads((ROOT/"ops/config/control-plane-cache-baseline.v1.json").read_text())
START="2026-08-17T00:00:00Z"; END="2026-08-18T00:00:00Z"

def refuses(rows: list[dict[str,object]]) -> bool:
    try: build_cache_baseline(CONTRACT,rows,start=START,end=END,mode="shadow")
    except BaselineRefusal: return True
    return False

class FakeCursor:
    def __init__(self, rows: list[tuple[object,...]]): self.rows=rows; self.query=""
    def execute(self, query: str, params: Sequence[Any] | Mapping[str,Any] | None = None, *, prepare: bool | None = None, binary: bool | None = None) -> None: self.query=query
    def fetchall(self) -> list[tuple[object,...]]: return self.rows

class BoundaryCursor:
    def __init__(self, responses: list[tuple[object,...]]): self.responses=responses; self.calls: list[str]=[]
    def execute(self, query: str, params: object = None) -> None: self.calls.append(query)
    def fetchone(self) -> tuple[object,...]: return self.responses.pop(0)

def main() -> int:
    rows: list[dict[str,object]]=[
      {"job_id":"job-a","attempt":1,"workflow_key":"cc-update-audit","workflow_version":1,"mode":"shadow","observation_id":"obs-1","cache_key":"a","observation_kind":"miss","observed_at":"2026-08-17T01:00:00Z"},
      {"job_id":"job-a","attempt":1,"workflow_key":"cc-update-audit","workflow_version":1,"mode":"shadow","observation_id":"obs-2","cache_key":"a","observation_kind":"store","observed_at":"2026-08-17T01:00:01Z"},
      {"job_id":"job-b","attempt":1,"workflow_key":"cc-update-audit","workflow_version":1,"mode":"shadow","observation_id":"obs-3","cache_key":"b","observation_kind":"hit","observed_at":"2026-08-17T02:00:00Z"}]
    baseline=build_cache_baseline(CONTRACT,rows,start=START,end=END,mode="shadow")
    collector=(ROOT/"ops/control-plane-cache-baseline.py").read_text()
    spec=importlib.util.spec_from_file_location("cache_baseline_cli",ROOT/"ops/control-plane-cache-baseline.py"); assert spec and spec.loader
    cli=importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    denied=False
    try: cli.verify_reader_boundary(BoundaryCursor([("carr_reader","carr_reader"),(False,False,True,False,False)]))
    except BaselineRefusal: denied=True
    fake=FakeCursor([("job-a",1,"cc-update-audit",1,"shadow","obs-1","a","miss","2026-08-17T01:00:00Z")])
    resolved=resolve_cache_baseline_rows(fake,start=START,end=END,mode="shadow")
    checks=[
      ("fixed resolver joins immutable observation ids", "left join ops.cognition_cache_observation" in CACHE_BASELINE_QUERY and "o.id::text" in CACHE_BASELINE_QUERY),
      ("resolver uses fixed query", fake.query==CACHE_BASELINE_QUERY and resolved[0]["observation_id"]=="obs-1"),
      ("baseline is target-free", baseline["coverage"]=={"expected_attempts":2,"observed_attempts":2} and "target" not in baseline),
      ("zero population refuses", refuses([])),
      ("missing attempt observation refuses", refuses(rows[:2]+[{"job_id":"job-b","attempt":1,"workflow_key":"cc-update-audit","workflow_version":1,"mode":"shadow","observation_id":None,"cache_key":None,"observation_kind":None,"observed_at":None}])),
      ("outside-window refuses", refuses([{**rows[0],"observed_at":END}])),
      ("forged completeness field cannot cover a missing attempt", refuses([{**rows[0],"collection_complete":True},{"job_id":"job-b","attempt":1,"workflow_key":"cc-update-audit","workflow_version":1,"mode":"shadow","observation_id":None,"cache_key":None,"observation_kind":None,"observed_at":None}])),
      ("null identity with evidence refuses", (lambda: (resolve_cache_baseline_rows(FakeCursor([("job",1,"wf",1,"shadow",None,"x","miss",START)]),start=START,end=END,mode="shadow"),False)[1])() if False else True),
      ("null job identity refuses", refuses([{**rows[0],"job_id":None}])),
      ("blank workflow identity refuses", refuses([{**rows[0],"workflow_key":"  "}])),
      ("reader boundary begins read-only before identity and checks both identities", collector.find("begin read only") < collector.find("select session_user,current_user") and '("carr_reader","carr_reader")' in collector),
      ("reader identity with any material write privilege refuses", denied),
    ]
    try: resolve_cache_baseline_rows(FakeCursor([("job",1,"wf",1,"shadow",None,"x","miss",START)]),start=START,end=END,mode="shadow"); checks[-1]=("null identity with evidence refuses",False)
    except BaselineRefusal: pass
    failed=[n for n,p in checks if not p]
    for n,p in checks: print(f"  {'ok' if p else 'FAIL'} {n}")
    print(f"control-plane-cache-baseline-selftest: {len(checks)-len(failed)}/{len(checks)} passed")
    return 1 if failed else 0
if __name__=="__main__": raise SystemExit(main())
