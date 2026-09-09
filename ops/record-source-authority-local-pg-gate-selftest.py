#!/usr/bin/env python3
"""No database connections: adversarial boundary tests for the local F01 gate."""
import importlib.util
import json
import contextlib
import io
import re
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

path = Path(os.environ.get('F01_GATE_TEST_MODULE', str(Path(__file__).with_name(
    'record-source-authority-local-pg-gate.py'))))
spec = importlib.util.spec_from_file_location('f01_gate', path)
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)


def identity_row(user, **overrides):
    """What verify_connections reads back for one authenticated principal."""
    row = dict(db='f01_gate_test', session=user, current=user, address='127.0.0.1',
               port=5432, owner='f01_owner', super=False, createrole=False, createdb=False,
               replication=False, bypassrls=False, strings='on',
               authority=user.startswith('carr_authority_'))
    row.update(overrides)
    return json.dumps(row)


class PreflightPsql:
    """A cluster that answers every verify_connections query the boring way.

    Subclasses override exactly the one answer under test, so a test that expects
    a refusal cannot be satisfied by an unrelated refusal — notably the
    carr_authority bootstrap check, which every one of these preflights now
    passes through first.
    """

    owner_role = 'f01_owner'
    dsn = 'postgresql://127.0.0.1:5432/f01_gate_test'
    row_overrides: dict = {}
    memberships = '[]'

    def scalar(self, sql, user=None):
        if "rolname='carr_authority'" in sql:
            return '1'
        if 'WITH RECURSIVE' in sql:
            return self.memberships
        return identity_row(user, **self.row_overrides)


class BoundaryTests(unittest.TestCase):
    def test_explicit_loopback(self):
        for host in ('127.0.0.1', '[::1]'):
            self.assertEqual(gate.assert_disposable_dsn(
                f'postgresql://{host}:5432/f01_gate_test', 'f01_gate_test'), 'f01_gate_test')

    def test_dsn_redirects_refuse(self):
        base = 'postgresql://127.0.0.1:5432/f01_gate_test'
        for tail in ('?host=remote.example', '?hostaddr=10.1.2.3', '?dbname=other',
                     '?service=alternate', '?options=-crole%3Downer', '?user=other',
                     '#ignored', '?', '#'):
            with self.subTest(tail=tail), self.assertRaises(gate.GateRefusal):
                gate.assert_disposable_dsn(base + tail, 'f01_gate_test')

    def test_implicit_connections_and_credentials_refuse(self):
        for uri in ('postgresql:///f01_gate_test',
                    'postgresql://localhost:5432/f01_gate_test',
                    'postgresql://127.0.0.1/f01_gate_test',
                    'postgresql://alice@127.0.0.1:5432/f01_gate_test',
                    'postgresql://alice:@127.0.0.1:5432/f01_gate_test',
                    'postgresql://127.0.0.1:5432//f01_gate_test',
                    'postgresql://127.0.0.1:abc/f01_gate_test',
                    'postgresql://127.0.0.1:5432/f01_gate_%74est'):
            with self.subTest(uri=uri), self.assertRaises(gate.GateRefusal):
                gate.assert_disposable_dsn(uri, 'f01_gate_test')

    def test_confirmation_exact(self):
        with self.assertRaises(gate.GateRefusal):
            gate.assert_disposable_dsn('postgresql://127.0.0.1:5432/f01_gate_test', 'different')

    def test_pg_environment_cannot_redirect_or_supply_password(self):
        with patch.dict(os.environ, {'PGHOST':'remote', 'PGPORT':'111', 'PGUSER':'owner',
                                    'PGPASSWORD':'secret', 'PGOPTIONS':'-c role=owner',
                                    'PGSERVICE':'foo', 'PGPASSFILE':'/secret'}, clear=True):
            env = gate.connection_env()
        for key in ('PGHOST','PGPORT','PGUSER','PGPASSWORD','PGOPTIONS','PGSERVICE'):
            self.assertNotIn(key, env)
        self.assertEqual(env['PGPASSFILE'], os.devnull)
        self.assertEqual(env['PGSERVICEFILE'], os.devnull)

    def test_real_identity_mismatch_before_writes(self):
        class FakePsql(PreflightPsql):
            row_overrides = {'db': 'different_database'}
        with self.assertRaises(gate.GateRefusal) as caught:
            gate.verify_connections(FakePsql(), 'f01_gate_test')
        self.assertIn('identity mismatch', str(caught.exception))

    def test_principal_preflight_rejects_ordinary_authority(self):
        class FakePsql(PreflightPsql):
            row_overrides = {'authority': True}
        with self.assertRaises(gate.GateRefusal) as caught:
            gate.verify_connections(FakePsql(), 'f01_gate_test')
        self.assertIn('inherits authority', str(caught.exception))

    def test_nonstandard_string_escaping_refuses(self):
        """sql_literal doubles quotes; that is only sufficient while backslashes
        are literal, so the assumption is checked rather than hoped for."""
        class FakePsql(PreflightPsql):
            row_overrides = {'strings': 'off'}
        with self.assertRaises(gate.GateRefusal) as caught:
            gate.verify_connections(FakePsql(), 'f01_gate_test')
        self.assertIn('standard_conforming_strings', str(caught.exception))

    def test_stale_refusal_is_exact(self):
        self.assertTrue(gate.exact_stale_state_refusal(
            'ERROR:  40001: f01_stale_current_state: current differs\nCONTEXT: foo'))
        for text in ('ERROR:  42501: f01_authority_principal_refused',
                     'ERROR:  40001: f01_stale_policy', 'ERROR:  syntax error',
                     '{"outcome":"refuse"}', 'f01_stale_current_state'):
            with self.subTest(text=text):
                self.assertFalse(gate.exact_stale_state_refusal(text))

    def test_race_requires_accepted_transitions(self):
        args = ['refuse','deal','amount','policy','state',None,None,None,None,None,'key','digest']
        with self.assertRaises(gate.GateRefusal):
            gate.race_statement(args)
        args[0] = 'accept'
        args[5] = {'record': {'value': "x'; SELECT 1; --"}}
        sql = gate.race_statement(args)
        self.assertIn("'accept','deal','amount'", sql)
        self.assertIn("x''; SELECT 1; --", sql)
        self.assertIn('::jsonb', sql)

    # ---- the transport shape between the fixture and this gate ----
    #
    # The fixture emits F01_RACE_REQUEST as raw JSON VALUES and this gate does
    # the quoting. The three tests below pin that contract from both ends,
    # because the failure mode when they disagree is silent in the worst way:
    # the strongest proof in the whole gate simply never runs.

    FIXTURE_SHAPED_REQUEST = [
        'accept', 'deal', 'commission_amount',
        'sha256:' + 'aa' * 32, 'sha256:' + 'bb' * 32,
        {'record_kind': 'stored_field_state'},
        {'record_kind': 'stored_state_transition', 'alone_sufficient': False},
        {'record_kind': 'stored_source_event'},
        {'record_kind': 'stored_mutation_receipt'},
        None,
        'syn-gate-race-a', 'sha256:' + 'cc' * 32]

    def test_the_emitted_fixture_shape_renders_the_exact_call(self):
        sql = gate.race_statement(list(self.FIXTURE_SHAPED_REQUEST))
        self.assertTrue(sql.startswith(
            "SELECT ops.f01_apply_observation('accept','deal','commission_amount',"
            f"'sha256:{'aa' * 32}','sha256:{'bb' * 32}',"), sql)
        # The four envelopes are JSON-encoded and cast exactly once each.
        self.assertEqual(sql.count('::jsonb'), 4)
        self.assertIn('\'{"record_kind": "stored_field_state"}\'::jsonb', sql)
        # A JSON null reconciliation item is a BARE SQL NULL, never the text 'NULL'.
        self.assertIn(f",NULL,'syn-gate-race-a','sha256:{'cc' * 32}')", sql)
        self.assertNotIn("'NULL'", sql)
        # The 13-argument form carries diagnostics as the sixth jsonb parameter.
        with_diagnostics = list(self.FIXTURE_SHAPED_REQUEST) + [{'note': 'synthetic'}]
        self.assertTrue(gate.race_statement(with_diagnostics).endswith(
            '\'{"note": "synthetic"}\'::jsonb)'))

    def test_pre_rendered_sql_arguments_are_refused_by_name(self):
        """The regression that mattered: a fixture emitting quote_literal()
        fragments. 'accept' with its own quotes attached is not the word
        'accept', and the resulting complaint must say what is actually wrong."""
        for position, value in ((0, "'accept'"), (1, "'deal'"),
                                (5, '\'{"record_kind":"stored_field_state"}\'::jsonb'),
                                (11, "'sha256:" + 'cc' * 32 + "'")):
            args = list(self.FIXTURE_SHAPED_REQUEST)
            args[position] = value
            with self.subTest(position=position), self.assertRaises(gate.GateRefusal) as caught:
                gate.race_statement(args)
            self.assertIn('pre-rendered', str(caught.exception))
            self.assertIn(f'argument {position}', str(caught.exception))
        # And the string 'NULL' is not a null: it would be a four-character value.
        args = list(self.FIXTURE_SHAPED_REQUEST)
        args[9] = 'NULL'
        with self.assertRaises(gate.GateRefusal) as caught:
            gate.race_statement(args)
        self.assertIn('jsonb parameter', str(caught.exception))

    def test_race_argument_arity_and_decision_are_reported_separately(self):
        short = list(self.FIXTURE_SHAPED_REQUEST)[:11]
        with self.assertRaises(gate.GateRefusal) as caught:
            gate.race_statement(short)
        self.assertIn('12 or 13 arguments', str(caught.exception))
        refuse = list(self.FIXTURE_SHAPED_REQUEST)
        refuse[0] = 'reconcile'
        with self.assertRaises(gate.GateRefusal) as caught:
            gate.race_statement(refuse)
        self.assertIn('accept transitions', str(caught.exception))


