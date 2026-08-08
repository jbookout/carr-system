#!/usr/bin/env python3
"""harvest-voice.py — Voice Harvest for Doc (loop: Joe's own Claude questions
become Doc's phrase cache AND a future voice-training corpus).

Joe's idea: mine the real questions he has actually asked across his Claude
history, have Doc answer them in character, then split every answer into
sentences and cache the FACT-FREE ones (frames, hedges, general reasoning) as
instant-replay audio. FACT-BEARING sentences (a digit, a date, a dollar
figure, a client or vendor name) are never cached live — Doc must always say
those fresh from the real record, never replay a stale name or number. They
still have value, so with --corpus they render to a separate directory for a
future voice-training pass; the live engine never reads that directory.

Four phases, run in order:

  1. MINING       — pull every human message out of Joe's account export(s),
                    keep the ones that read as a business question, dedup,
                    cap at --limit. A CURATED bank of 40 verbatim questions
                    (assets/harvest-curated-seed.json, Joe's real workflows —
                    deal status, critical dates, negotiation, clients,
                    vendors, comps, judgment calls, daily planning) rides
                    alongside the mined set through every later phase,
                    tagged "curated" so provenance stays inspectable.
                    -> assets/harvest-questions.json (mined)
                    -> assets/harvest-questions-curated.json (curated)

  2. GENERATION   — ask Doc (convo_core.ask_brain_streaming, real preamble +
                    hot-context, same as the live voice loop) each question.
                    Costs a brain call per question, no GPU. Replies are
                    cached by exact question text, so a second run never
                    re-asks a question it already has an answer for.
                    -> assets/harvest-replies.jsonl

  3. THE SPLIT    — split every reply into sentences (speak.split_sentences)
                    and classify each one FACT-BEARING or FACT-FREE. The
                    classifier is deliberately over-inclusive: any digit,
                    currency symbol, month name, weekday name, or a
                    capitalized word that isn't sentence-initial and isn't
                    the pronoun "I" trips FACT-BEARING. Written out in full
                    so a human can audit the calls before anything caches.
                    -> assets/harvest-frames.txt   (fact-free)
                    -> assets/harvest-factual.txt  (fact-bearing)
                    -> assets/harvest-sentences.jsonl (full detail + reasons)

  4. RENDER       — ONLY fact-free sentences ever reach the live phrase cache
                    (assets/phrases/, via speak.prepare — same cache the
                    engine replays instantly). Fact-bearing sentences are
                    rendered ONLY with --corpus, and then to a separate
                    directory (assets/harvest-corpus/) the engine never
                    reads. Serial (the render daemon serializes anyway),
                    content-addressed (a sha1 of the normalized text), so a
                    second pass — --resume or not — never redoes a line
                    whose .wav already exists.

Usage:
  harvest-voice.py --dry-run                 mine + generate + classify only
  harvest-voice.py                            + render frames to the cache
  harvest-voice.py --corpus                   + also render facts to corpus/
  harvest-voice.py --resume                   note this is a continuation
  harvest-voice.py --limit 20                 shrink both sources for a test
  harvest-voice.py --export a.json --export b.zip   explicit export sources

Safety rule this file exists to enforce: a fact-bearing sentence must NEVER
reach assets/phrases/. See bin/test-harvest.sh.
"""

import argparse
import hashlib
import json
import pathlib
import re
import sys
import time
import unicodedata
import zipfile
from difflib import SequenceMatcher

BIN = pathlib.Path(__file__).resolve().parent
TOOL = BIN.parent
ASSETS = TOOL / "assets"

sys.path.insert(0, str(BIN))
import speak       # noqa: E402  — pure stdlib, safe to import without .venv-tts
import convo_core   # noqa: E402

