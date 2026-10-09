#!/usr/bin/env python3
"""Exercise merge-time renumbering through its CLI in isolated Git repositories."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env

TOOL = ROOT / "tools/renumber-at-merge.py"


class RenumberTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="renumber-at-merge-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = fixture_env()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "selftest")
        self.git("config", "user.email", "selftest@example.invalid")
        self.write("migrations/0010_main.sql", "select 10;\n")
        self.write("docs/existing.txt", "Main uses 0010_main.sql.\n")
        self.commit("main fixture")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("switch", "-q", "-c", "feature")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message):
        paths = self.git("ls-files", "--modified", "--others", "--exclude-standard").splitlines()
        if paths:
            self.git("add", "--", *paths)
        self.write("message.txt", message)
        self.git("commit", "-q", "-F", "message.txt")
        (self.repo / "message.txt").unlink()

    def run_tool(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(TOOL), "--repo", str(self.repo), *args],
                                cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def tree(self):
        return {str(p.relative_to(self.repo)): p.read_bytes()
                for p in self.repo.rglob("*") if p.is_file() and ".git" not in p.parts}

    def test_collision_and_references(self):
        self.write("migrations/0010_feature.sql", "-- Migration 0010\nselect 1;\n")
        self.write("docs/existing.txt", "Main uses 0010_main.sql.\nFeature uses migrations/0010_feature.sql (migration 0010).\n")
        self.write("ops/config/registry.json", '{"migration":"migrations/0010_feature.sql"}\n')
        self.commit("feature")
        result = self.run_tool()
        self.assertEqual(result["renames"], {"0010_feature.sql": "0011_feature.sql"})
        self.assertFalse((self.repo / "migrations/0010_feature.sql").exists())
        self.assertEqual((self.repo / "migrations/0011_feature.sql").read_text(), "-- Migration 0011\nselect 1;\n")
        self.assertEqual((self.repo / "docs/existing.txt").read_text(), "Main uses 0010_main.sql.\nFeature uses migrations/0011_feature.sql (migration 0011).\n")
        self.assertEqual(json.loads((self.repo / "ops/config/registry.json").read_text())["migration"], "migrations/0011_feature.sql")
        self.assertEqual((self.repo / "migrations/0010_main.sql").read_text(), "select 10;\n")

    def test_sorts_below_main(self):
        self.write("migrations/0009_feature.sql", "select 1;\n")
        self.commit("below main")
        self.assertEqual(self.run_tool()["renames"], {"0009_feature.sql": "0011_feature.sql"})

    def test_unpadded_migration_label(self):
        self.write("migrations/0009_feature.sql", "-- Migration number 9\nselect 1;\n")
        self.commit("unpadded label")
        self.run_tool()
        self.assertEqual((self.repo / "migrations/0011_feature.sql").read_text(), "-- Migration number 11\nselect 1;\n")

    def test_edited_main_reference_keeps_its_numeric_label(self):
        main = "Main uses 0010_main.sql (migration 0010).\n"
        self.write("docs/existing.txt", main)
        self.commit("main numeric reference")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("migrations/0010_feature.sql", "-- Migration 0010\nselect 1;\n")
        edited = main.rstrip() + ", already applied\n"
        self.write("docs/existing.txt", edited)
        self.commit("edit main reference")
        self.run_tool()
        self.assertEqual((self.repo / "docs/existing.txt").read_text(), edited)
        self.assertEqual((self.repo / "migrations/0011_feature.sql").read_text(), "-- Migration 0011\nselect 1;\n")

    def test_filename_references_require_complete_tokens(self):
        self.write("migrations/0009_feature.sql", "select 1;\n")
        source = "open('archive_0009_feature.sql')\nopen('10009_feature.sql')\nopen('0009_feature.sql.bak')\nopen('0009_feature.sql_extra')\nopen('migrations/0009_feature.sql')\n"
        self.write("tools/source.py", source)
        self.commit("filename references")
        self.run_tool()
        self.assertEqual((self.repo / "tools/source.py").read_text(), source.replace("migrations/0009_feature.sql", "migrations/0011_feature.sql"))

    def test_sql_values_are_not_migration_references(self):
        self.write("migrations/0009_feature.sql", "-- Migration 0009\nselect 0009 as business_value, '0009' as business_code;\n")
        self.commit("SQL values")
        self.run_tool()
        self.assertEqual((self.repo / "migrations/0011_feature.sql").read_text(), "-- Migration 0011\nselect 0009 as business_value, '0009' as business_code;\n")

    def test_two_added_migrations_do_not_cascade(self):
        self.write("migrations/0009_first.sql", "-- 0009_first.sql then 0010_second.sql\nselect 1;\n")
        self.write("migrations/0010_second.sql", "-- 0009_first.sql then 0010_second.sql\nselect 2;\n")
        self.commit("two migrations")
        self.assertEqual(self.run_tool()["renames"], {
            "0009_first.sql": "0011_first.sql", "0010_second.sql": "0012_second.sql"})
        self.assertIn("0011_first.sql then 0012_second.sql", (self.repo / "migrations/0011_first.sql").read_text())

    def test_noop_branch(self):
        self.write("migrations/0011_forward.sql", "select 1;\n")
        self.commit("already forward")
        before = self.tree()
        self.assertEqual(self.run_tool()["renames"], {})
        self.assertEqual(self.tree(), before)

    def test_applied_migration_refusal(self):
        self.git("mv", "migrations/0010_main.sql", "migrations/0009_main.sql")
        self.write("migrations/0010_feature.sql", "select 1;\n")
        self.commit("attempt to move applied migration")
        before = self.tree()
        result = self.run_tool(ok=False)
        self.assertIn("applied main migration", result["error"])
        self.assertEqual(self.tree(), before)

    def test_idempotency_before_and_after_commit(self):
        self.write("migrations/0009_feature.sql", "-- 0009_feature.sql\nselect 1;\n")
        self.commit("feature")
        self.run_tool()
        before = self.tree()
        self.assertEqual(self.run_tool()["renames"], {})
        self.assertEqual(self.tree(), before)
        self.commit("renumbered")
        self.assertEqual(self.run_tool()["renames"], {})
        self.assertEqual(self.tree(), before)

    def test_dry_run_does_not_write(self):
        self.write("migrations/0010_feature.sql", "select 1;\n")
        self.commit("feature")
        before = self.tree()
        self.assertEqual(self.run_tool("--dry-run")["renames"], {"0010_feature.sql": "0011_feature.sql"})
        self.assertEqual(self.tree(), before)

    def test_burned_slots_are_skipped(self):
        self.write("migrations/0532_room_dispatch_spine_scac_successor.sql", "select 1;\n")
        self.commit("main advanced")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("migrations/0531_feature.sql", "select 1;\n")
        self.commit("feature")
        self.assertEqual(self.run_tool()["renames"], {"0531_feature.sql": "0537_feature.sql"})

    def test_generator_emits_references_and_new_checksum(self):
        self.write("migrations/0009_feature.sql", "-- 0009_feature.sql\nselect 1;\n")
        self.write("ops/result.generated.json", '{"migration":"0009_feature.sql","sha256":"before"}\n')
        self.write("tools/render.py", "import hashlib,json,pathlib\np=pathlib.Path('migrations/0011_feature.sql')\npathlib.Path('ops/result.generated.json').write_text(json.dumps({'migration':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))\n")
        self.commit("generated reference")
        self.run_tool("--generator", "ops/result.generated.json", json.dumps([sys.executable, "tools/render.py"]))
        result = json.loads((self.repo / "ops/result.generated.json").read_text())
        self.assertEqual(result["migration"], "0011_feature.sql")
        self.assertEqual(result["sha256"], hashlib.sha256(b"-- 0011_feature.sql\nselect 1;\n").hexdigest())

    def test_snapshot_runs_own_disposable_generator(self):
        snapshot = "COPY public.schema_migrations (filename, sha256) FROM stdin;\n0010_main.sql\tmain-sha\n\\.\n"
        self.write("db/schema.sql", snapshot)
        self.commit("main snapshot")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("migrations/0009_feature.sql", "-- 0009_feature.sql\nselect 1;\n")
        self.write("db/schema.sql", snapshot.replace("\\.\n", "0009_feature.sql\told-sha\n\\.\n"))
        self.write("ops/migration-shadow.py", "import hashlib,pathlib,sys\nassert '--write' in sys.argv and '--base' in sys.argv\np=pathlib.Path('migrations/0011_feature.sql')\npathlib.Path('db/schema.sql').write_text('COPY public.schema_migrations (filename, sha256) FROM stdin;\\n0010_main.sql\\tmain-sha\\n'+p.name+'\\t'+hashlib.sha256(p.read_bytes()).hexdigest()+'\\n\\\\.\\n')\n")
        self.commit("pending snapshot")
        result = self.run_tool()
        self.assertEqual(result["generators"][0][1], "ops/migration-shadow.py")
        self.assertIn("0011_feature.sql\t" + hashlib.sha256(b"-- 0011_feature.sql\nselect 1;\n").hexdigest(), (self.repo / "db/schema.sql").read_text())

    def test_unregistered_generator_refuses_before_writes(self):
        self.write("migrations/0009_feature.sql", "select 1;\n")
        self.write("ops/result.generated.json", '{"migration":"0009_feature.sql"}\n')
        self.commit("unknown generator")
        before = self.tree()
        result = self.run_tool(ok=False)
        self.assertIn("owning generator", result["error"])
        self.assertEqual(self.tree(), before)

    def test_generated_migration_rename_requires_generator(self):
        self.write("migrations/0009_feature.sql", "-- GENERATED by own renderer\nselect 1;\n")
        self.commit("generated migration")
        before = self.tree()
        self.assertIn("owning generator", self.run_tool(ok=False)["error"])
        self.assertEqual(self.tree(), before)

    def test_generator_cannot_change_main_migrations(self):
        self.write("migrations/0009_feature.sql", "select 1;\n")
        self.write("ops/result.generated.json", '{"migration":"0009_feature.sql"}\n')
        self.write("tools/render.py", "import pathlib\npathlib.Path('ops/result.generated.json').write_text('{\"migration\":\"0011_feature.sql\"}')\npathlib.Path('migrations/0010_main.sql').write_text('select 666;')\n")
        self.commit("bad generator")
        before = self.tree()
        result = self.run_tool("--generator", "ops/result.generated.json", json.dumps([sys.executable, "tools/render.py"]), ok=False)
        self.assertIn("applied main migration", result["error"])
        self.assertEqual(self.tree(), before)

    def test_failed_generator_restores_source(self):
        self.write("migrations/0009_feature.sql", "select 1;\n")
        self.write("ops/result.generated.json", '{"migration":"0009_feature.sql"}\n')
        self.write("tools/render.py", "import pathlib,sys\npathlib.Path('ops/result.generated.json').write_text('partial')\nsys.exit(1)\n")
        self.commit("broken generator")
        before = self.tree()
        self.run_tool("--generator", "ops/result.generated.json", json.dumps([sys.executable, "tools/render.py"]), ok=False)
        self.assertEqual(self.tree(), before)

    def test_invalid_snapshot_encoding_restores_every_file(self):
        snapshot = "COPY public.schema_migrations (filename, sha256) FROM stdin;\n0010_main.sql\tmain-sha\n\\.\n"
        self.write("db/schema.sql", snapshot)
        self.commit("main snapshot")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("migrations/0009_feature.sql", "select 1;\n")
        self.write("ops/migration-shadow.py", "import pathlib\npathlib.Path('db/schema.sql').write_bytes(bytes([255]))\n")
        self.commit("invalid snapshot generator")
        before = self.tree()
        result = self.run_tool(ok=False)
        self.assertIn("decode", result["error"])
        self.assertEqual(self.tree(), before)

    def test_cancelled_generator_and_descendants_stop_before_restoration(self):
        self.cancel_generator(signal.SIGINT)

    def test_terminated_generator_and_descendants_stop_before_restoration(self):
        self.cancel_generator(signal.SIGTERM)

    def cancel_generator(self, cancellation):
        scratch = tempfile.TemporaryDirectory(prefix="renumber-cancel-")
        self.addCleanup(scratch.cleanup)
        ready = Path(scratch.name) / "ready"
        self.write("migrations/0009_feature.sql", "-- GENERATED by own renderer\nselect 1;\n")
        child = "import pathlib,signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); pathlib.Path(" + repr(str(ready)) + ").write_text('ready'); time.sleep(0.7); pathlib.Path('migrations/0011_feature.sql').write_text('late write')"
        self.write("tools/render.py", "import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c'," + repr(child) + "],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\ntime.sleep(30)\n")
        self.commit("interruptible generator")
        before = self.tree()
        command = json.dumps([sys.executable, "tools/render.py"])
        process = subprocess.Popen([sys.executable, str(TOOL), "--repo", str(self.repo), "--generator", "migrations/0009_feature.sql", command], cwd=self.repo, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "generator never became ready")
            os.killpg(process.pid, cancellation)
            process.communicate(timeout=5)
            self.assertNotEqual(process.returncode, 0)
            time.sleep(0.8)
            self.assertEqual(self.tree(), before)
            self.assertEqual(self.run_tool("--dry-run", "--generator", "migrations/0009_feature.sql", command)["renames"], {"0009_feature.sql": "0011_feature.sql"})
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()

    def test_failed_multioutput_generator_restores_declared_unchanged_output(self):
        self.write("migrations/0009_feature.sql", "select 1;\n")
        self.write("ops/result.generated.json", '{"migration":"0009_feature.sql"}\n')
        self.write("ops/second.generated.json", '{"checksum":"before"}\n')
        self.write("tools/render.py", "import pathlib,sys\npathlib.Path('ops/result.generated.json').write_text('partial')\npathlib.Path('ops/second.generated.json').write_text('partial')\nsys.exit(1)\n")
        self.commit("broken multioutput generator")
        before = self.tree()
        command = json.dumps([sys.executable, "tools/render.py"])
        self.run_tool("--generator", "ops/result.generated.json", command,
                      "--generator", "ops/second.generated.json", command, ok=False)
        self.assertEqual(self.tree(), before)

    def test_single_argument_generator_reports_failure_as_json(self):
        self.write("migrations/0009_feature.sql", "select 1;\n")
        self.write("ops/result.generated.json", '{"migration":"0009_feature.sql"}\n')
        self.commit("single argv generator")
        self.assertIn("allocated references", self.run_tool("--generator", "ops/result.generated.json", '["true"]', ok=False)["error"])

    def test_pending_identity_in_main_ledger_refuses(self):
        self.write("db/schema.sql", "COPY public.schema_migrations (filename, sha256) FROM stdin;\n0009_feature.sql\tapplied-sha\n\\.\n")
        self.commit("applied legacy identity")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("migrations/0009_feature.sql", "select 1;\n")
        self.commit("feature")
        before = self.tree()
        self.assertIn("applied main migration", self.run_tool(ok=False)["error"])
        self.assertEqual(self.tree(), before)

    def test_branch_preview_without_checkout(self):
        self.write("migrations/0010_feature.sql", "select 1;\n")
        self.commit("feature")
        branch = self.git("rev-parse", "HEAD")
        self.git("switch", "-q", "main")
        self.write("migrations/0011_new_main.sql", "select 2;\n")
        self.commit("main advanced")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        before = self.tree()
        result = self.run_tool("--dry-run", "--branch", branch)
        self.assertTrue(result["requires_rebase"])
        self.assertEqual(result["renames"], {"0010_feature.sql": "0012_feature.sql"})
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.git("branch", "--show-current"), "main")

    def scac_fixture(self, *, sorted_keys=False):
        chain = {"versions": [{"number": 1, "version": "scac-mutation-registry.v1", "migration": "migrations/0010_main.sql"}], "atomic_groups": [], "strict_atomic_groups": []}
        self.write("ops/config/scac-registry-chain.json", json.dumps(chain))
        self.write("ops/config/scac-registry-source-inventory-fixtures.v1.json", '{"patches":[]}')
        self.write("ops/config/scac-registry-full-entry-set-seals.json", '{}')
        self.write("mcp-server/src/scac-mutation-registry.current.generated.js", "// main runtime\n")
        self.write("ops/registry-history.mjs", "export function historicalRows(number) { return [{number}]; }\n")
        self.write("ops/integration-generation.mjs", "import fs from 'node:fs'; export async function writeIntegratedArtifact(path,bytes) {fs.writeFileSync(path,bytes);}\n")
        self.write("ops/registry-chain.mjs", "export {preservesRegistryChainHistory} from " + json.dumps((ROOT / "ops/registry-chain.mjs").as_uri()) + ";\n" + """export function appendSuccessor(p) {
          const domain=p.domainMigration[0];
          const sql='-- GENERATED by own renderer\\n-- '+domain.filename+'\\nselect 2;\\n';
          const current={number:2,version:'v2',migration:'migrations/'+domain.successor_filename,atomic_pair:[domain.filename,domain.successor_filename]};
          return {sql,runtime:'// GENERATED by own renderer\\n',chain:{...p.chain,versions:[...p.chain.versions,current]},fixture:{rendered:true},seals:{rendered:true}};
        }\n""")
        self.commit("main registry fixture")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("migrations/0008_domain.sql", "select 1;\n")
        self.write("migrations/0009_feature_scac_successor.sql", "-- GENERATED by own renderer\n-- 0008_domain.sql\nselect 2;\n")
        group = ["0008_domain.sql","0009_feature_scac_successor.sql"]
        chain["versions"].append({"number":2,"version":"scac-mutation-registry.v2","predecessor":"scac-mutation-registry.v1","migration":"migrations/0009_feature_scac_successor.sql","atomic_pair":group,"strict_atomic":True,"catalog":{},"entry_set_digest":"seal"})
        chain["atomic_groups"].append(group)
        chain["strict_atomic_groups"].append(group)
        self.write("ops/config/scac-registry-chain.json", json.dumps(chain, sort_keys=sorted_keys))
        self.commit("pending registry fixture")
        return chain

    def test_scac_uses_owning_renderers_for_sql_and_chain(self):
        chain = self.scac_fixture()
        result = self.run_tool()
        self.assertEqual(result["renames"], {"0008_domain.sql":"0011_domain.sql","0009_feature_scac_successor.sql":"0012_feature_scac_successor.sql"})
        self.assertEqual((self.repo / "migrations/0012_feature_scac_successor.sql").read_text(), "-- GENERATED by own renderer\n-- 0011_domain.sql\nselect 2;\n")
        generated_chain = json.loads((self.repo / "ops/config/scac-registry-chain.json").read_text())
        self.assertEqual(generated_chain["versions"][0], chain["versions"][0])
        self.assertEqual(generated_chain["versions"][-1]["atomic_pair"], ["0011_domain.sql","0012_feature_scac_successor.sql"])
        self.assertEqual(json.loads((self.repo / "ops/config/scac-registry-source-inventory-fixtures.v1.json").read_text()), {"rendered":True})

    def test_scac_history_accepts_equivalent_object_key_order(self):
        chain = self.scac_fixture(sorted_keys=True)
        self.run_tool()
        observed = json.loads((self.repo / "ops/config/scac-registry-chain.json").read_text())
        self.assertEqual(observed["versions"][0], chain["versions"][0])
        self.assertEqual(observed["versions"][-1]["migration"], "migrations/0012_feature_scac_successor.sql")

    def real_scac_fixture(self, *, source_reference=False):
        chain = json.loads((ROOT / "ops/config/scac-registry-chain.json").read_text())
        predecessor = chain["versions"][-1]
        for path in [predecessor["migration"], "ops/registry-chain.mjs", "ops/registry-history.mjs",
                     "ops/integration-generation.mjs", "tools/integration_candidate.py",
                     "mcp-server/src/scac-mutation-registry.current.generated.js",
                     "ops/config/scac-registry-chain.json", "ops/config/scac-registry-full-entry-set-seals.json",
                     "ops/config/scac-registry-source-inventory-fixtures.v1.json"]:
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / path, target)
        self.env["PYTHONPATH"] = os.pathsep.join(str(ROOT / p) for p in ["ops", "tools", "."])
        self.commit("real registry predecessor")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        slot = int(Path(predecessor["migration"]).name[:4])
        old_domain = f"{slot-2:04d}_domain.sql"
        old_seal = f"{slot-1:04d}_test_scac_successor.sql"
        new_domain = f"{slot+1:04d}_domain.sql"
        new_seal = f"{slot+2:04d}_test_scac_successor.sql"
        source_path = "mcp-server/src/pending-source.js"
        if source_reference:
            self.write(source_path, "// pending source refers to " + old_domain + "\n")
        script = """import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {appendSuccessor,registryChain} from './ops/registry-chain.mjs';