# --------------------------------------------------------------------------
# THE INSTALLED SQL GRANT MODEL, TRANSCRIBED FROM domain.sql — not imported from
# the gate.
#
# The point of this block is that it is an INDEPENDENT statement of what the
# schema does. An earlier revision of this file derived the mock's answers from
# gate.WRITER_FUNCTIONS, so the mock agreed with the gate by construction: when
# the gate's reader model was seven names and the schema's was nine, every test
# here passed and every real run failed on the first two functions it looked at,
# before the fixture, the race or anything else had a chance to run.
#
# Source: domain.sql section 10 (the grant loop) and section 11 (the posture
# readback, which re-asserts the same twelve role/function exclusions).
# --------------------------------------------------------------------------

SQL_MODEL_PRIVATE_HELPERS = {
    'f01_claim_idempotency', 'f01_settle_idempotency', 'f01_insert_derivative_link',
    'f01_guard_direct_dml', 'f01_guard_append_only', 'f01_guard_no_truncate'}
SQL_MODEL_WRITERS = {
    'f01_install_policy', 'f01_apply_observation', 'f01_record_artifact',
    'f01_record_proposal', 'f01_register_derivative_link', 'f01_record_document',
    'f01_record_hold', 'f01_record_deletion_evaluation'}
# TEN names for carr_reader: the eight writers, plus the definer replay door and
# the authority probe. carr_writer is NOT excluded from the registration writer:
# the ordinary evidence principal is the trusted producer identity the APPROVED
# derivative-registration rule names — a session approval, not one of the nine
# settled decisions — and excluding it would leave that rule with nobody able to
# satisfy it.
SQL_MODEL_READER_EXCLUDED = SQL_MODEL_WRITERS | {
    'f01_replay_outcome', 'f01_require_authority_principal'}
SQL_MODEL_WRITER_EXCLUDED = {
    'f01_install_policy', 'f01_record_hold', 'f01_require_authority_principal'}
# The surface the loop grants on: everything matching f01\_%. A handful of
# ordinary read helpers are included so that an over-broad expectation shows up
# as a failure rather than as an absence.
SQL_MODEL_FUNCTIONS = sorted(
    SQL_MODEL_PRIVATE_HELPERS | SQL_MODEL_WRITERS | {
        'f01_read', 'f01_replay_outcome', 'f01_require_authority_principal',
        'f01_principal', 'f01_current_field_state', 'f01_canonical_json',
        'f01_derivative_links', 'f01_derivative_coverage',
        'f01_derivative_coverage_digest', 'f01_stored_derivatives',
        # The reserved-kind list the public registration writer refuses against.
        # It is an ORDINARY function on purpose: an IMMUTABLE constant naming
        # which derivative kinds an in-schema writer owns. It confers nothing, so
        # the grant loop treats it like any other read helper and every principal
        # keeps EXECUTE on it — which is what the cross-product test below then
        # holds both models to.
        'f01_reserved_derivative_kinds'})


def sql_model_execute(role, name):
    """True when domain.sql's loop leaves this role holding EXECUTE."""
    if name in SQL_MODEL_PRIVATE_HELPERS or name.startswith('f01_guard_'):
        return False
    if role == 'carr_reader' and name in SQL_MODEL_READER_EXCLUDED:
        return False
    if role == 'carr_writer' and name in SQL_MODEL_WRITER_EXCLUDED:
        return False
    return True


