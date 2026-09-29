"""Offline contracts for measured Flash timings and the tool-free ask command."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MeasurementTests(unittest.TestCase):
    def test_log_separates_prefill_decode_and_cache(self):
        bench = load("bench", "flash-overhead.py")
        text = """ds4-server: live kv cache miss live=16232 prompt=15095 common=13368 reason=token-mismatch
ds4-server: chat ctx=0..15095:15095 TOOLS prefill chunk 15095/15095 (100.0%) chunk=668.85 t/s avg=955.82 t/s 15.793s
ds4-server: chat ctx=0..15095:15095 TOOLS prompt done 15.793s
ds4-server: chat ctx=15095..16232:1137 gen=1137 TOOLS decoding chunk=52.99 t/s avg=55.20 t/s 20.596s
ds4-server: chat ctx=0..15095:15095 gen=1137 TOOLS finish=stop 36.391s"""
        result = bench.log_metrics(text)
        self.assertEqual(result["prefill_tokens"], 15095)
        self.assertEqual(result["prefill_s"], 15.793)
        self.assertEqual(result["decode_s"], 20.596)
        self.assertEqual(result["decode_tok_s"], 55.20)
        self.assertIsNone(result["mtp_acceptance_pct"])
        self.assertIn("common=13368", result["cache_events"][0])

    def test_mtp_acceptance_uses_counters_not_decode_rate(self):
        bench = load("bench", "flash-overhead.py")
        result = bench.log_metrics("ds4: Qwen3.8 mtp: 100 verify cycles, 75 drafts accepted (75.0%)")
        self.assertEqual(result["mtp_acceptance_pct"], 75.0)
        self.assertIsNone(result["decode_tok_s"])

    def test_mtp_diagnostic_is_bounded_and_preserves_shared_lock(self):
        bench = load("bench", "flash-overhead.py")
        from subprocess import CompletedProcess
        fake = CompletedProcess([], 0, stdout=b"1,2", stderr=b"ds4: Qwen3.8 mtp: 10 verify cycles, 7 drafts accepted (70.0%)")
        with patch.object(bench.subprocess, "run", return_value=fake) as run:
            row = bench.measure_mtp(Path("/tmp/ds4"), Path("/tmp/model.gguf"))
        self.assertEqual(row["acceptance_pct"], 70.0)
        self.assertLessEqual(run.call_args.kwargs["timeout"], 120)
        self.assertNotEqual(run.call_args.kwargs["env"]["DS4_LOCK_FILE"], "/tmp/ds4.lock")

    def test_benchmark_checks_the_answer_not_just_exit_status(self):
        bench = load("bench", "flash-overhead.py")
        self.assertTrue(bench.answer_valid(bench.SHORT, "PING"))
        self.assertFalse(bench.answer_valid(bench.SHORT, "Sorry"))
        self.assertTrue(bench.answer_valid(bench.LONG, ",".join(map(str, range(1, 201)))))
        self.assertFalse(bench.answer_valid(bench.LONG, "1, 2, 200"))

    def test_sse_usage_includes_cache_creation_and_read(self):
        bench = load("bench", "flash-overhead.py")
        raw = b'data: {"type":"message_start","message":{"usage":{"input_tokens":7,"cache_creation_input_tokens":100,"cache_read_input_tokens":200}}}\n\ndata: {"type":"message_delta","usage":{"output_tokens":17}}\n\n'
        self.assertEqual(bench.usage_metrics(raw),
                         {"prompt_tokens": 307, "cached_tokens": 200, "output_tokens": 17})

    def test_ambiguous_shared_log_is_not_attributed(self):
        bench = load("bench", "flash-overhead.py")
        result = bench.log_metrics("ds4-server: chat ctx=0..5:5 prompt start\n"
                                   "ds4-server: chat ctx=0..8:8 prompt start\n")
        self.assertFalse(result["attributable"])
        self.assertIsNone(result["prefill_s"])


class AskTests(unittest.TestCase):
    def test_direct_request_is_tool_free_thinking_off_and_bounded(self):
        fr = load("fr", "flash-run.py")
        seen = []
        def opener(req, timeout):
            seen.append((req.full_url, json.loads(req.data), timeout))
            return io.BytesIO(b'{"choices":[{"message":{"content":"PING"},"finish_reason":"stop"}]}')
        with patch.dict("os.environ", {"CARR_FLASH_URL": "http://127.0.0.1:8000"}):
            reply = fr.ask_turn("ping", max_tokens=20, opener=opener)
        self.assertEqual(reply["result"], "PING")
        url, body, timeout = seen[0]
        self.assertEqual(url, "http://127.0.0.1:8000/v1/chat/completions")
        self.assertEqual(body["max_tokens"], 20)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertNotIn("tools", body)
        self.assertLessEqual(timeout, 600)

    def test_ask_refuses_remote_and_other_loopback_ports(self):
        fr = load("fr", "flash-run.py")
        for url in ("https://example.com", "http://localhost:5432", "http://127.0.0.2:8000",
                    "http://127.0.0.1:8000@evil.example:8000", "http://localhost:8000/path"):
            with patch.dict("os.environ", {"CARR_FLASH_URL": url}), self.assertRaises(ValueError):
                fr.ask_turn("ping")

    def invoke(self, reply, *flags):
        fr = load("fr", "flash-run.py")
        out, err = io.StringIO(), io.StringIO()
        with patch.object(fr, "ask_turn", return_value=reply) as turn, patch.object(fr, "_append"), patch("sys.stdout", out), patch("sys.stderr", err):
            code = fr.main(["ask", "Return a label", *flags])
        return code, out.getvalue(), err.getvalue(), turn

    def test_json_object_success_uses_direct_desk_without_coding_attempt(self):
        code, out, _, turn = self.invoke({"status": "completed", "result": '{"label":"yes"}', "finish": "stop"}, "--json-object")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"label": "yes"})
        self.assertIn("JSON object", turn.call_args.args[0])

    def test_rejects_invalid_truncated_or_nonobject_json(self):
        for result, finish in (("not json", "stop"), ("[]", "stop"), ('{"a":1}', "length")):
            code, out, _, _ = self.invoke({"status": "completed", "result": result, "finish": finish}, "--json-object")
            self.assertEqual(code, 5)
            self.assertEqual(out, "")

    def test_server_failure_never_prints_an_answer(self):
        code, out, err, _ = self.invoke({"status": "failed", "detail": "no_answer"})
        self.assertEqual(code, 5)
        self.assertEqual(out, "")
        self.assertIn("no_answer", err)


if __name__ == "__main__":
    unittest.main()
