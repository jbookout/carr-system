#!/usr/bin/env python3
"""build-voice-corpus.py — manufacture the cleanest possible Doc corpus for cloning.

WHY THIS EXISTS (Joe, 2026-08-08): once ElevenLabs does production rendering,
render speed stops constraining the SOURCE material. "Now that we don't have to
worry about speed we can just optimize for quality." Every take budget in this
system was a speed compromise; this script spends the budget that is now free.

WHAT IT CHANGES vs render-bridges.sh's 4-take screen:

  1. BEST-OF-N, N as large as you like. Model loads once and stays warm, so the
     marginal cost of a take is a forward pass, not a process start.

  2. TWO GATES PER TAKE, not one. render-bridges.sh screens prosody only
     (terminal f0 slope, falling endings — Doc's signature, RECIPE.md). This
     also scores IDENTITY: every take is embedded and compared against the
     frozen anchor, and takes that drift off Doc are rejected outright. Drift is
     the failure that matters here, because a cloner AVERAGES its corpus — one
     bad take does not get ignored, it gets blended in and blurs the identity.

  3. PROSODIC SPREAD BY PARAMETER SWEEP. Chatterbox has no delivery channel, so
     a corpus rendered at one setting is prosodically narrow by construction —
     and a narrow corpus risks teaching the cloner a FLAT Doc, which is the
     opposite of what expressive hosted rendering is being bought for. Cycling
     exaggeration across takes spreads the range without inventing a channel
     that does not exist.

THE SCORER IS CHATTERBOX'S OWN VOICE ENCODER (`model.ve`), deliberately. It
needs no install, so .venv-tts is never pip-touched (it is Doc's LIVE voice);
and it is the same notion of speaker identity the cloner itself conditions on,
so the gate measures the thing the renderer is actually trying to reproduce.

OUTPUT IS RAW, PRE-MASTERING, on purpose. The frozen chain in RECIPE.md is what
makes Doc sound like a machine, and it was dialled for Chatterbox's spectrum.
Baking it into a clone would teach the cloner the mastering as part of the
voice and then get applied a second time on top. Clone from raw; re-dial the
chain against the new engine's output afterwards, by ear.

CURATION BEATS VOLUME. Do not chase a duration target by lowering the gates.
Twenty clean minutes beat thirty with a fifth drifting. The report prints what
was rejected and why so that trade stays visible rather than silent.

Usage:
  build-voice-corpus.py --out corpus/ [--takes 24] [--texts lines.txt]
  build-voice-corpus.py --out corpus/ --min-similarity 0.82 --takes 40

Resumable: a line whose best take already exists is skipped, so a long
unattended run survives being interrupted.
"""

import argparse
import json
import pathlib
import sys
import time

# The default text set. Chosen for PHONETIC coverage and for the sentence
# SHAPES Doc actually produces — questions, short declaratives, numbers, dates,
# money, proper nouns, and the long qualified clause that is his house style.
# Shape drives prosody in an engine with no instruction channel, so this list
# is doing the job audio tags will do later.
DEFAULT_TEXTS = [
    "Morning. Three things need you today, and one of them is time sensitive.",
    "Confirming: two point four million dollars, close by September thirtieth.",
    "I have a concern. That clause would follow you through a practice sale.",
    "Renewal is likely; moderate confidence: one broker conversation, no signed letter of intent.",
    "The landlord has not responded since Tuesday.",
    "Do you want me to draft the counter, or wait for the survey?",
    "Rent is thirty-two dollars a square foot, triple net, with three percent annual escalations.",
    "That is the listing agent's number, not ours. I would verify it.",
    "Nothing on the board expires tonight.",
    "The lease commences the first of March, assuming delivery is on time.",
    "I can only speak to what I can verify, and that is not in the record yet.",
    "Six thousand four hundred square feet, second generation dental.",
    "Your option window closes in eleven days.",
    "He asked for a tenant improvement allowance of sixty dollars per foot.",
    "I would not sign that without an assignment clause carve-out.",
    "Okay. Pulling the comparison now.",
    "The practice is in Fort Walton Beach, off Racetrack Road.",
    "Two of the four spaces are gone. The other two are still available.",
    "That is a question for your attorney, not for me.",
    "I hold the records, the calendar, and the numbers.",
]

# Cycled across takes so the corpus spans intensities. 0.40 is the production
# setting from RECIPE.md and stays the centre of the sweep; the spread exists
# to give the cloner range, not to change Doc.
EXAGGERATION_SWEEP = (0.30, 0.40, 0.40, 0.50)
CFG_WEIGHT = 0.60