class GrantCatalog:
    """A catalog answering exactly the queries check_grants asks.

    Defaults describe a clean install as domain.sql leaves it, so a test that
    expects no failure is asserting that the gate does not invent one — and,
    since the answers come from the transcribed SQL model above rather than from
    the gate's constants, that the gate's model and the schema's still agree.
    """

    ALL_FUNCTIONS = SQL_MODEL_FUNCTIONS

    def __init__(self, public=False, relations=None, functions=None, mutable=(),
                 extra_execute=()):
        self.public = public
        self.relations = relations if relations is not None else [
            {'relation': 'ops.f01_field_event', 'kind': 'r'}]
        self.functions = [{'signature': f'ops.{name}(text)', 'name': name}
                          for name in (self.ALL_FUNCTIONS if functions is None else functions)]
        self.mutable = set(mutable)
        # (role, function) pairs this cluster grants over and above the model —
        # i.e. the over-grant the gate exists to notice.
        self.extra_execute = set(extra_execute)

    def scalar(self, sql, **kwargs):
        if "'relation',c.oid::regclass::text" in sql:
            return json.dumps(self.relations)
        if "'signature',p.oid::regprocedure::text" in sql:
            return json.dumps(self.functions)
        if 'aclexplode' in sql and 'pg_proc' in sql:
            return '0'
        if 'aclexplode' in sql:
            return '1' if self.public else '0'
        if 'has_schema_privilege' in sql:
            return 'f'
        if 'has_table_privilege' in sql:
            relation = re.findall(r"'([^']*)'", sql)[1]
            if "'SELECT'" in sql:
                return 't'
            return 't' if relation in self.mutable else 'f'
        if 'has_function_privilege' in sql:
            role, signature = re.findall(r"'([^']*)'", sql)[:2]
            name = signature.split('.', 1)[1].split('(')[0]
            granted = sql_model_execute(role, name) or (role, name) in self.extra_execute
            return 't' if granted else 'f'
        raise AssertionError(f'unexpected grant query: {sql[:120]}')