QUESTIONS_MINED = ASSETS / "harvest-questions.json"
QUESTIONS_CURATED_SEED = ASSETS / "harvest-curated-seed.json"
QUESTIONS_CURATED = ASSETS / "harvest-questions-curated.json"
REPLIES = ASSETS / "harvest-replies.jsonl"
SENTENCES = ASSETS / "harvest-sentences.jsonl"
FRAMES_TXT = ASSETS / "harvest-frames.txt"
FACTUAL_TXT = ASSETS / "harvest-factual.txt"
CORPUS_DIR = ASSETS / "harvest-corpus"
HARVEST_SESSION_FILE = ASSETS / ".harvest-brain-session-id"
HISTORY_SOURCE_DIR = ASSETS / "history-source"

DEFAULT_LIMIT = 60
MIN_QUESTION_CHARS = 15
MAX_QUESTION_CHARS = 500
DEDUP_THRESHOLD = 0.85


# ============================================================ Phase 1: MINING

QUESTION_LEAD = re.compile(
    r"^(what|who|whom|whose|when|where|why|how|which|is|isn't|are|aren't|"
    r"was|were|do|don't|does|doesn't|did|didn't|can|can't|could|couldn't|"
    r"should|shouldn't|would|wouldn't|will|won't|am i|any|"
    r"do we|does anyone|is there|are there)\b",
    re.IGNORECASE,
)

CODE_OR_PASTE_MARKERS = (
    "```", "traceback", "stack trace", "npm ", "npm install", "pip install",
    "git clone", "git commit", "def ", "class ", "import ", "console.log",
    "function(", "select * ", " where ", "http://", "https://",
    "not supported on your current device",
    # Make.com / automation-wiring debugging — not a business question,
    # just plumbing. Doc's persona (deal board, leads, triage) has no
    # opinion on any of this, so it makes poor harvest material even when
    # it happens to be phrased as a question.
    "module ", "connection point", "escapejson", "escape json",
    "autocorrects to", "webhook", " connector", "google sheet id",
)

TRIVIAL_MESSAGES = {
    "test", "testing", "ok", "okay", "k", "yes", "no", "yep", "nope",
    "thanks", "thank you", "thx", "great", "cool", "nice", "perfect",
    "got it", "sounds good", "it worked", "it worked!", "worked",
    "makes sense", "understood", "hi", "hello", "hey",
}


def _read_export_file(path):
    """Return the parsed conversations.json contents from PATH — a .json
    file, a directory containing one, or a claude.ai export .zip bundle
    (read in memory; nothing is extracted to disk)."""
    p = pathlib.Path(path)
    if p.is_dir():
        p = p / "conversations.json"
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as zf:
            return json.loads(zf.read("conversations.json"))
    return json.loads(p.read_text())


