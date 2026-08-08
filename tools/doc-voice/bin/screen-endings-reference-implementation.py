import torch, torchaudio, perth, numpy as np
if getattr(perth, "PerthImplicitWatermarker", None) is None:
    perth.PerthImplicitWatermarker = perth.DummyWatermarker
from chatterbox.tts import ChatterboxTTS
import librosa
B="/private/tmp/claude-501/-Users-booko-My-Drive-CARR-AI/81a69706-4b5e-47c1-b80b-2d2cac7673f4/scratchpad/bakeoff"
device = "mps" if torch.backends.mps.is_available() else "cpu"
model = ChatterboxTTS.from_pretrained(device=device)
TXT = "I'm Doc. I work with the CARR team on practice realestate. I hold the records, the calendar, and the numbers, and I only speak to what I can verify."
REF = f"{B}/samples-r2/doc-take2.wav"

def terminal_slope_score(path: str) -> float:
    """Mean f0 slope over the final stretch of each voiced phrase; negative = falling."""
    y, sr = librosa.load(path, sr=24000)
    f0, voiced, _ = librosa.pyin(y, fmin=60, fmax=300, sr=sr)
    f0 = np.where(voiced, f0, np.nan)
    # find voiced segments, take last 25 frames of each substantial segment
    isv = ~np.isnan(f0)
    slopes = []
    start = None
    for i, v in enumerate(np.append(isv, False)):
        if v and start is None: start = i
        elif not v and start is not None:
            if i - start > 40:  # substantial phrase
                tail = f0[max(start, i-25):i]
                tail = tail[~np.isnan(tail)]
                if len(tail) > 8:
                    x = np.arange(len(tail))
                    slopes.append(np.polyfit(x, tail, 1)[0])
            start = None
    return float(np.mean(slopes)) if slopes else 0.0

results = []
for i in range(1, 7):
    wav = model.generate(TXT, audio_prompt_path=REF, exaggeration=0.40, cfg_weight=0.60)
    p = f"{B}/samples-r2/doc-roll{i}.wav"
    torchaudio.save(p, wav, model.sr)
    s = terminal_slope_score(p)
    results.append((i, s))
    print(f"roll {i}: terminal slope {s:+.3f} Hz/frame", flush=True)
results.sort(key=lambda t: t[1])
print("RANKING (most falling first):", [f"roll{i} ({s:+.2f})" for i, s in results])
print("BEST:", results[0][0], "SECOND:", results[1][0])