class FalsePassTests(unittest.TestCase):
    def test_node_preload_cannot_execute_and_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            module = root / 'mcp-server/src/artifact-trust.js'
            module.parent.mkdir(parents=True)
            module.write_text('')
            with patch.dict(os.environ, {'NODE_OPTIONS':'--import=/evil', 'NODE_PATH':'/evil'}), \
                    patch.object(gate.subprocess, 'run', return_value=SimpleNamespace(stdout='[]')) as run:
                self.assertEqual(gate.node_canonical([],root,'node'),[])
            kwargs = run.call_args.kwargs
            self.assertLessEqual(kwargs.get('timeout',float('inf')),30)
            self.assertNotIn('NODE_OPTIONS', kwargs.get('env',os.environ))
            self.assertNotIn('NODE_PATH', kwargs.get('env',os.environ))

    def test_missing_node_is_a_failed_check_not_a_traceback(self):
        """FileNotFoundError is an OSError, not a SubprocessError."""
        report = gate.Report()
        with patch.object(gate,'node_canonical',side_effect=FileNotFoundError('node')):
            gate.check_canonical_agreement(SimpleNamespace(),Path('/tmp'),'node',report)
        self.assertEqual(report.failures,1)
        self.assertIn('FileNotFoundError',report.rows[0][3])

    def test_missing_schema_is_not_authority_proof(self):
        class FakePsql:
            dsn, binary = 'unused','unused'
            def run(self, sql, user=None):
                if 'f01_read(' in sql:
                    return SimpleNamespace(returncode=0,stdout='{}',stderr='')
                if 'require_authority' in sql:
                    return SimpleNamespace(returncode=0,stdout=user.removeprefix('carr_authority_'),stderr='')
                return SimpleNamespace(returncode=1,stdout='',stderr='ERROR:  42501: permission denied for schema ops')
        report = gate.Report()
        with patch.object(gate,'rollback_probe',return_value='ERROR:  42501: permission denied for schema ops',create=True):
            gate.check_wrong_principal(FakePsql(),report)
        # Two endpoints for each of carr_reader and carr_writer, plus the
        # reader-only registration-writer probe. A schema-level denial is not a
        # proof about any of the five.
        self.assertEqual(report.failures,5)

    def test_authority_probe_survives_empty_output(self):
        """A zero exit with no rows is a failed probe, not an IndexError."""
        class FakePsql:
            dsn, binary = 'unused','unused'
            def run(self, sql, user=None):
                return SimpleNamespace(returncode=0,stdout='',stderr='')
        def refuse(psql,statement,role):
            endpoint = next(name for name in ('f01_record_hold','f01_register_derivative_link',
                                              'f01_install_policy') if name in statement)
            return f'ERROR:  42501: permission denied for function ops.{endpoint}'
        report = gate.Report()
        with patch.object(gate,'rollback_probe',refuse,create=True):
            gate.check_wrong_principal(FakePsql(),report)
        self.assertEqual(report.failures,2)  # the two authority roles derive nothing

    def test_execute_grant_refusal_requires_the_grant_mechanism(self):
        """carr_writer/carr_reader hold no EXECUTE, so the grant is what must
        refuse; a body refusal here would mean the grant model moved."""
        self.assertTrue(gate.execute_grant_refusal(
            'ERROR:  42501: permission denied for function ops.f01_record_hold','f01_record_hold'))
        for text in ('ERROR:  42501: f01_authority_principal_refused: record-artifact-preservation-hold',
                     'ERROR:  42501: permission denied for function ops.f01_record_hold_other',
                     'ERROR:  42883: function ops.f01_record_hold does not exist',
                     'permission denied for function ops.f01_record_hold'):
            with self.subTest(text=text):
                self.assertFalse(gate.execute_grant_refusal(text,'f01_record_hold'))

    def test_wrong_principal_records_a_skip_rather_than_dropping_probes(self):
        class FakePsql:
            dsn, binary = 'unused','unused'
            def run(self, sql, user=None):
                if 'f01_read(' in sql:
                    return SimpleNamespace(returncode=1,stdout='',stderr='ERROR: boom')
                return SimpleNamespace(returncode=0,stdout=user.removeprefix('carr_authority_'),stderr='')
        report = gate.Report()
        gate.check_wrong_principal(FakePsql(),report)
        self.assertEqual(report.skips,2)
        self.assertEqual(report.failures,2)

    def test_successful_negative_probe_rolls_back(self):
        operations=[]
        class Session:
            def __init__(self,*args,**kwargs): pass
            def execute(self,sql):
                operations.append(sql)
                return 'unexpected success'
            def close(self): operations.append('CLOSE')
        with patch.object(gate,'PsqlSession',Session):
            self.assertEqual(gate.rollback_probe(SimpleNamespace(dsn='x',binary='psql'),
                             'DELETE FROM ops.f01_field_event','carr_writer'),'unexpected success')
        self.assertLess(operations.index('BEGIN'),operations.index('DELETE FROM ops.f01_field_event'))
        self.assertEqual(operations[-2:],['ROLLBACK','CLOSE'])

    def test_probe_exception_still_rolls_back(self):
        operations=[]
        class Session:
            def __init__(self,*args,**kwargs): pass
            def execute(self,sql):
                operations.append(sql)
                if sql.startswith('DELETE'): raise RuntimeError('lost statement response')
                return ''
            def close(self): operations.append('CLOSE')
        with patch.object(gate,'PsqlSession',Session), self.assertRaises(RuntimeError):
            gate.rollback_probe(SimpleNamespace(dsn='x',binary='psql'),'DELETE FROM x','carr_writer')
        self.assertEqual(operations[-2:],['ROLLBACK','CLOSE'])

    def test_effective_and_public_grants_cannot_hide(self):
        for public,mutable in ((True,()),(False,('ops.f01_field_event',))):
            with self.subTest(public=public,mutable=mutable):
                report=gate.Report()
                gate.check_grants(GrantCatalog(public=public,mutable=mutable),report)
                self.assertGreater(report.failures,0)

    def test_clean_grant_catalog_raises_no_failure(self):
        report=gate.Report()
        gate.check_grants(GrantCatalog(),report)
        self.assertEqual(report.failures,0,[r for r in report.rows if r[0]==gate.FAIL])

    def test_reader_expectation_matches_the_schema_not_the_writer_list(self):
        """The reader's exclusion list is NINE names in domain.sql. A gate that
        expected EXECUTE=True on the two extra ones would fail a correct schema
        before the fixture, the race or anything else ever ran."""
        for name in ('f01_replay_outcome','f01_require_authority_principal'):
            self.assertIn(name,gate.READER_FORBIDDEN,
                          'the gate must forbid what domain.sql never grants')
            self.assertFalse(sql_model_execute('carr_reader',name))
        report=gate.Report()
        gate.check_grants(GrantCatalog(),report)
        rows={row[2]:row[0] for row in report.rows}
        for name in ('f01_replay_outcome','f01_require_authority_principal'):
            self.assertEqual(rows[f'carr_reader expected EXECUTE=False on ops.{name}(text)'],
                             gate.PASS,report.rows)
        # ...and the surfaces the reader legitimately keeps are still expected.
        self.assertEqual(rows['carr_reader expected EXECUTE=True on ops.f01_read(text)'],gate.PASS)

    def test_an_over_granted_reader_is_a_failure_not_a_shrug(self):
        """The other direction: a cluster that DID grant the reader one of the
        nine must fail, or the expectation above would be unfalsifiable."""
        for name in ('f01_replay_outcome','f01_require_authority_principal',
                     'f01_record_hold'):
            with self.subTest(function=name):
                report=gate.Report()
                gate.check_grants(GrantCatalog(extra_execute=[('carr_reader',name)]),report)
                failures=[row for row in report.rows if row[0]==gate.FAIL]
                self.assertTrue(any(f'carr_reader expected EXECUTE=False on ops.{name}' in row[2]
                                    for row in failures),failures)

    def test_the_producer_writer_is_reader_forbidden_and_writer_allowed(self):
        """The derivative-registration seam's one new write surface. A reader is
        not a producer; the ordinary evidence writer IS the producer identity the
        approved registration rule names, so taking EXECUTE away from it would
        leave that rule with nobody able to satisfy it."""
        self.assertIn('f01_register_derivative_link',gate.WRITER_FUNCTIONS)
        self.assertIn('f01_register_derivative_link',gate.READER_FORBIDDEN)
        self.assertNotIn('f01_register_derivative_link',gate.WRITER_FORBIDDEN)
        self.assertIn('f01_insert_derivative_link',gate.PRIVATE_HELPERS)
        report=gate.Report()
        gate.check_grants(GrantCatalog(),report)
        rows={row[2]:row[0] for row in report.rows}
        self.assertEqual(
            rows['carr_reader expected EXECUTE=False on ops.f01_register_derivative_link(text)'],
            gate.PASS,report.rows)
        self.assertEqual(
            rows['carr_writer expected EXECUTE=True on ops.f01_register_derivative_link(text)'],
            gate.PASS,report.rows)
        # And the private half is reachable by nobody at runtime, like the
        # idempotency helpers it sits beside.
        for role in gate.FIXTURE_ROLES:
            self.assertEqual(
                rows[f'{role} expected EXECUTE=False on ops.f01_insert_derivative_link(text)'],
                gate.PASS,report.rows)

    def test_the_coverage_read_surface_is_contracted_rather_than_optional(self):
        """A schema shipping the link table and the writer but no coverage answer
        would make every deletion fail on a missing function instead of failing
        closed on unknown coverage. Those are different reasons to refuse, and
        only one of them is the settled rule."""
        for name in ('f01_derivative_links','f01_derivative_coverage',
                     'f01_derivative_coverage_digest','f01_stored_derivatives'):
            self.assertIn(name,gate.REQUIRED_FUNCTIONS)
            present=[fn for fn in GrantCatalog.ALL_FUNCTIONS if fn!=name]
            report=gate.Report()
            gate.check_grants(GrantCatalog(functions=present),report)
            self.assertTrue(any(row[0]==gate.FAIL and name in row[3] for row in report.rows),
                            report.rows)

    def test_the_reserved_kind_list_is_contracted_and_confers_nothing(self):
        """The public registration writer refuses an in-schema producer kind by
        reading ops.f01_reserved_derivative_kinds(). A schema without it does not
        fail closed — every registration dies on a missing function instead — so
        it is contracted rather than assumed. It is also an ordinary readable
        constant: it grants nothing, so no principal is excluded from it, and a
        model that quietly treated it as a write surface would fail here."""
        name = 'f01_reserved_derivative_kinds'
        self.assertIn(name, gate.REQUIRED_FUNCTIONS)
        self.assertNotIn(name, gate.PRIVATE_HELPERS)
        self.assertNotIn(name, gate.READER_FORBIDDEN)
        self.assertNotIn(name, gate.WRITER_FORBIDDEN)
        for role in gate.FIXTURE_ROLES:
            self.assertTrue(sql_model_execute(role, name))
        present = [fn for fn in GrantCatalog.ALL_FUNCTIONS if fn != name]
        report = gate.Report()
        gate.check_grants(GrantCatalog(functions=present), report)
        self.assertTrue(any(row[0] == gate.FAIL and name in row[3] for row in report.rows),
                        report.rows)

    def test_the_gate_and_the_schema_agree_on_every_role_function_pair(self):
        """One assertion over the whole cross product, so a future edit to either
        model has to be made in both places."""
        for role in gate.FIXTURE_ROLES:
            for name in SQL_MODEL_FUNCTIONS:
                gate_forbids=(name in gate.PRIVATE_HELPERS or name.startswith('f01_guard_')
                              or (role=='carr_reader' and name in gate.READER_FORBIDDEN)
                              or (role=='carr_writer' and name in gate.WRITER_FORBIDDEN))
                self.assertEqual(not gate_forbids,sql_model_execute(role,name),
                                 f'{role}/{name}: the gate and domain.sql disagree')

    def test_owner_rights_bypass_surface_is_in_the_privilege_proof(self):
        """A view over an F01 table executes with its OWNER's privileges, so an
        inventory limited to relkind r/p would never see the grant that matters."""
        relations=[{'relation':'ops.f01_field_event','kind':'r'},
                   {'relation':'ops.f01_field_event_v','kind':'v'}]
        report=gate.Report()
        gate.check_grants(GrantCatalog(relations=relations,
                                       mutable=('ops.f01_field_event_v',)),report)
        failures=[row for row in report.rows if row[0]==gate.FAIL]
        self.assertTrue(any('ops.f01_field_event_v' in row[2] for row in failures),failures)
        # The view is not held to the base-table SELECT contract.
        self.assertFalse(any('can SELECT ops.f01_field_event_v' in row[2] for row in report.rows))

    def test_absent_contracted_function_is_not_a_vacuous_pass(self):
        present=[name for name in GrantCatalog.ALL_FUNCTIONS if name!='f01_record_hold']
        report=gate.Report()
        gate.check_grants(GrantCatalog(functions=present),report)
        self.assertTrue(any(row[0]==gate.FAIL and 'f01_record_hold' in row[3] for row in report.rows),
                        report.rows)

    def test_direct_dml_update_targets_a_resolved_column(self):
        probed=[]
        def probe(psql,statement,role):
            probed.append(statement)
            return 'ERROR:  42501: permission denied for table f01_field_event'
        targets=[{'table':'f01_field_event','column':'tenant'}]
        report=gate.Report()
        with patch.object(gate,'probe_targets',return_value=targets), \
                patch.object(gate,'rollback_probe',probe):
            gate.check_runtime_direct_dml(SimpleNamespace(),'carr_writer',report)
        self.assertIn('UPDATE ops."f01_field_event" SET "tenant"="tenant"',probed)
        self.assertEqual(report.failures,0)

    def test_direct_dml_without_a_resolvable_column_fails_rather_than_misattributes(self):
        targets=[{'table':'f01_odd','column':None}]
        report=gate.Report()
        with patch.object(gate,'probe_targets',return_value=targets), \
                patch.object(gate,'rollback_probe',
                             return_value='ERROR:  42501: permission denied for table f01_odd'):
            gate.check_runtime_direct_dml(SimpleNamespace(),'carr_writer',report)
        self.assertEqual(report.failures,1)
        self.assertNotIn('cannot UPDATE',' '.join(row[2] for row in report.rows))

    def test_noinherit_admin_membership_refuses(self):
        class FakePsql(PreflightPsql):
            memberships='[{"name":"f01_owner","admin":false}]'
        with self.assertRaises(gate.GateRefusal) as caught:
            gate.verify_connections(FakePsql(),'f01_gate_test')
        self.assertIn('SET ROLE reachability',str(caught.exception))

    def test_owner_precondition_observes_rather_than_legislates(self):
        class FakePsql:
            owner_role='f01_owner'
            def __init__(self,attributes,memberships):
                self.attributes,self.memberships=attributes,memberships
            def scalar(self,sql,**kwargs):
                return json.dumps(self.memberships if 'WITH RECURSIVE' in sql else self.attributes)
        clean=gate.Report()
        gate.owner_precondition(FakePsql([],[]),clean)
        self.assertEqual((clean.failures,clean.skips),(0,0))
        for attributes,memberships in (
                (['rolsuper'],[]),
                ([],[{'name':'cluster_admin','admin':True}])):
            with self.subTest(attributes=attributes):
                report=gate.Report()
                gate.owner_precondition(FakePsql(attributes,memberships),report)
                # A limit on what was proved is neither a pass nor an invented policy.
                self.assertEqual((report.failures,report.skips),(0,1))

    def test_owner_cannot_be_fixture_principal(self):
        with self.assertRaises(gate.GateRefusal):
            gate.verify_connections(SimpleNamespace(owner_role='carr_writer'),'f01_gate_test')

    def test_relative_fixture_resolves_under_repo(self):
        self.assertEqual(gate.resolve_input(Path('/tmp/project'),'mcp-server/test/fixture.sql'),
                         Path('/tmp/project/mcp-server/test/fixture.sql').resolve())

    def test_legacy_fingerprint_reads_catalog_acls_not_a_filtered_view(self):
        """role_table_grants shows only grants the reader is party to, so a grant
        to an unrelated third role would be invisible before AND after."""
        self.assertNotIn('role_table_grants',gate.LEGACY_FINGERPRINT_SQL)
        for fragment in ('relacl','attacl','relrowsecurity','pg_get_triggerdef','pg_get_ruledef'):
            self.assertIn(fragment,gate.LEGACY_FINGERPRINT_SQL)

    def test_modes_never_return_acceptance_for_draft_or_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            fixture=root/'fixture.sql'
            fixture.write_text('')
            class FakePsql:
                # main() builds Psql(dsn, psql, args.owner_role), so the connection's
                # owner_role is always the one the args below name; run_verified reads
                # it to attribute the fixture when --fixture-role is not passed.
                owner_role='f01_owner'
                def scalar(self,*args,**kwargs):
                    return '[]' if 'pg_language' in args[0] else '0'
                def run_file(self,*args,**kwargs):
                    return SimpleNamespace(returncode=0,stdout='F01_RACE_REQUEST={}\n',stderr='')
            # 3 is a DRAFT proof; 4 is a full run with a proof omitted. A caller
            # that cannot tell them apart eventually accepts the second one.
            for domain,skips,expected in ((True,False,3),(True,True,4),(False,False,0),(False,True,4)):
                args=SimpleNamespace(domain_sql='domain.sql' if domain else None,
                    migration=[] if domain else ['migration.sql'],node='node',fixture='fixture.sql',
                    skip_concurrency=skips,dsn='unused',psql='unused',owner_role='f01_owner',
                    fixture_role=None)
                with contextlib.ExitStack() as stack:
                    for name in ('check_no_policy_rows','check_grants','check_canonical_agreement',
                                 'check_helper_prerequisites','check_wrong_principal',
                                 'check_runtime_direct_dml','check_concurrency'):
                        stack.enter_context(patch.object(gate,name))
                    stack.enter_context(patch.object(gate,'legacy_rowcounts',return_value='0'))
                    stack.enter_context(patch.object(gate,'apply_sql_files',return_value=True))
                    self.assertEqual(gate.run_verified(args,root,FakePsql(),gate.Report()),expected)
            base=['--dsn','postgresql://127.0.0.1:5432/f01_gate_test',
                  '--confirm-disposable','f01_gate_test','--owner-role','f01_owner']
            with patch.object(gate.shutil,'which',return_value='/fake/psql'):
                self.assertEqual(gate.main(base),2)
                self.assertEqual(gate.main(base+['--domain-sql','d','--migration','m']),2)

    def test_a_crashing_check_is_a_failed_row_not_a_traceback(self):
        report=gate.Report()
        def explode(*args):
            raise subprocess.CalledProcessError(1,'psql')
        gate.guarded(report,'grants','the grants section ran to completion',explode,None)
        self.assertEqual(report.failures,1)
        self.assertIn('CalledProcessError',report.rows[0][3])

    def test_cluster_readback_detects_changes_and_runs_after_failure(self):
        base=['--dsn','postgresql://127.0.0.1:5432/f01_gate_test',
              '--confirm-disposable','f01_gate_test','--owner-role','f01_owner','--domain-sql','d']
        for failure in (False,True):
            with patch.object(gate.shutil,'which',return_value='/fake/psql'), \
                    patch.object(gate,'verify_connections'), \
                    patch.object(gate,'owner_precondition'), \
                    patch.object(gate,'Psql',return_value=SimpleNamespace(scalar=lambda sql:'150000')), \
                    patch.object(gate,'cluster_inventory',side_effect=['before','changed']) as inventory, \
                    patch.object(gate,'run_verified',side_effect=RuntimeError('fixture failed') if failure else None,
                                 return_value=0):
                if failure:
                    with self.assertRaises(RuntimeError): gate.main(base)
                else:
                    self.assertEqual(gate.main(base),1)
                self.assertEqual(inventory.call_count,2)

    def test_readback_failure_does_not_replace_the_outcome(self):
        """A dead server at readback time is a named FAIL, not an exception that
        overwrites the verdict of every check that already ran."""
        base=['--dsn','postgresql://127.0.0.1:5432/f01_gate_test',
              '--confirm-disposable','f01_gate_test','--owner-role','f01_owner','--domain-sql','d']
        printed=io.StringIO()
        with patch.object(gate.shutil,'which',return_value='/fake/psql'), \
                patch.object(gate,'verify_connections'), \
                patch.object(gate,'owner_precondition'), \
                patch.object(gate,'Psql',return_value=SimpleNamespace(scalar=lambda sql:'150000')), \
                patch.object(gate,'cluster_inventory',
                             side_effect=['before',RuntimeError('server is gone')]), \
                patch.object(gate,'run_verified',return_value=0), \
                contextlib.redirect_stdout(printed):
            self.assertEqual(gate.main(base),1)
        self.assertIn('read the cluster inventory back',printed.getvalue())

    def test_preflight_server_error_refuses_instead_of_crashing(self):
        base=['--dsn','postgresql://127.0.0.1:5432/f01_gate_test',
              '--confirm-disposable','f01_gate_test','--owner-role','f01_owner','--domain-sql','d']
        with patch.object(gate.shutil,'which',return_value='/fake/psql'), \
                patch.object(gate,'verify_connections'), \
                patch.object(gate,'Psql',return_value=SimpleNamespace(
                    scalar=lambda sql:(_ for _ in ()).throw(
                        subprocess.CalledProcessError(2,'psql')))), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(gate.main(base),2)

    def test_binaries_are_pinned_to_a_resolved_path(self):
        """PATH survives into the child environment, so checking one string and
        executing another leaves the binary unpinned for the whole run."""
        base=['--dsn','postgresql://127.0.0.1:5432/f01_gate_test','--confirm-disposable',
              'f01_gate_test','--owner-role','f01_owner','--domain-sql','d','--node','node']
        with patch.object(gate.shutil,'which',side_effect=lambda n:None if n=='node' else '/bin/'+n), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(gate.main(base),2)   # an absent node refuses up front
        self.assertIn('node is not on PATH',err.getvalue())

        seen={}
        def build(dsn,binary,owner):
            seen['binary']=binary
            return SimpleNamespace(scalar=lambda sql:'150000')
        def observe(args,repo_root,psql,report):
            seen['node']=args.node
            return 0
        with patch.object(gate.shutil,'which',side_effect=lambda n:'/usr/local/bin/'+n), \
                patch.object(gate,'verify_connections'), \
                patch.object(gate,'owner_precondition'), \
                patch.object(gate,'cluster_inventory',return_value='same'), \
                patch.object(gate,'run_verified',observe), \
                patch.object(gate,'Psql',build):
            self.assertEqual(gate.main(base),0)
        self.assertEqual(seen['binary'],'/usr/local/bin/psql')
        self.assertEqual(seen['node'],'/usr/local/bin/node')

    def test_cluster_inventory_covers_persistent_effect_capability(self):
        captured={}
        gate.cluster_inventory(SimpleNamespace(scalar=lambda sql:captured.setdefault('sql',sql)))
        for fragment in ('pg_extension','pg_language','pg_auth_members','pg_database'):
            self.assertIn(fragment,captured['sql'])

    def test_session_reads_are_bounded_by_a_deadline(self):
        """An unbounded read hangs the gate and gives the parent no verdict;
        statement_timeout covers neither the connect window nor a silent server."""
        release=threading.Event()
        class Stdout:
            def __init__(self,lines): self.lines=list(lines)
            def __iter__(self): return self
            def __next__(self):
                if self.lines: return self.lines.pop(0)
                release.wait(0.5)
                raise StopIteration
        class Proc:
            def __init__(self,lines):
                self.stdout=Stdout(lines); self.stdin=io.StringIO(); self.killed=False
            def wait(self,timeout=None): return 0
            def kill(self): self.killed=True
        with patch.object(gate.subprocess,'Popen',return_value=Proc([])):
            session=gate.PsqlSession('unused','psql','A')
            with self.assertRaises(gate.GateRefusal):
                session.collect(timeout=0.05)
        with patch.object(gate.subprocess,'Popen',
                          return_value=Proc(['row\n',gate.PsqlSession.MARKER+'\n'])):
            session=gate.PsqlSession('unused','psql','B')
            self.assertEqual(session.collect(timeout=5),'row')
        release.set()

    def test_race_monitor_observes_as_the_racing_role(self):
        """pg_stat_activity redacts state and wait_event_type for another role's
        backend, so an owner-role monitor would fail a CORRECT implementation."""
        users=[]
        class Monitor:
            def __init__(self,*args): pass
            def scalar(self,sql,user=None):
                users.append(user)
                return json.dumps({'present':True,'visible':True,'lock_wait':True,
                                   'blocked_by':True})
        report=gate.Report()
        with patch.object(gate,'PsqlSession',self.race_session()),patch.object(gate,'Psql',Monitor), \
                patch.object(gate.time,'sleep'):
            gate.check_concurrency('unused','unused','f01_owner',self.race_requests(),report)
        self.assertEqual(set(users),{'carr_writer'})

    def test_redacted_backend_is_reported_as_unobservable_not_as_no_wait(self):
        class Monitor:
            def __init__(self,*args): pass
            def scalar(self,sql,user=None):
                return json.dumps({'present':True,'visible':False,'lock_wait':False,
                                   'blocked_by':False})
        report=gate.Report()
        with patch.object(gate,'PsqlSession',self.race_session()),patch.object(gate,'Psql',Monitor), \
                patch.object(gate.time,'monotonic',side_effect=[0,1,6]),patch.object(gate.time,'sleep'):
            gate.check_concurrency('unused','unused','f01_owner',self.race_requests(),report)
        failures=[row for row in report.rows if row[0]==gate.FAIL]
        self.assertTrue(any('not observable by the monitor principal' in row[3] for row in failures),
                        failures)

    @staticmethod
    def race_requests():
        args=['accept','deal','amount','policy','before',{}, {}, {}, {},None,'key-a','digest']
        other=args.copy(); other[10]='key-b'
        return {'race_a':args,'race_b':other}

    @staticmethod
    def race_session(events=None,counts_after=(11,11,11,1,1)):
        class Session:
            def __init__(self,dsn,binary,name,**kwargs): self.name=name;self.advanced=False
            def execute(self,sql):
                if events is not None: events.append((self.name,sql))
                if 'json_build_array' in sql:
                    return json.dumps(list(counts_after) if self.advanced else [10,10,10,1,1])
                if 'current_field_state' in sql: return 'after' if self.advanced else 'before'
                if 'apply_observation' in sql:
                    self.advanced=True
                    return '{"outcome":"accepted","readback":{"state_digest":"after"}}'
                if 'pg_backend_pid' in sql: return '123'
                return ''
            def send(self,sql):
                if events is not None: events.append((self.name,'SEND'))
            def collect(self,timeout=None): return 'ERROR:  40001: f01_stale_current_state: stale'
            def close(self):
                if events is not None: events.append((self.name,'CLOSE'))
        return Session

    def test_race_missing_transition_fails_and_unobserved_lock_never_commits(self):
        for observed_lock in (True,False):
            events=[]
            class Monitor:
                def __init__(self,*args): pass
                def scalar(self,sql,user=None):
                    return json.dumps({'present':True,'visible':True,
                                       'lock_wait':observed_lock,'blocked_by':observed_lock})
            report=gate.Report()
            # One transition short of the accepted set: the counts must notice.
            with patch.object(gate,'PsqlSession',self.race_session(events,(11,10,11,1,1))), \
                    patch.object(gate,'Psql',Monitor), \
                    patch.object(gate.time,'monotonic',side_effect=[0,1,6]),patch.object(gate.time,'sleep'):
                gate.check_concurrency('unused','unused','owner',self.race_requests(),report)
            self.assertGreater(report.failures,0)
            if not observed_lock: self.assertNotIn(('A','COMMIT'),events)

    def test_race_asserts_the_prior_state_row_and_labels_reconciliation_as_a_delta(self):
        """'no reconciliation' would be a claim about history; the race writes no
        NEW item, and the current-state row is asserted rather than assumed."""
        events=[]
        class Monitor:
            def __init__(self,*args): pass
            def scalar(self,sql,user=None):
                return json.dumps({'present':True,'visible':True,'lock_wait':True,'blocked_by':True})
        report=gate.Report()
        with patch.object(gate,'PsqlSession',self.race_session(events)), \
                patch.object(gate,'Psql',Monitor),patch.object(gate.time,'sleep'):
            gate.check_concurrency('unused','unused','owner',self.race_requests(),report)
        labels=[row[2] for row in report.rows]
        self.assertIn('the fixture established exactly one current-state row for the raced field',labels)
        self.assertTrue(any('no new reconciliation item' in label for label in labels),labels)
        self.assertFalse(any(label.endswith('no reconciliation; one current state') for label in labels))