def terminal_slope_score(path, librosa, np):
    """Mean f0 slope over the final stretch of each voiced phrase.

    Negative = falling = Doc. Lifted from
    bin/screen-endings-reference-implementation.py so the corpus is screened by
    the same measure that screens every rendered phrase — one definition of
    Doc's signature, not two that can drift.

    RETURNS None WHEN NOTHING COULD BE MEASURED, which the reference
    implementation reported as 0.0. That conflation is a silent gate failure:
    "perfectly flat ending" and "no voiced run long enough to measure" are not
    the same fact, and with a `slope <= 0.0` gate the second one PASSES. Short
    lines rarely contain a 40-frame voiced run, so on exactly the phrases Doc
    says most the screen would be switched off without saying so. Caught by the
    2026-08-08 smoke test, where the kept take scored +0.00 on a 2.8s line.
    """
    y, sr = librosa.load(path, sr=24000)
    f0, voiced, _ = librosa.pyin(y, fmin=60, fmax=300, sr=sr)
    f0 = np.where(voiced, f0, np.nan)
    isv = ~np.isnan(f0)
    slopes, start = [], None
    for i, v in enumerate(np.append(isv, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > 40:
                tail = f0[max(start, i - 25):i]
                tail = tail[~np.isnan(tail)]
                if len(tail) > 8:
                    slopes.append(np.polyfit(np.arange(len(tail)), tail, 1)[0])
            start = None
    return float(np.mean(slopes)) if slopes else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="corpus output directory")
    ap.add_argument("--takes", type=int, default=24,
                    help="takes per line (default 24; 4 was the speed compromise)")
    ap.add_argument("--texts", help="file of lines, one per line (default: built-in set)")
    ap.add_argument("--anchor", help="identity anchor wav (default: reference/doc-identity-reference.wav)")
    ap.add_argument("--min-similarity", type=float, default=0.80,
                    help="reject takes below this cosine similarity to the anchor")
    ap.add_argument("--max-slope", type=float, default=0.0,
                    help="reject takes whose terminal f0 slope is above this (0 = must fall)")
    ap.add_argument("--rank", choices=("identity", "prosody"), default="identity",
                    help="among takes that clear BOTH gates, what wins. "
                         "identity (default) = highest similarity to the anchor; "
                         "prosody = most-falling ending.")
    args = ap.parse_args()

    import numpy as np
    import librosa
    import torch
    import torchaudio
    import perth
    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        perth.PerthImplicitWatermarker = perth.DummyWatermarker
    from chatterbox.tts import ChatterboxTTS
    from chatterbox.models.voice_encoder import VoiceEncoder
    from chatterbox.models.s3tokenizer import S3_SR

    tool = pathlib.Path(__file__).resolve().parent.parent
    anchor = pathlib.Path(args.anchor) if args.anchor else tool / "reference" / "doc-identity-reference.wav"
    if not anchor.exists():
        sys.exit(f"no identity anchor at {anchor}")
    out = pathlib.Path(args.out)
    (out / "takes").mkdir(parents=True, exist_ok=True)

    texts = ([t.strip() for t in pathlib.Path(args.texts).read_text().splitlines() if t.strip()]
             if args.texts else list(DEFAULT_TEXTS))

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"· device {device} · {len(texts)} lines x {args.takes} takes "
          f"= {len(texts) * args.takes} renders", flush=True)
    model = ChatterboxTTS.from_pretrained(device=device)

    # Embed the anchor ONCE. Every take is scored against this single frozen
    # file, never against another take — otherwise the corpus can drift as a
    # whole while every neighbouring pair still looks similar.
    anchor_wav, _ = librosa.load(str(anchor), sr=S3_SR)
    anchor_embed = model.ve.embeds_from_wavs([anchor_wav], sample_rate=S3_SR)

    report, kept_seconds = [], 0.0
    for idx, text in enumerate(texts):
        best_path = out / f"line-{idx:03d}.wav"
        if best_path.exists():
            kept_seconds += librosa.get_duration(path=str(best_path))
            print(f"[{idx:03d}] already built — skipping", flush=True)
            continue

        started, scored = time.monotonic(), []
        for take in range(args.takes):
            exag = EXAGGERATION_SWEEP[take % len(EXAGGERATION_SWEEP)]
            wav = model.generate(text, audio_prompt_path=str(anchor),
                                 exaggeration=exag, cfg_weight=CFG_WEIGHT)
            tmp = out / "takes" / f"line-{idx:03d}-take-{take:03d}.wav"
            torchaudio.save(str(tmp), wav, model.sr)

            y16, _ = librosa.load(str(tmp), sr=S3_SR)
            sim = float(VoiceEncoder.voice_similarity(
                model.ve.embeds_from_wavs([y16], sample_rate=S3_SR), anchor_embed))
            slope = terminal_slope_score(str(tmp), librosa, np)
            # Identity always gates. Prosody gates only when it was actually
            # measured — an unmeasurable take is not thereby a good one, so it
            # is marked and ranked BELOW every measured falling take rather
            # than being quietly treated as a perfect 0.0.
            passes = sim >= args.min_similarity and (
                slope is None or slope <= args.max_slope)
            scored.append({"take": take, "exaggeration": exag, "similarity": sim,
                           "slope": slope, "measured": slope is not None,
                           "passes": passes, "path": str(tmp)})

        # Rank ONLY among takes that clear both gates. A take that drifts off
        # Doc is not "the best available" — it is unusable, and shipping it
        # because nothing better appeared is how a corpus rots.
        eligible = [s for s in scored if s["passes"]]
        pool = eligible or []
        if pool:
            # WHAT WINS AMONG TAKES THAT CLEAR BOTH GATES, and why the default
            # is identity rather than prosody (changed 2026-08-08 after the
            # smoke test kept a 0.888 take over an available 0.922 one):
            #
            # This corpus is for CLONING, not for playback. The hosted engine
            # supplies delivery through audio tags, so a falling ending is a
            # RENDERING property it will control at generation time. What the
            # corpus uniquely determines is identity — and a cloner averages
            # its corpus, so similarity variance blurs the result in a way
            # prosody variance does not. Rising endings are still rejected by
            # the gate, so ranking on identity never buys upspeak; it just
            # stops trading identity purity for a signature the clone will
            # get from elsewhere.
            #
            # --rank prosody restores the old behaviour for anyone building a
            # corpus meant to be played rather than cloned.
            if args.rank == "prosody":
                best = min(pool, key=lambda s: (not s["measured"],
                                                s["slope"] if s["measured"] else 0.0,
                                                -s["similarity"]))
            else:
                best = max(pool, key=lambda s: (s["similarity"], s["measured"]))
            pathlib.Path(best["path"]).rename(best_path)
            dur = librosa.get_duration(path=str(best_path))
            kept_seconds += dur
            shown = f"{best['slope']:+.2f}" if best["measured"] else "unmeasured"
            status = f"kept take {best['take']:03d} sim={best['similarity']:.3f} slope={shown} {dur:.1f}s"
        else:
            best, status = None, "NO TAKE PASSED — line excluded from corpus"

        for s in scored:                       # discard the losers
            p = pathlib.Path(s["path"])
            if p.exists():
                p.unlink()
        sims = [s["similarity"] for s in scored]
        report.append({
            "index": idx, "text": text, "takes": args.takes,
            "passed": len(eligible), "kept": best["take"] if best else None,
            "similarity_best": max(sims), "similarity_worst": min(sims),
            "similarity_mean": sum(sims) / len(sims),
            "slope_kept": best["slope"] if best else None,
            "slope_measured": best["measured"] if best else False,
            "takes_measured": sum(1 for s in scored if s["measured"]),
            "seconds": librosa.get_duration(path=str(best_path)) if best else 0.0,
        })
        print(f"[{idx:03d}] {len(eligible)}/{args.takes} passed · {status} "
              f"· {time.monotonic() - started:.0f}s", flush=True)

    excluded = [r for r in report if r["kept"] is None]
    unmeasured = [r for r in report if r["kept"] is not None and not r["slope_measured"]]
    summary = {
        "lines": len(report),
        "lines_excluded": len(excluded),
        "lines_kept_without_prosody_measurement": len(unmeasured),
        "total_seconds": round(kept_seconds, 1),
        "total_minutes": round(kept_seconds / 60, 1),
        "takes_per_line": args.takes,
        "min_similarity": args.min_similarity,
        "max_slope": args.max_slope,
        "anchor": str(anchor),
        "lines_detail": report,
    }
    (out / "corpus-report.json").write_text(json.dumps(summary, indent=2))

    print(f"\n=== corpus: {summary['total_minutes']} min "
          f"({summary['total_seconds']}s) across "
          f"{len(report) - len(excluded)} line(s) ===")
    if unmeasured:
        # Visible, never silent: these lines were chosen on identity alone
        # because no take had a voiced run long enough to score the ending.
        print(f"{len(unmeasured)} line(s) kept WITHOUT a prosody measurement "
              f"(too short to score the ending — picked on identity alone):")
        for r in unmeasured:
            print(f"  [{r['index']:03d}] «{r['text'][:60]}»")
    if excluded:
        # Never silent: a line no take could carry is a fact about the gates or
        # the text, and it must be visible rather than quietly missing.
        print(f"EXCLUDED {len(excluded)} line(s) — no take cleared both gates:")
        for r in excluded:
            print(f"  [{r['index']:03d}] best similarity {r['similarity_best']:.3f} "
                  f"«{r['text'][:60]}»")
    if kept_seconds < 25 * 60:
        print(f"\nNOTE: {summary['total_minutes']} min. Professional Voice Cloning "
              f"wants ~30. ADD LINES rather than lowering --min-similarity: a "
              f"drifting take does not pad the corpus, it blurs the clone.")
    print(f"report: {out / 'corpus-report.json'}")


if __name__ == "__main__":
    main()
