import io
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import github_app_token as app


class GitHubAppTokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.pem = cls.key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="github-app-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.config = self.home / "config.json"
        self.write_config()
        key = self.home / ".config/carr/github-app.pem"
        key.parent.mkdir(parents=True)
        key.write_bytes(self.pem)
        key.chmod(0o600)
        self.addCleanup(patch.stopall)
        patch.object(app, "CONFIG_PATH", self.config).start()
        patch.object(Path, "home", return_value=self.home).start()
        patch.object(app.time, "time", return_value=2000000000).start()
        patch.dict(os.environ, {"GH_TOKEN": "synthetic-user", "GITHUB_TOKEN": "synthetic-user",
                                "GH_HOST": "synthetic.enterprise"}).start()
        self.http = patch.object(app.urllib.request, "urlopen").start()
        self.http.side_effect = self.response
        self.requests = []

    def write_config(self, **overrides):
        config = {"app_id": 5199739, "client_id": "Iv23liV6joa2rIh9RjPj",
                  "repository": "jbookout/carr-system", "key_file": "github-app.pem",
                  "reserve": 500}
        config.update(overrides)
        self.config.write_text(json.dumps(config))

    def response(self, request, **kwargs):
        import jwt
        claims = jwt.decode(request.headers["Authorization"].split(" ", 1)[1],
                            self.key.public_key(), algorithms=["RS256"],
                            options={"verify_exp": False, "verify_iat": False})
        self.assertEqual(claims, {"iat": 1999999940, "exp": 2000000540, "iss": "5199739"})
        self.requests.append(request)
        if request.full_url.endswith("/installation"):
            result = {"id": 123}
        else:
            self.assertEqual(request.get_method(), "POST")
            result = {"token": "synthetic-app", "expires_at": "2033-05-18T04:33:20Z"}
        return io.BytesIO(json.dumps(result).encode())

    def test_mints_verified_rs256_and_reuses_private_cache(self):
        env = app.gh_env()
        self.assertTrue(env["GH_TOKEN"] == "synthetic-app")
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["GH_HOST"], "github.com")
        self.assertTrue(os.environ["GH_TOKEN"] == "synthetic-user")
        self.assertTrue(app.gh_env()["GH_TOKEN"] == "synthetic-app")
        self.assertEqual(len(self.requests), 2)
        cache = self.home / ".cache/carr/github-app-token.json"
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)

    def test_refreshes_at_five_minute_boundary(self):
        app.gh_env()
        with patch.object(app.time, "time", return_value=2000003300):
            self.http.side_effect = lambda request, **kwargs: io.BytesIO(json.dumps(
                {"id": 123} if request.full_url.endswith("/installation") else
                {"token": "synthetic-refreshed", "expires_at": "2033-05-18T05:33:20Z"}).encode())
            self.assertTrue(app.gh_env()["GH_TOKEN"] == "synthetic-refreshed")
        self.assertEqual(self.http.call_count, 4)

    def test_cache_is_bound_to_repository_and_key(self):
        app.gh_env()
        self.write_config(repository="jbookout/doctorcre-app")
        app.gh_env()
        self.assertEqual(self.http.call_count, 4)
        with (self.home / ".config/carr/github-app.pem").open("ab") as key:
            key.write(b"\n")
        app.gh_env()
        self.assertEqual(self.http.call_count, 6)

    def test_configuration_errors_stop_calls_without_reading_credentials(self):
        for content in ("{", "{}", '{"app_id":"no"}', "[]"):
            self.config.write_text(content)
            with self.assertRaises(app.GitHubAppError):
                app.gh_env()
            self.http.assert_not_called()

    def test_missing_key_falls_back_to_stored_login_with_one_log_line(self):
        self.write_config(key_file="absent.pem")
        with self.assertLogs(app.LOG, level="WARNING") as logs:
            env = app.gh_env()
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("private key missing; falling back to user login", logs.output[0])
        self.http.assert_not_called()

    def test_missing_or_suspended_installation_logs_fallback(self):
        for unavailable in (urllib.error.HTTPError("synthetic", 404, "missing", {}, None),
                            io.BytesIO(b'{"id":123,"suspended_at":"2033-01-01T00:00:00Z"}')):
            self.http.side_effect = unavailable if isinstance(unavailable, Exception) else None
            self.http.return_value = unavailable
            with self.assertLogs(app.LOG, level="WARNING") as logs:
                self.assertNotIn("GH_TOKEN", app.gh_env())
            self.assertEqual(len(logs.output), 1)
            self.assertIn("falling back to user login", logs.output[0])

    def test_api_failure_never_uses_personal_allowance_or_leaks_details(self):
        self.http.side_effect = urllib.error.URLError("synthetic-secret-error")
        with self.assertRaises(app.GitHubAppError) as error:
            app.gh_env()
        self.assertNotIn("synthetic-secret-error", str(error.exception))

    def test_loose_cache_is_refreshed_and_invalid_response_is_refused(self):
        app.gh_env()
        cache = self.home / ".cache/carr/github-app-token.json"
        cache.chmod(0o644)
        app.gh_env()
        self.assertEqual(self.http.call_count, 4)
        self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o600)
        self.http.side_effect = lambda *args, **kwargs: io.BytesIO(b'{"id":123}')
        with patch.object(app.time, "time", return_value=2000003300):
            with self.assertRaises(app.GitHubAppError):
                app.gh_env()

    def test_token_with_less_than_five_minutes_remaining_is_refused(self):
        self.http.side_effect = lambda request, **kwargs: io.BytesIO(json.dumps(
            {"id": 123} if request.full_url.endswith("/installation") else
            {"token": "synthetic-app", "expires_at": "2033-05-18T03:38:20Z"}).encode())
        with self.assertRaises(app.GitHubAppError):
            app.gh_env()

    def test_concurrent_clients_share_one_mint(self):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=4) as pool:
            envs = list(pool.map(lambda _: app.gh_env(), range(4)))
        self.assertTrue(all(env["GH_TOKEN"] == "synthetic-app" for env in envs))
        self.assertEqual(self.http.call_count, 2)

    def test_budget_pauses_if_either_pool_is_below_floor(self):
        env = {"GH_TOKEN": "synthetic-app"}
        for core, graphql, paused, reset in ((499, 900, True, 2000000100),
                                            (900, 499, True, 2000000200),
                                            (499, 499, True, 2000000200),
                                            (500, 500, False, None)):
            payload = {"resources": {"core": {"remaining": core, "reset": 2000000100},
                                      "graphql": {"remaining": graphql, "reset": 2000000200}}}
            with patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps(payload), "")) as gh:
                budget = app.remaining_budget(env=env)
            self.assertEqual((budget.core, budget.graphql, budget.paused, budget.reset),
                             (core, graphql, paused, reset))
            self.assertEqual(gh.call_args.args[0], ["gh", "api", "rate_limit"])
            self.assertIs(gh.call_args.kwargs["env"], env)
            if paused:
                self.assertTrue(budget.message.startswith("paused until "))

    def test_configurable_reserve_and_budget_error_are_explicit(self):
        self.write_config(reserve=100)
        payload = {"resources": {"core": {"remaining": 100, "reset": 2000000100},
                                  "graphql": {"remaining": 100, "reset": 2000000200}}}
        with patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(payload), "")):
            self.assertFalse(app.remaining_budget(env={}).paused)
            self.assertTrue(app.remaining_budget(env={}, reserve=101).paused)
        for output, status in (("{}", 0), ("synthetic-secret-error", 1)):
            with patch.object(app.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], status, output, "synthetic-secret-error")):
                with self.assertRaises(app.GitHubAppError) as error:
                    app.remaining_budget(env={})
                self.assertNotIn("synthetic-secret-error", str(error.exception))


class IntakeContractTests(unittest.TestCase):
    def test_guard_allows_named_intake_without_executing_it(self):
        spec = importlib.util.spec_from_file_location("github_app_guard", ROOT / "hooks/guard-unattended.py")
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        for command in ("./bin/github-app-key-intake.sh", "bash bin/github-app-key-intake.sh"):
            self.assertIsNone(guard.check(command))

    def test_intake_keeps_secret_resolution_inside_named_script(self):
        script = (ROOT / "bin/github-app-key-intake.sh").read_text()
        self.assertIn('"$HOME/Downloads"', script)
        self.assertIn("carr-watchdog-jbookout*.private-key.pem", script)
        self.assertIn("mv ", script)
        self.assertIn("chmod 600", script)
        self.assertNotIn("cp ", script)
        self.assertNotIn("cat ", script)
        self.assertNotIn("set -x", script)


if __name__ == "__main__":
    unittest.main()