def default_export_paths():
    """Discover export sources with no hardcoded personal path in this file.
    Checked in order: (1) assets/history-source/ — a local, gitignored drop
    folder Joe can leave export files in; (2) the newest claude.ai export
    bundles in ~/Downloads (data-*-batch-*.zip, the standard "export my
    data" download naming)."""
    found = []
    if HISTORY_SOURCE_DIR.is_dir():
        found.extend(sorted(HISTORY_SOURCE_DIR.glob("*.json")))
        found.extend(sorted(HISTORY_SOURCE_DIR.glob("*.zip")))
        found.extend(sorted(HISTORY_SOURCE_DIR.glob("*/conversations.json")))
    if not found:
        downloads = pathlib.Path.home() / "Downloads"
        candidates = sorted(
            downloads.glob("data-*-batch-*.zip"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        found = candidates[:2]
    return [str(p) for p in found]


def load_conversations(paths):
    """Yield (text, conversation_uuid, conversation_name, created_at,
    message_uuid) for every human message across the given export sources."""
    for path in paths:
        data = _read_export_file(path)
        for conv in data:
            conv_uuid = conv.get("uuid", "")
            conv_name = conv.get("name", "")
            for msg in conv.get("chat_messages", []):
                if msg.get("sender") != "human":
                    continue
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                yield (text, conv_uuid, conv_name, msg.get("created_at", ""),
                       msg.get("uuid", ""))


def looks_like_code_or_paste(text):
    lower = text.lower()
    if any(marker in lower for marker in CODE_OR_PASTE_MARKERS):
        return True
    brace_count = sum(text.count(c) for c in "{}[]")
    if brace_count >= 6:
        return True
    if text.count("/") >= 6:
        return True
    return False


def is_probable_question(text):
    stripped = text.strip()
    if stripped.endswith("?"):
        return True
    return bool(QUESTION_LEAD.match(stripped))


def normalize_for_dedup(text):
    t = unicodedata.normalize("NFKD", text.lower())
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_near_duplicate(candidate_norm, seen_norms, threshold=DEDUP_THRESHOLD):
    for norm in seen_norms:
        if SequenceMatcher(None, candidate_norm, norm).ratio() >= threshold:
            return True
    return False


def mine_questions(export_paths, limit):
    rows = list(load_conversations(export_paths))
    rows.sort(key=lambda r: r[3] or "")  # chronological

    seen_msg_uuids = set()
    seen_norms = []
    mined = []
    for text, conv_uuid, conv_name, created_at, msg_uuid in rows:
        if msg_uuid and msg_uuid in seen_msg_uuids:
            continue
        if msg_uuid:
            seen_msg_uuids.add(msg_uuid)

        if not (MIN_QUESTION_CHARS <= len(text) <= MAX_QUESTION_CHARS):
            continue
        if text.strip().strip(".!").lower() in TRIVIAL_MESSAGES:
            continue
        if looks_like_code_or_paste(text):
            continue
        if not is_probable_question(text):
            continue

        norm = normalize_for_dedup(text)
        if not norm or is_near_duplicate(norm, seen_norms):
            continue
        seen_norms.append(norm)

        mined.append({
            "question": text,
            "source": "mined",
            "conversation_uuid": conv_uuid,
            "conversation_name": conv_name,
            "created_at": created_at,
        })
        if len(mined) >= limit:
            break

    QUESTIONS_MINED.write_text(json.dumps(mined, indent=2) + "\n")
    return mined


def load_curated(limit):
    """The curated bank rides alongside the mined set (Joe's addition, since
    his Claude history skews system-building rather than deal work). Same
    dedup pass, capped at the same --limit so a shrunk test run shrinks both
    sources together; the real run (default 60) always includes the full
    40-question bank."""
    if not QUESTIONS_CURATED_SEED.exists():
        QUESTIONS_CURATED.write_text("[]\n")
        return []

    seed = json.loads(QUESTIONS_CURATED_SEED.read_text())
    seen_norms = []
    curated = []
    for row in seed:
        text = (row.get("question") or "").strip()
        if not text:
            continue
        norm = normalize_for_dedup(text)
        if not norm or is_near_duplicate(norm, seen_norms):
            continue
        seen_norms.append(norm)
        curated.append({
            "question": text,
            "source": "curated",
            "category": row.get("category", ""),
        })
        if len(curated) >= limit:
            break

    QUESTIONS_CURATED.write_text(json.dumps(curated, indent=2) + "\n")
    return curated


# ======================================================= Phase 2: GENERATION

def load_existing_replies():
    replies = {}
    if REPLIES.exists():
        with REPLIES.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                replies[row.get("question", "")] = row
    return replies


def generate_replies(questions):
    """Ask Doc every question, in order. Replies are cached to disk by exact
    question text as they arrive, so an interrupted run (or a second run
    over the same question set) never re-asks a question it already has an
    answer for — this is the natural resume behavior for this phase."""
    existing = load_existing_replies()

    system_prompt = convo_core.refresh_hot_context()
    if system_prompt is None:
        print("harvest: no hot-context snapshot available — cannot ask Doc",
              file=sys.stderr)
        sys.exit(1)

    # Isolate the harvest's brain session from Doc's LIVE session. Never
    # read or write assets/.brain-session-id — that file belongs to whatever
    # conversation is running at Joe's desk right now.
    convo_core.SESSION_FILE = HARVEST_SESSION_FILE

    results = []
    new_count = 0
    REPLIES.parent.mkdir(parents=True, exist_ok=True)
    with REPLIES.open("a") as out:
        for i, q in enumerate(questions, 1):
            question = q["question"]
            if question in existing:
                results.append(existing[question])
                print(f"  [{i}/{len(questions)}] cached reply — {question[:60]!r}")
                continue

            t0 = time.time()
            reply, proc = convo_core.ask_brain_streaming(question, system_prompt)
            elapsed = time.time() - t0
            row = {
                "question": question,
                "source": q.get("source", "mined"),
                "reply": reply,
                "returncode": proc.returncode,
                "elapsed_s": round(elapsed, 1),
            }
            out.write(json.dumps(row) + "\n")
            out.flush()
            results.append(row)
            new_count += 1
            status = "ok" if proc.returncode == 0 else "ERROR"
            print(f"  [{i}/{len(questions)}] {status} ({elapsed:.1f}s) — "
                  f"{question[:60]!r}")

    convo_core._BRAIN.close()
    return results, new_count


# ============================================================ Phase 3: SPLIT

NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand", "million", "billion", "dozen", "half", "quarter", "third",
    "first", "second", "fourth", "fifth", "sixth", "seventh", "eighth",
    "ninth", "tenth", "once", "twice",
}
QUANTITY_WORDS = {
    "several", "few", "many", "most", "all", "none", "both", "couple",
    "handful", "dozens", "multiple", "majority", "minority", "each",
    "every", "some", "any",
}
MONTHS = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
}
WEEKDAYS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri",
    "sat", "sun",
}
CURRENCY_CHARS = set("$€£¢")
# The small allowlist: common words legitimately capitalized mid-sentence.
# Only the first-person pronoun qualifies in ordinary English prose — every
# other mid-sentence capital is treated as a probable proper noun.
CAP_ALLOWLIST = {"i"}


