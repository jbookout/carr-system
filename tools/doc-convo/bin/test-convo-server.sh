#!/usr/bin/env bash
set -euo pipefail

BIN="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
SERVER_PID=""
CONTEXT="$BIN/../assets/hot-context.md"
MADE_CONTEXT=0
cleanup() {
  [[ -n "$SERVER_PID" ]] && kill "$SERVER_PID" 2>/dev/null || true
  [[ -n "$SERVER_PID" ]] && wait "$SERVER_PID" 2>/dev/null || true
  [[ "$MADE_CONTEXT" == 1 ]] && rm -f "$CONTEXT"
  rm -rf "$TMP"
}
trap cleanup EXIT

if [[ ! -e "$CONTEXT" ]]; then
  mkdir -p "$(dirname "$CONTEXT")"
  printf '# test hot context\n' >"$CONTEXT"
  MADE_CONTEXT=1
fi

cat >"$TMP/ffmpeg" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == *volumedetect* ]]; then
  echo 'mean_volume: -20.0 dB' >&2
  exit 0
fi
out="${@: -1}"
dd if=/dev/zero of="$out" bs=20001 count=1 2>/dev/null
trap 'exit 0' INT TERM
while :; do sleep 1; done
EOF
cat >"$TMP/afplay" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$TMP/ffmpeg" "$TMP/afplay"

PATH="$TMP:$PATH" DOC_MIC_DEVICE=:0 python3 "$BIN/convo-server.py" >"$TMP/server.log" 2>&1 &
SERVER_PID=$!
for _ in {1..50}; do
  curl -fsS http://127.0.0.1:4680/state >"$TMP/state" && break
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    cat "$TMP/server.log" >&2
    exit 1
  fi
  sleep 0.1
done
[[ -s "$TMP/state" ]] || { cat "$TMP/server.log" >&2; exit 1; }
python3 - "$TMP/state" <<'PY'
import json, sys
assert json.load(open(sys.argv[1]))["state"] == "idle"
PY
# GET / serves the panel when present, a designed 503 when not — both valid.
STATUS=$(curl -sS -o "$TMP/panel" -w '%{http_code}' http://127.0.0.1:4680/)
if [[ "$STATUS" == 200 ]]; then
  grep -q '<title>Dr. CRE</title>' "$TMP/panel"
else
  [[ "$STATUS" == 503 ]]
  grep -q 'panel missing:' "$TMP/panel"
fi

curl -NsS --max-time 8 http://127.0.0.1:4680/events >"$TMP/events" &
EVENTS_PID=$!
sleep 0.2
curl -fsS -X POST -H 'Content-Type: application/json' -d '{"action":"start"}' http://127.0.0.1:4680/talk >/dev/null
sleep 0.2
curl -fsS -X POST -H 'Content-Type: application/json' -d '{"action":"stop"}' http://127.0.0.1:4680/talk >/dev/null
for _ in {1..50}; do
  grep -q 'data: {"state": "idle"}' "$TMP/events" && \
    grep -q 'event: turn' "$TMP/events" && \
    grep -q 'event: timing' "$TMP/events" && break
  sleep 0.1
done
kill "$EVENTS_PID" 2>/dev/null || true
wait "$EVENTS_PID" 2>/dev/null || true
# Mocked mic yields a heard-nothing turn: listening -> thinking -> idle plus a
# turn event. rendering only appears on real brain turns (text lands first,
# voice follows — the text-before-speak contract).
for state in listening thinking idle; do
  grep -q "data: {\"state\": \"$state\"}" "$TMP/events"
done
grep -q 'event: turn' "$TMP/events"
grep -q 'event: timing' "$TMP/events"
python3 - "$BIN/../assets/turn-timings.jsonl" <<'PY'
import json, sys
timing = json.loads(open(sys.argv[1]).read().splitlines()[-1])
expected = {
    "mic_open", "record_stop", "volume_check", "stt", "reflex_check",
    "brain_ttfs", "brain_total", "tts_first_audio", "tts_total", "total_ms",
}
assert set(timing) == expected
assert all(isinstance(value, (int, float)) for value in timing.values())
PY
echo "convo-server smoke test passed"
