#!/usr/bin/env python3
"""Magic 8 Ball sound effects, synthesized from scratch (no samples).

Writes 22.05 kHz 16-bit mono WAVs to ../sounds. The firmware embeds them and plays them
through the board's ES8311 + NS4150B and a small speaker, so everything lives between
~250 Hz and ~8 kHz: a tiny speaker can't reproduce real bass.

    python3 tools/make_sounds.py

Timing: the reveal sounds put their landing chime at LAND seconds, which lines up with
the flash of light in magic8.h (960 ms after the rise starts, minus audio latency).
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sounds"
SR = 22050
LAND = 0.88
rng = np.random.default_rng(88)


# ---------------------------------------------------------------------------- helpers
def secs(d: float) -> np.ndarray:
    return np.arange(int(round(d * SR))) / SR


def silence(d: float) -> np.ndarray:
    return np.zeros(int(round(d * SR)))


def place(dst: np.ndarray, src: np.ndarray, at: float, gain: float = 1.0) -> None:
    i = int(round(at * SR))
    if i >= len(dst):
        return
    n = min(len(src), len(dst) - i)
    dst[i:i + n] += src[:n] * gain


def fade(x: np.ndarray, fade_in: float = 0.004, fade_out: float = 0.02) -> np.ndarray:
    x = x.copy()
    a, b = int(fade_in * SR), int(fade_out * SR)
    if a:
        x[:a] *= np.linspace(0, 1, a)
    if b:
        x[-b:] *= np.linspace(1, 0, b)
    return x


def normalize(x: np.ndarray, peak_db: float) -> np.ndarray:
    m = np.max(np.abs(x)) or 1.0
    return x / m * (10 ** (peak_db / 20))


def tone(freqs, dur: float, shape: str = "sine") -> np.ndarray:
    """Oscillator following a frequency curve (array or constant)."""
    n = int(round(dur * SR))
    f = np.broadcast_to(np.asarray(freqs, float), (n,)) if np.ndim(freqs) == 0 else np.asarray(freqs, float)
    ph = 2 * np.pi * np.cumsum(f) / SR
    if shape == "sine":
        return np.sin(ph)
    if shape == "tri":
        return 2 / np.pi * np.arcsin(np.sin(ph))
    if shape == "soft-square":  # odd harmonics, rolled off: bright but not harsh
        return sum(np.sin(k * ph) / k ** 1.4 for k in (1, 3, 5, 7))
    raise ValueError(shape)


def glide(f0: float, f1: float, dur: float) -> np.ndarray:
    return f0 * (f1 / f0) ** (secs(dur) / dur)


def bell(f: float, dur: float = 1.2, decay: float = 3.2, bright: float = 1.0) -> np.ndarray:
    """Struck glass/bell: inharmonic partials, higher ones dying faster."""
    t = secs(dur)
    out = np.zeros_like(t)
    for k, (ratio, amp) in enumerate(((1, 1.0), (2.0, 0.35), (2.76, 0.45 * bright), (5.40, 0.22 * bright),
                                      (8.93, 0.10 * bright))):
        fk = f * ratio
        if fk > SR * 0.45:
            continue
        out += amp * np.sin(2 * np.pi * fk * t + rng.uniform(0, 6.28)) * np.exp(-t * decay * (1 + 0.7 * k))
    attack = 1 - np.exp(-t / 0.0015)
    return out * attack


def noise(dur: float) -> np.ndarray:
    return rng.standard_normal(int(round(dur * SR)))


def biquad_bp(x: np.ndarray, fc, q: float) -> np.ndarray:
    """Band-pass with a (possibly moving) centre frequency."""
    fc = np.broadcast_to(np.asarray(fc, float), x.shape)
    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = 0.0
    for i in range(len(x)):
        w = 2 * np.pi * fc[i] / SR
        alpha = np.sin(w) / (2 * q)
        b0, b2 = alpha, -alpha
        a0, a1, a2 = 1 + alpha, -2 * np.cos(w), 1 - alpha
        yi = (b0 * x[i] + b2 * x2 - a1 * y1 - a2 * y2) / a0
        x2, x1 = x1, x[i]
        y2, y1 = y1, yi
        y[i] = yi
    return y


def lowpass(x: np.ndarray, fc: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * fc / SR)
    y = np.zeros_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def reverb(x: np.ndarray, size: float = 0.5, mix: float = 0.25, tail: float = 0.6) -> np.ndarray:
    """Small Schroeder reverb: the 'inside a liquid-filled ball' space."""
    x = np.concatenate([x, silence(tail)])
    wet = np.zeros_like(x)
    for d_ms, g in ((29.7, 0.77), (37.1, 0.75), (41.1, 0.73), (43.7, 0.71)):
        d = int(d_ms * size * 2 * SR / 1000)
        y = x.copy()
        for i in range(d, len(y)):
            y[i] += g * y[i - d]
        wet += y
    for d_ms, g in ((5.0, 0.7), (1.7, 0.7)):
        d = int(d_ms * SR / 1000)
        y = np.zeros_like(wet)
        for i in range(len(wet)):
            xd = wet[i - d] if i >= d else 0.0
            yd = y[i - d] if i >= d else 0.0
            y[i] = -g * wet[i] + xd + g * yd
        wet = y
    wet = lowpass(wet, 5000)
    wet /= np.max(np.abs(wet)) or 1
    return (1 - mix) * x / (np.max(np.abs(x)) or 1) + mix * wet


def bubble(f0: float, dur: float) -> np.ndarray:
    """A single bubble: a sine whose pitch rises as it reaches the surface."""
    t = secs(dur)
    f = f0 * (1 + 1.8 * t / dur)
    env = np.exp(-t / (dur * 0.35)) * (1 - np.exp(-t / 0.002))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env


def sparkle(dur: float, count: int, f_lo: float = 3000, f_hi: float = 6500, start: float = 0.0) -> np.ndarray:
    out = silence(start + dur + 0.2)
    for _ in range(count):
        at = start + dur * rng.random() ** 1.3
        f = rng.uniform(f_lo, f_hi)
        ping = np.sin(2 * np.pi * f * secs(0.12)) * np.exp(-secs(0.12) / 0.025)
        place(out, ping, at, rng.uniform(0.25, 1.0) * (1 - (at - start) / (dur + 0.3)))
    return out


def rise(dur: float = LAND) -> np.ndarray:
    """The die surfacing: a soft swell of filtered noise with a gentle upward glide."""
    t = secs(dur)
    swell = (t / dur) ** 2.2
    n = biquad_bp(noise(dur), 900 + 2600 * (t / dur) ** 1.5, 2.2) * swell * 0.5
    g = tone(glide(330, 660, dur), dur, "sine") * swell * 0.18
    return fade(n + g, 0.05, 0.03)


def bitcrush(x: np.ndarray, bits: int = 3, hold: int = 6) -> np.ndarray:
    y = x[::hold].repeat(hold)[:len(x)]
    q = 2 ** (bits - 1)
    return np.round(y * q) / q


# ---------------------------------------------------------------------------- sounds
def s_turnover():
    d = 0.55
    t = secs(d)
    w = biquad_bp(noise(d), 500 + 2200 * np.sin(np.pi * t / d) ** 1.5, 1.6) * np.sin(np.pi * t / d) ** 2
    out = silence(0.75)
    place(out, w, 0, 0.9)
    thump = (tone(glide(240, 150, 0.16), 0.16, "tri") * np.exp(-secs(0.16) / 0.05))
    place(out, thump, 0.42, 0.6)
    return normalize(fade(reverb(out, 0.4, 0.2, 0.25)), -5)


def s_bubbles():
    d = 1.5
    t = secs(d)
    bed = lowpass(noise(d), 700) * (0.55 + 0.45 * np.sin(2 * np.pi * 5.5 * t)) * 0.9
    out = bed * np.minimum(1, t / 0.15) * np.minimum(1, (d - t) / 0.3)
    for _ in range(26):
        at = d * 0.9 * rng.random() ** 1.2
        place(out, bubble(rng.uniform(380, 1150), rng.uniform(0.025, 0.07)), at, rng.uniform(0.35, 1.0))
    return normalize(fade(reverb(out, 0.5, 0.22, 0.3)), -6)


def reveal(landing: np.ndarray, length: float, rise_gain: float = 1.0, room: float = 0.3) -> np.ndarray:
    out = silence(length)
    place(out, rise(), 0, rise_gain)
    place(out, landing, LAND, 1.0)
    return normalize(fade(reverb(out, 0.6, room, 0.6), 0.01, 0.15), -1)


def s_reveal_common():
    land = bell(1046.5, 1.2, 3.0) + 0.5 * bell(523.25, 1.2, 3.4, 0.6)
    return reveal(land, 2.0)


def s_reveal_rare():
    land = silence(1.3)
    place(land, bell(1318.5, 1.1, 3.2), 0)
    place(land, bell(1975.5, 1.1, 3.4), 0.075)
    place(land, sparkle(0.8, 14, start=0.05), 0, 0.35)
    return reveal(land, 2.2, room=0.35)


def s_reveal_legendary():
    land = silence(1.8)
    notes = (1046.5, 1318.5, 1568.0, 2093.0)
    for i, f in enumerate(notes):
        d = 1.4 if i == len(notes) - 1 else 0.5
        b = bell(f, d, 2.4 if i == len(notes) - 1 else 4.0, 1.2)
        b += 0.25 * tone(f, d, "soft-square") * np.exp(-secs(d) / (0.35 if i == 3 else 0.08))
        place(land, b, i * 0.075, 0.9)
    place(land, sparkle(1.3, 34, 3500, 7500, start=0.2), 0, 0.4)
    return reveal(land, 2.9, rise_gain=1.2, room=0.4)


def s_reveal_glitch():
    land = silence(0.9)
    chime = bell(1046.5, 0.5, 4.0)
    for i, (shift, at) in enumerate(((1.0, 0.0), (1.5, 0.07), (0.75, 0.13), (2.0, 0.19))):
        seg = chime[: int(0.06 * SR)]
        idx = (np.arange(int(len(seg) / shift)) * shift).astype(int)
        place(land, bitcrush(seg[idx], 3, 5 + i), at, 0.9)
    zap = tone(glide(2200, 180, 0.16), 0.16, "soft-square") * np.linspace(1, 0, int(0.16 * SR))
    place(land, bitcrush(zap, 2, 4), 0.27, 0.55)
    place(land, bitcrush(bell(1046.5, 0.45, 5.0), 4, 3), 0.45, 0.6)
    return reveal(land, 1.9, room=0.15)


def s_reveal_secret():
    land = silence(1.0)
    vib = 1 + 0.03 * np.sin(2 * np.pi * 7 * secs(0.3))
    a = tone(glide(880, 620, 0.3) * vib, 0.3, "tri") * np.sin(np.pi * secs(0.3) / 0.3) ** 0.7
    vib2 = 1 + 0.035 * np.sin(2 * np.pi * 6 * secs(0.42))
    b = tone(glide(740, 520, 0.42) * vib2, 0.42, "tri") * np.sin(np.pi * secs(0.42) / 0.42) ** 0.7
    place(land, a, 0.0)
    place(land, b, 0.32)
    place(land, bell(1568, 0.6, 4.0), 0.0, 0.4)
    return reveal(land, 2.0, room=0.25)


def s_sink():
    d = 0.6
    t = secs(d)
    w = biquad_bp(noise(d), 2200 * (1 - t / d) + 350, 1.8) * np.sin(np.pi * t / d) ** 2
    out = silence(0.8)
    place(out, w, 0, 0.8)
    place(out, bubble(500, 0.06), 0.25, 0.5)
    place(out, bubble(380, 0.07), 0.38, 0.4)
    return normalize(fade(reverb(out, 0.5, 0.25, 0.3)), -8)


def s_wake():
    out = silence(1.0)
    place(out, bell(783.99, 0.9, 3.5, 0.5), 0.0, 0.7)
    place(out, bell(1174.66, 0.9, 3.0, 0.5), 0.12, 0.8)
    return normalize(fade(reverb(out, 0.5, 0.3, 0.3), 0.005, 0.2), -7)


def s_charge():
    out = silence(0.5)
    a = tone(glide(700, 880, 0.08), 0.08, "tri") * np.exp(-secs(0.08) / 0.05)
    b = tone(glide(1100, 1320, 0.16), 0.16, "tri") * np.exp(-secs(0.16) / 0.08)
    place(out, a, 0.0)
    place(out, b, 0.09)
    place(out, bell(2637, 0.3, 6.0), 0.1, 0.25)
    return normalize(fade(reverb(out, 0.3, 0.15, 0.15)), -7)


def s_low_battery():
    out = silence(0.7)
    place(out, tone(783.99, 0.16, "tri") * np.exp(-secs(0.16) / 0.1), 0.0)
    place(out, tone(523.25, 0.28, "tri") * np.exp(-secs(0.28) / 0.14), 0.18)
    return normalize(fade(reverb(out, 0.3, 0.15, 0.2)), -8)


def s_sound_on():
    out = silence(0.4)
    place(out, bell(1567.98, 0.35, 6.0, 0.6), 0.0)
    return normalize(fade(out), -8)


def snare(d: float = 0.18) -> np.ndarray:
    t = secs(d)
    body = biquad_bp(noise(d), 2200, 0.8) * np.exp(-t / 0.05)
    tone_ = tone(glide(330, 220, d), d, "tri") * np.exp(-t / 0.035)
    return body * 0.9 + tone_ * 0.6


def tom(f0: float, d: float = 0.3) -> np.ndarray:
    t = secs(d)
    return tone(glide(f0, f0 * 0.68, d), d, "tri") * np.exp(-t / 0.09) + \
        biquad_bp(noise(d), 1200, 1.0) * np.exp(-t / 0.012) * 0.4


def cymbal(d: float = 0.9) -> np.ndarray:
    t = secs(d)
    hiss = biquad_bp(noise(d), 7000, 0.6)
    metal = sum(np.sign(np.sin(2 * np.pi * f * t)) for f in (3150, 4120, 5280, 6690)) / 4
    ring = metal * np.sin(2 * np.pi * 1520 * t)
    env = np.exp(-t / 0.22) * (1 - np.exp(-t / 0.002))
    return (hiss * 0.8 + ring * 0.35) * env


def s_rimshot():
    """Roast mode: the die surfaces, then ba-dum-tss."""
    land = silence(1.3)
    place(land, snare(), 0.0, 0.9)
    place(land, tom(260), 0.17, 1.0)
    place(land, cymbal(), 0.36, 0.8)
    return reveal(land, 2.3, room=0.15)


def s_mode_switch():
    """Mode change: a bright major chord with a sparkle, landing with the flash."""
    land = silence(1.4)
    for f, at in ((1046.5, 0.0), (1318.5, 0.02), (1568.0, 0.04), (2093.0, 0.06)):
        place(land, bell(f, 1.2, 2.8, 0.8), at, 0.6)
    place(land, sparkle(0.9, 18, 3200, 7000, start=0.05), 0, 0.3)
    return reveal(land, 2.3, room=0.35)


def brass(f: float, dur: float, vibrato: float = 0.0, bend: float = 0.0) -> np.ndarray:
    """A muted-trombone note: harmonic stack whose brightness opens and closes ("wah")."""
    t = secs(dur)
    freq = f * (1 - 0.018 * np.exp(-t / 0.04))                    # scoop up into the note
    freq = freq * (1 + vibrato * np.sin(2 * np.pi * 5.2 * t) * np.clip(t / 0.25, 0, 1))
    freq = freq * (1 - bend * np.clip((t - dur * 0.55) / (dur * 0.45), 0, 1) ** 2)
    ph = 2 * np.pi * np.cumsum(freq) / SR
    bright = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 0.6           # the mute opening and closing
    out = np.zeros_like(t)
    for k in range(1, 14):
        if f * k > SR * 0.45:
            break
        formant = np.exp(-((f * k - 900) / 700) ** 2) + 0.35       # a brassy bump around 900 Hz
        out += np.sin(k * ph) / k ** 0.9 * formant * bright ** (0.4 + 0.25 * k)
    env = np.clip(t / 0.03, 0, 1) * np.clip((dur - t) / 0.06, 0, 1)
    return out * env


def s_skill_issue():
    """SKILL ISSUE: the die surfaces, then a sad trombone (wah, wah, wah, waaah)."""
    land = silence(2.0)
    for i, (f, d) in enumerate(((392.0, 0.32), (369.99, 0.32), (349.23, 0.32))):
        place(land, brass(f, d), i * 0.34, 0.8)
    place(land, brass(329.63, 1.0, vibrato=0.025, bend=0.04), 1.02, 0.9)
    return reveal(land, 3.2, rise_gain=0.8, room=0.18)


# Order = the Snd enum in magic8.h and the files list in magic-8-ball.yaml.
SOUNDS = [
    ("turnover", s_turnover),
    ("bubbles", s_bubbles),
    ("reveal_common", s_reveal_common),
    ("reveal_rare", s_reveal_rare),
    ("reveal_legendary", s_reveal_legendary),
    ("reveal_glitch", s_reveal_glitch),
    ("reveal_secret", s_reveal_secret),
    ("sink", s_sink),
    ("wake", s_wake),
    ("charge", s_charge),
    ("low_battery", s_low_battery),
    ("sound_on", s_sound_on),
    ("rimshot", s_rimshot),
    ("mode_switch", s_mode_switch),
    ("skill_issue", s_skill_issue),
]


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = np.clip(x, -1, 1)
    pcm = (pcm * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main():
    OUT.mkdir(exist_ok=True)
    total = 0
    for name, fn in SOUNDS:
        x = fn()
        write_wav(OUT / f"{name}.wav", x)
        total += len(x)
        print(f"{name:18s} {len(x) / SR:5.2f} s")
    print(f"total {total / SR:.1f} s, {total * 2 / 1024:.0f} KB")


if __name__ == "__main__":
    main()