def strip_card_line(text):
    """CARD lines are structured data for the screen, never spoken — and
    they are guaranteed fact-bearing (three+ figures by law of the
    preamble), so they never belong in either sentence class."""
    idx = text.find("\nCARD: ")
    if idx >= 0:
        return text[:idx]
    if text.startswith("CARD: "):
        return ""
    return text


def _word_tokens(sentence):
    return re.findall(r"[A-Za-z']+", sentence)


def classify_sentence(sentence):
    """Return (is_fact_bearing, reasons). Deliberately over-inclusive per
    spec: a false FRAME classification would let a stale fact get replayed
    as if Doc just looked it up, which is the one mistake this pipeline
    exists to prevent. A false FACT classification just means one more
    ordinary sentence renders to the corpus dir (or nowhere, by default)
    instead of the live cache — cheap, safe, reversible."""
    reasons = []

    if re.search(r"\d", sentence):
        reasons.append("digit")
    if any(ch in CURRENCY_CHARS for ch in sentence):
        reasons.append("currency_symbol")

    lowered_words = {w.lower() for w in _word_tokens(sentence)}
    # SPELLED-OUT NUMBERS — the gap that let "Four deals are in closing right
    # now" through as cacheable on the first run (2026-08-08). Doc speaks
    # numbers as words by design (the voice preamble instructs it), so a
    # digit check alone is blind to exactly the sentences this guard exists
    # to catch. Quantity words count too: a cached "several" ages the same
    # way a cached "four" does.
    if lowered_words & NUMBER_WORDS:
        reasons.append("number_word")
    if lowered_words & QUANTITY_WORDS:
        reasons.append("quantity_word")
    if lowered_words & MONTHS:
        reasons.append("month_name")
    if lowered_words & WEEKDAYS:
        reasons.append("weekday")

    tokens = re.findall(r"\S+", sentence)
    for idx, tok in enumerate(tokens):
        if idx == 0:
            continue  # sentence-initial capitalization is just grammar
        core = tok.strip(".,;:!?\"'()[]")
        if not core or not core[0].isupper():
            continue
        low = core.lower()
        if low in CAP_ALLOWLIST or low.startswith("i'"):
            continue
        reasons.append(f"proper_noun:{core}")
        break

    return (len(reasons) > 0, reasons)


