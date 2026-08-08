#!/usr/bin/env python
"""render_phrase.py — render one phrase in Doc's frozen voice. Runs INSIDE
.venv-tts (torch + chatterbox); never import this from plain python3.

    render_phrase.py "text" out.wav [--takes N]

Identity comes ONLY from the reference wav (doc-voice/RECIPE.md iteration
rules: character briefs are dead — re-describing Doc re-rolls the actor).
Settings are the frozen ones: exaggeration 0.40, cfg_weight 0.60.

--takes N rolls N takes and keeps the one with the most-falling terminal f0
(RECIPE delivery screening: rising finals are rejected; falling, settled
endings are Doc's signature). Live conversation renders use a single take for
speed; the bridge kit uses --takes 4.

Platform note (bake-off lesson, 2026-08-07): resemble-perth exposes
PerthImplicitWatermarker=None on this Mac; the dummy substitution below keeps
render alive. Internal Joe+Dell surface only — revisit the watermark before
anything ever ships wider.
"""

import sys

import numpy as np


def terminal_f0_slope(wav, sr) -> float:
    """Mean f0 slope over the last ~25 voiced frames; negative = falling."""
    import librosa

    f0 = librosa.pyin(
        wav.astype(np.float32), sr=sr,
        fmin=librosa.note_to_hz("C2"), fmax=librosa.note_to_hz("C5"),
    )[0]
    voiced = f0[~np.isnan(f0)]
    if len(voiced) < 8:
        return 0.0
    tail = voiced[-25:]
    return float(np.polyfit(np.arange(len(tail)), tail, 1)[0])


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    takes = 1
    if "--takes" in sys.argv:
        takes = int(sys.argv[sys.argv.index("--takes") + 1])
    text, out_path = args[0], args[1]

    import perth
    if getattr(perth, "PerthImplicitWatermarker", None) is None:
        perth.PerthImplicitWatermarker = perth.DummyWatermarker  # bake-off fix

    import torch
    import torchaudio
    from chatterbox.tts import ChatterboxTTS

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = ChatterboxTTS.from_pretrained(device=device)
    ref = str(
        __import__("pathlib").Path(__file__).resolve()
        .parent.parent.parent / "doc-voice" / "reference" / "doc-identity-reference.wav"
    )

    best, best_slope = None, float("inf")
    for _ in range(takes):
        audio = model.generate(
            text, audio_prompt_path=ref, exaggeration=0.40, cfg_weight=0.60,
        )
        if takes == 1:
            best = audio
            break
        slope = terminal_f0_slope(audio.squeeze().cpu().numpy(), model.sr)
        if slope < best_slope:
            best, best_slope = audio, slope

    torchaudio.save(out_path, best.cpu(), model.sr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
