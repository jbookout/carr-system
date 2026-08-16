#!/usr/bin/env python3
"""Hermetic contracts for the idea/social canonical read-only collectors."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.control_plane_collectors_social import CollectorUnavailable, SocialCanonicalCollector


class Query:
    def __init__(self, values): self.values = values
    def rows(self, name): return self.values.get(name, [])


class Policy:
    def __init__(self, value): self.value = value
    def read_object(self, name):
        if name != 'social_cadence_policy': raise AssertionError(name)
        return self.value


class Receipts:
    def __init__(self, values): self.values = values
    def latest(self, key): return self.values.get(key)


def check(label, value):
    if not value: raise AssertionError(label)
    print('ok:', label)


def main():
    query = Query({
        'idea_bank_oldest_unsurfaced': [{'id':'42','title':'Lease expiry','last_surfaced':None}],
        'social_next_week_sources': [{'source_ref':'content:1'}],
        'social_next_week_coverage': [{'coverage_state':'uncovered'}],
        'social_metric_exports': [{'placement_id':'p1','external_id':'x1','platform':'linkedin',
            'source_observed_at':'2026-08-16T00:00:00Z','metric_kind':'views_count',
            'metric_value':42,'window_current':True,'owned_account':True,
            'placement_in_window':True}],
    })
    policy = Policy({'platforms':['linkedin','x'],'voice_version':1,
                     'cadence':'valid','topic_rotation':'valid','reply_mode':'no_replies'})
    receipts = Receipts({'idea-resurface-monthly': {'receipt_ref':'job:1','created_at':'2026-08-01T00:00:00Z'},
                         'social-batch-weekly': None})
    collector = SocialCanonicalCollector(query, policy, receipts)
    idea = list(collector.collect(builder_key='idea-bank.oldest-unsurfaced', workflow_key='idea-resurface-monthly'))
    check('idea emits canonical refs and immutable receipt state', idea[0]['values']['ideas'] == ['idea:42'] and idea[1]['values']['previous_receipt_state'] == 'present')
    brief = list(collector.collect(builder_key='social.next-week-brief', workflow_key='social-batch-weekly'))
    check('social brief emits runtime fact fields and absent receipt only from reader', brief[0]['values']['reply_mode'] == 'no_replies' and brief[1]['values']['previous_receipt_state'] == 'absent')
    metrics = list(collector.collect(builder_key='social.metrics-exports', workflow_key='social-metrics-pull-weekly'))
    check('metrics emits typed canonical export rows', metrics[0]['values']['platform_exports'][0]['placement_id'] == 'p1')
    for builder, workflow in [('idea-bank.oldest-unsurfaced','idea-resurface-monthly'), ('social.next-week-brief','social-batch-weekly'), ('social.metrics-exports','social-metrics-pull-weekly')]:
        bad = SocialCanonicalCollector(Query({}), policy, receipts)
        try: list(bad.collect(builder_key=builder, workflow_key=workflow))
        except CollectorUnavailable: refused = True
        else: refused = False
        check(builder + ' refuses missing canonical rows', refused)
    bad_receipt = SocialCanonicalCollector(query, policy, Receipts({'idea-resurface-monthly': {'receipt_ref':'', 'created_at':'x'}}))
    try: list(bad_receipt.collect(builder_key='idea-bank.oldest-unsurfaced', workflow_key='idea-resurface-monthly'))
    except CollectorUnavailable: refused = True
    else: refused = False
    check('receipt state refuses nonimmutable receipt shape', refused)
    return 0


if __name__ == '__main__': raise SystemExit(main())