def split_and_classify(replies):
    frames, factual, records = [], [], []
    for row in replies:
        reply_text = strip_card_line(row.get("reply", "") or "")
        for sentence in speak.split_sentences(reply_text):
            sentence = sentence.strip()
            if not sentence:
                continue
            is_fact, reasons = classify_sentence(sentence)
            records.append({
                "sentence": sentence,
                "fact_bearing": is_fact,
                "reasons": reasons,
                "question": row.get("question"),
                "source": row.get("source"),
            })
            (factual if is_fact else frames).append(sentence)

    SENTENCES.write_text(
        "".join(json.dumps(r) + "\n" for r in records)
    )
    FRAMES_TXT.write_text("".join(s + "\n" for s in frames))
    FACTUAL_TXT.write_text("".join(s + "\n" for s in factual))
    return frames, factual, records


# =========================================================== Phase 4: RENDER

def sha1_wav_name(sentence):
    return hashlib.sha1(speak.normalize(sentence).encode()).hexdigest()[:16] + ".wav"


def render_frames(frames):
    total = len(frames)
    rendered = skipped = failed = 0
    t_start = time.time()
    for i, sentence in enumerate(frames, 1):
        # Hard rule, enforced at the call site, not just by list membership:
        # refuse outright rather than silently cache a misclassified line.
        is_fact, reasons = classify_sentence(sentence)
        if is_fact:
            raise RuntimeError(
                f"harvest: refusing to cache a fact-bearing sentence "
                f"({reasons}): {sentence!r}"
            )
        wav_path = speak.PHRASES / sha1_wav_name(sentence)
        already = wav_path.exists()
        t0 = time.time()
        result = speak.prepare(sentence)
        elapsed = time.time() - t0
        if already:
            skipped += 1
            print(f"  [{i}/{total}] already cached ({elapsed:.2f}s) — "
                  f"{sentence[:60]!r}")
        elif result is not None:
            rendered += 1
            print(f"  [{i}/{total}] rendered ({elapsed:.1f}s) — "
                  f"{sentence[:60]!r}")
        else:
            failed += 1
            print(f"  [{i}/{total}] FAILED ({elapsed:.1f}s) — "
                  f"{sentence[:60]!r}", file=sys.stderr)
    print(f"frames: {rendered} rendered, {skipped} already cached, "
          f"{failed} failed — {time.time() - t_start:.1f}s total")


def render_corpus(factual):
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    original_phrases = speak.PHRASES
    speak.PHRASES = CORPUS_DIR  # redirect speak.prepare's output dir only
    try:
        total = len(factual)
        rendered = skipped = failed = 0
        t_start = time.time()
        for i, sentence in enumerate(factual, 1):
            is_fact, _reasons = classify_sentence(sentence)
            if not is_fact:
                print(f"  [{i}/{total}] skipped — not fact-bearing, does "
                      f"not belong in this list: {sentence[:60]!r}",
                      file=sys.stderr)
                continue
            wav_path = CORPUS_DIR / sha1_wav_name(sentence)
            already = wav_path.exists()
            t0 = time.time()
            result = speak.prepare(sentence)
            elapsed = time.time() - t0
            if already:
                skipped += 1
                print(f"  [{i}/{total}] already cached ({elapsed:.2f}s) — "
                      f"{sentence[:60]!r}")
            elif result is not None:
                rendered += 1
                print(f"  [{i}/{total}] rendered ({elapsed:.1f}s) — "
                      f"{sentence[:60]!r}")
            else:
                failed += 1
                print(f"  [{i}/{total}] FAILED ({elapsed:.1f}s) — "
                      f"{sentence[:60]!r}", file=sys.stderr)
        print(f"corpus: {rendered} rendered, {skipped} already cached, "
              f"{failed} failed — {time.time() - t_start:.1f}s total")
    finally:
        speak.PHRASES = original_phrases  # never leave the live cache path swapped


# =================================================================== REPORT