import {historicalRows} from './ops/registry-history.mjs';
const row=registryChain.versions.at(-1);
const rows=historicalRows(row.number);
if (process.argv[3]) rows.push({...rows[0],ingress_key:'mcp-tool:pending-source',source_locator:process.argv[3],
source_digest:createHash('sha256').update(fs.readFileSync(process.argv[3])).digest('hex')});
rows.sort((a,b)=>a.ingress_key.localeCompare(b.ingress_key));
const result=await appendSuccessor({rows,catalog:row.catalog,entrySetDigest:row.entry_set_digest,
domainMigration:{filename:process.argv[1],sql:'select 1;\\n',successor_filename:process.argv[2]}});
process.stdout.write(JSON.stringify(result));
"""
        rendered = subprocess.run(["node", "--input-type=module", "-e", script, new_domain, new_seal, *([source_path] if source_reference else [])],
                                  cwd=self.repo, env=self.env, check=True, capture_output=True, text=True)
        result = json.loads(rendered.stdout)
        substitute = lambda text: text.replace(new_domain, old_domain).replace(new_seal, old_seal)
        self.write("migrations/"+old_domain, "-- Migration "+old_domain[:4]+"\nselect 1;\n")
        self.write("migrations/"+old_seal, substitute(result["sql"]))
        for key, path in [("chain","scac-registry-chain.json"), ("fixture","scac-registry-source-inventory-fixtures.v1.json"), ("seals","scac-registry-full-entry-set-seals.json")]:
            self.write("ops/config/"+path, substitute(json.dumps(result[key])))
        self.write("mcp-server/src/scac-mutation-registry.current.generated.js", result["runtime"])
        self.commit("stale pending real registry")
        return chain, new_domain, new_seal

    def test_scac_source_contract_change_refuses_before_mutation(self):
        self.real_scac_fixture(source_reference=True)
        before = self.tree()
        for args in [("--dry-run",), ()]:
            with self.subTest(args=args):
                result = self.run_tool(*args, ok=False)
                self.assertIn("source contract", result["error"])
                self.assertIn("mcp-server/src/pending-source.js", result["error"])
                self.assertEqual(self.tree(), before)
                fixture = json.loads((self.repo / "ops/config/scac-registry-source-inventory-fixtures.v1.json").read_text())
                contract = next(row for row in fixture["patches"][-1]["upsert"] if row["ingress_key"] == "mcp-tool:pending-source")
                self.assertEqual(contract["source_digest"], hashlib.sha256((self.repo / contract["source_locator"]).read_bytes()).hexdigest())

    def test_generated_scac_source_contract_refuses_before_mutation(self):
        _, new_domain, _ = self.real_scac_fixture(source_reference=True)
        source_path = "mcp-server/src/pending-source.js"
        command = json.dumps([sys.executable, "-c", "from pathlib import Path; Path(" + repr(source_path) + ").write_text(" + repr("// pending source refers to " + new_domain + "\n") + ")"])
        before = self.tree()
        result = self.run_tool("--generator", source_path, command, ok=False)
        self.assertIn("source contract", result["error"])
        self.assertEqual(self.tree(), before)

    def test_real_scac_renderers_refresh_dependency_checksums(self):
        chain, new_domain, new_seal = self.real_scac_fixture()
        for filename, mutate in [
            ("scac-registry-chain.json", lambda data: data["strict_atomic_groups"].pop(0)),
            ("scac-registry-full-entry-set-seals.json", lambda data: data.update({next(iter(data)):"sha256:"+"0"*64})),
            ("scac-registry-source-inventory-fixtures.v1.json", lambda data: data["patches"][0].update(reason="rewritten history")),
        ]:
            with self.subTest(filename=filename):
                target = self.repo / "ops/config" / filename
                original = target.read_bytes()
                altered = json.loads(original)
                mutate(altered)
                target.write_text(json.dumps(altered))
                before = self.tree()
                self.assertIn("applied registry history", self.run_tool("--dry-run", ok=False)["error"])
                self.assertEqual(self.tree(), before)
                target.write_bytes(original)
        self.run_tool()
        domain = (self.repo / "migrations" / new_domain).read_text()
        sql = (self.repo / "migrations" / new_seal).read_text()
        self.assertIn("filename='"+new_domain+"' and sha256='"+hashlib.sha256(domain.encode()).hexdigest()+"'", sql)
        generated_chain = json.loads((self.repo / "ops/config/scac-registry-chain.json").read_text())
        self.assertEqual(generated_chain["versions"][:-1], chain["versions"])
        self.assertEqual(generated_chain["versions"][-1]["migration_sha256"], hashlib.sha256(sql.encode()).hexdigest())
        self.assertEqual(generated_chain["versions"][-1]["artifact_sha256"], hashlib.sha256((self.repo / "mcp-server/src/scac-mutation-registry.current.generated.js").read_bytes()).hexdigest())
        before = self.tree()
        self.assertEqual(self.run_tool()["renames"], {})
        self.assertEqual(self.tree(), before)


if __name__ == "__main__":
    unittest.main()