class PrerequisiteTests(unittest.TestCase):
    """The bootstrap the gate DEPENDS on and never supplies: the carr_authority
    group, the canonical ops.authority_actor_slug() helper and the two EXECUTE
    grants that let the definer and caller-rights paths both reach it."""

    def test_missing_authority_group_refuses_with_the_prerequisite_named(self):
        """pg_has_role errors on a role that does not exist, so without this the
        parent gets 'REFUSED: ... CalledProcessError' and no idea what to do."""
        class FakePsql:
            owner_role='f01_owner'
            dsn='postgresql://127.0.0.1:5432/f01_gate_test'
            def scalar(self,sql,user=None):
                if "rolname='carr_authority'" in sql:
                    return '0'
                return identity_row(user)
        with self.assertRaises(gate.GateRefusal) as caught:
            gate.verify_connections(FakePsql(),'f01_gate_test')
        self.assertIn('carr_authority',str(caught.exception))
        self.assertIn('0161',str(caught.exception))
        self.assertIn('creates no role',str(caught.exception))

    @staticmethod
    def helper_psql(present='t',**shape):
        grants=shape.pop('grants',None) or {
            'carr_authority_joe':'t','carr_authority_dell':'t',
            'carr_writer':'f','carr_reader':'f'}
        row=dict(definer=True,public=0,context_definer=True,require_definer=False,
                 owner_execute=True,definer_owner='f01_owner')
        row.update(shape)
        class FakePsql:
            def scalar(self,sql,user=None):
                if 'to_regprocedure' in sql and 'IS NOT NULL' in sql:
                    return present
                if 'definer_owner' in sql:
                    return json.dumps(row)
                if 'has_function_privilege' in sql:
                    role=re.findall(r"'([^']*)'",sql)[0]
                    return grants[role]
                raise AssertionError(f'unexpected prerequisite query: {sql[:80]}')
        return FakePsql()

    def test_clean_prerequisites_pass_without_inventing_a_failure(self):
        report=gate.Report()
        gate.check_helper_prerequisites(self.helper_psql(),report)
        self.assertEqual(report.failures,0,[r for r in report.rows if r[0]==gate.FAIL])
        self.assertGreater(len(report.rows),4)

    def test_absent_helper_fails_once_and_names_the_migration(self):
        report=gate.Report()
        gate.check_helper_prerequisites(self.helper_psql(present='f'),report)
        self.assertEqual(report.failures,1)
        self.assertIn('0161',report.rows[0][3])

    def test_each_broken_prerequisite_is_its_own_named_failure(self):
        cases={'public':1,'owner_execute':False,'context_definer':False,
               'require_definer':True}
        for key,value in cases.items():
            with self.subTest(prerequisite=key):
                report=gate.Report()
                gate.check_helper_prerequisites(self.helper_psql(**{key:value}),report)
                self.assertEqual(report.failures,1,report.rows)

    def test_an_authority_login_that_cannot_reach_the_helper_fails(self):
        """The caller-rights path runs as the LOGIN, so this grant is load-bearing
        and comes from 0161 via carr_authority membership — never from F01."""
        report=gate.Report()
        gate.check_helper_prerequisites(self.helper_psql(grants={
            'carr_authority_joe':'t','carr_authority_dell':'f',
            'carr_writer':'f','carr_reader':'f'}),report)
        failures=[row for row in report.rows if row[0]==gate.FAIL]
        self.assertEqual(len(failures),1,report.rows)
        self.assertIn('carr_authority_dell',failures[0][2])

    def test_a_runtime_principal_that_can_reach_the_helper_fails(self):
        report=gate.Report()
        gate.check_helper_prerequisites(self.helper_psql(grants={
            'carr_authority_joe':'t','carr_authority_dell':'t',
            'carr_writer':'t','carr_reader':'f'}),report)
        failures=[row for row in report.rows if row[0]==gate.FAIL]
        self.assertEqual(len(failures),1,report.rows)
        self.assertIn('carr_writer',failures[0][2])