def print_dry_run_report(mined, curated, replies, frames, factual, records):
    print()
    print("=== DRY RUN REPORT ===")
    print(f"questions mined:             {len(mined)}")
    print(f"questions curated:           {len(curated)}")
    print(f"questions total:             {len(mined) + len(curated)}")
    print(f"replies generated:           {len(replies)}")
    print(f"sentences split:             {len(records)}")
    print(f"  fact-free  (cacheable):    {len(frames)}")
    print(f"  fact-bearing (never cached): {len(factual)}")
    print()
    print("--- sample: up to 10 FACT-FREE sentences "
          "(would render to assets/phrases/) ---")
    for s in frames[:10]:
        print(f"  · {s}")
    if not frames:
        print("  (none)")
    print()
    print("--- sample: up to 10 FACT-BEARING sentences "
          "(never cached; corpus-only with --corpus) ---")
    for s in factual[:10]:
        print(f"  · {s}")
    if not factual:
        print("  (none)")
    print()


# ==================================================================== MAIN

def main():
    parser = argparse.ArgumentParser(
        description="Voice Harvest for Doc — mine Joe's real questions, "
                     "have Doc answer them, cache the fact-free sentences.",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help=f"max questions per source (default {DEFAULT_LIMIT}); applies "
             "independently to the mined set and the curated bank, so the "
             "default always includes the full 40-question curated bank",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="mine + generate + classify, report counts and samples, "
             "render nothing",
    )
    parser.add_argument(
        "--corpus", action="store_true",
        help="also render fact-bearing sentences into "
             "assets/harvest-corpus/ (training corpus; the live engine "
             "never reads this directory). Ignored with --dry-run.",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="acknowledge this is a continuation of an interrupted run. "
             "Both the reply cache (by question text) and the render cache "
             "(by content hash) are always resume-safe; this flag mainly "
             "changes the status messages printed at each phase.",
    )
    parser.add_argument(
        "--export", action="append", default=None,
        help="path to a conversations.json export, an export .zip bundle, "
             "or a directory containing conversations.json (repeatable). "
             "Defaults to assets/history-source/ if populated, else the "
             "newest export bundles in ~/Downloads.",
    )
    args = parser.parse_args()

    export_paths = args.export or default_export_paths()
    if not export_paths:
        print("harvest: no export source found — pass --export PATH, or "
              "drop a conversations.json / export .zip into "
              f"{HISTORY_SOURCE_DIR}", file=sys.stderr)
        return 1
    missing = [p for p in export_paths if not pathlib.Path(p).exists()]
    if missing:
        print(f"harvest: export file(s) not found: {missing}", file=sys.stderr)
        return 1

    mode = "resuming" if args.resume else "starting"
    print(f"=== Phase 1: mining questions ({mode}) — "
          f"{len(export_paths)} export source(s) ===")
    for p in export_paths:
        print(f"  source: {p}")
    mined = mine_questions(export_paths, args.limit)
    curated = load_curated(args.limit)
    questions = mined + curated
    print(f"mined: {len(mined)} (cap {args.limit})   "
          f"curated: {len(curated)} (of 40 seeded)   "
          f"total: {len(questions)}")

    print(f"\n=== Phase 2: generation ({len(questions)} question(s)) ===")
    replies, new_count = generate_replies(questions)
    print(f"replies: {len(replies)} total, {new_count} newly generated, "
          f"{len(replies) - new_count} reused from cache")

    print(f"\n=== Phase 3: the split ===")
    frames, factual, records = split_and_classify(replies)
    print(f"sentences: {len(records)}   fact-free: {len(frames)}   "
          f"fact-bearing: {len(factual)}")

    if args.dry_run:
        print_dry_run_report(mined, curated, replies, frames, factual, records)
        print("dry run — nothing rendered "
              f"(would cache {len(frames)} frame(s) to assets/phrases/, "
              f"{len(factual)} fact-bearing sentence(s) NOT cached)")
        return 0

    print(f"\n=== Phase 4: render — frames -> assets/phrases/ "
          f"({mode}) ===")
    render_frames(frames)

    if args.corpus:
        print(f"\n=== Phase 4b: render — fact-bearing -> "
              f"{CORPUS_DIR} ({mode}) ===")
        render_corpus(factual)
    else:
        print(f"\n({len(factual)} fact-bearing sentence(s) not rendered — "
              "pass --corpus to build the training corpus)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