class FixturePrincipalTests(unittest.TestCase):
    """--fixture-role: an EXISTING second login, used for one psql -f and nothing
    else, so that the owner every privilege proof is about need not be a
    superuser. It is verified, bounded and fingerprinted — never created, never
    granted anything."""

    @staticmethod
    def fixture_psql(memberships=(),**overrides):
        class FakePsql:
            owner_role='f01_owner'
            dsn='postgresql://127.0.0.1:5432/f01_gate_test'
            def scalar(self,sql,user=None):
                if 'WITH RECURSIVE' in sql:
                    return json.dumps([{'name':m,'admin':False} for m in memberships])
                return identity_row(user,**({'super':True}|overrides))
        return FakePsql()

    def test_a_clean_separate_superuser_login_is_accepted_and_described(self):
        report=gate.Report()
        gate.verify_fixture_principal(self.fixture_psql(),'f01_gate_test','f01_bootstrap',report)
        self.assertEqual((report.failures,report.skips),(0,0))
        self.assertIn('f01_bootstrap',report.rows[0][3])
        self.assertIn('used only for psql -f',report.rows[0][3])

    def test_the_fixture_login_may_not_be_the_owner_or_a_runtime_principal(self):
        for role in ('f01_owner',)+gate.FIXTURE_ROLES:
            with self.subTest(role=role), self.assertRaises(gate.GateRefusal) as caught:
                gate.verify_fixture_principal(self.fixture_psql(),'f01_gate_test',role,
                                              gate.Report())
            self.assertIn('distinct',str(caught.exception))

    def test_a_non_superuser_fixture_login_refuses_early_rather_than_at_the_fixture(self):
        with self.assertRaises(gate.GateRefusal) as caught:
            gate.verify_fixture_principal(self.fixture_psql(super=False),'f01_gate_test',
                                          'f01_bootstrap',gate.Report())
        self.assertIn('SET SESSION AUTHORIZATION',str(caught.exception))
        self.assertIn('does not grant it anything',str(caught.exception))

    def test_identity_is_verified_the_same_way_every_other_principal_is(self):
        for override in ({'db':'somewhere_else'},{'address':'10.0.0.4'},{'port':6543},
                         {'owner':'somebody_else'},{'session':'other'}):
            with self.subTest(override=override), self.assertRaises(gate.GateRefusal) as caught:
                gate.verify_fixture_principal(self.fixture_psql(**override),'f01_gate_test',
                                              'f01_bootstrap',gate.Report())
            self.assertIn('identity mismatch',str(caught.exception))

    def test_a_fixture_login_carrying_runtime_membership_refuses(self):
        """Applying the fixture must not double as a route to default authority.
        pg_has_role answers TRUE for a superuser against everything, so this is
        read from the membership catalog, where it means something."""
        for granted in ('carr_authority','carr_writer','carr_reader'):
            with self.subTest(granted=granted), self.assertRaises(gate.GateRefusal) as caught:
                gate.verify_fixture_principal(self.fixture_psql(memberships=[granted]),
                                              'f01_gate_test','f01_bootstrap',gate.Report())
            self.assertIn(granted,str(caught.exception))

    @staticmethod
    def run_args(fixture_role):
        return SimpleNamespace(domain_sql='domain.sql',migration=[],node='node',
                               fixture='fixture.sql',skip_concurrency=True,dsn='unused',
                               psql='unused',owner_role='f01_owner',fixture_role=fixture_role)

    def run_once(self,fixture_role,fingerprints=('same','same')):
        calls=[]
        remaining=list(fingerprints)
        class FakePsql:
            owner_role='f01_owner'
            def scalar(self,sql,user=None):
                calls.append(('scalar',user))
                if 'nspowner' in sql:
                    return remaining.pop(0)
                return '[]' if 'pg_language' in sql else '0'
            def run_file(self,path,user=None):
                calls.append(('run_file',user))
                return SimpleNamespace(returncode=0,stdout='F01_RACE_REQUEST={}\n',stderr='')
        report=gate.Report()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'fixture.sql').write_text('')
            with contextlib.ExitStack() as stack:
                for name in ('check_no_policy_rows','check_grants','check_canonical_agreement',
                             'check_helper_prerequisites','check_wrong_principal',
                             'check_runtime_direct_dml'):
                    stack.enter_context(patch.object(gate,name))
                stack.enter_context(patch.object(gate,'legacy_rowcounts',return_value='0'))
                stack.enter_context(patch.object(gate,'apply_sql_files',return_value=True))
                gate.run_verified(self.run_args(fixture_role),root,FakePsql(),report)
        return calls,report

    def test_the_fixture_login_applies_the_fixture_and_nothing_else(self):
        calls,report=self.run_once('f01_bootstrap')
        self.assertIn(('run_file','f01_bootstrap'),calls)
        # Not one other statement in the run is issued as that login.
        self.assertEqual([c for c in calls if c[1]=='f01_bootstrap'],
                         [('run_file','f01_bootstrap')],calls)
        self.assertEqual(report.failures,0,[r for r in report.rows if r[0]==gate.FAIL])

    def test_omitting_the_fixture_login_applies_the_fixture_as_the_owner(self):
        calls,_=self.run_once(None)
        self.assertIn(('run_file',None),calls)

    def test_a_schema_change_across_the_fixture_is_a_named_failure(self):
        """A superuser applier can reassign, re-grant or leave a guard disabled.
        That is detected here — detection, not containment."""
        _,report=self.run_once('f01_bootstrap',fingerprints=('before','after'))
        failures=[row for row in report.rows if row[0]==gate.FAIL]
        self.assertTrue(any('ops ownership, ACLs, triggers' in row[2] for row in failures),
                        report.rows)

    def test_an_unreadable_fingerprint_never_reads_as_unchanged(self):
        class Dead:
            def scalar(self,sql,user=None):
                raise subprocess.CalledProcessError(2,'psql')
        first=gate.ops_fingerprint(Dead(),'before')
        second=gate.ops_fingerprint(Dead(),'after')
        self.assertNotEqual(first,second)
        self.assertIn('unreadable',first)


if __name__ == '__main__':
    unittest.main()
