#!/usr/bin/env python3
"""Turn a video file into the ball's SD-card clip format.

    python3 tools/make_video.py media/<your video> --start 43 [--duration 10] [--fps 15]

Writes media/sd/m8/rickroll.m8v (video) and media/sd/m8/rickroll.wav (audio), plus
media/sd/preview.png (a contact sheet of the frames, to check the cut). Copy the m8 folder
to the root of the FAT32 microSD card.

The .m8v format (little-endian), read by the firmware's video player:
    0   "M8V1"
    4   uint16 width, uint16 height (even, multiples of 16)
    8   uint16 fps, uint16 frame count N
    12  uint32 offset[N + 1]        file offsets of each JPEG; offset[N] = end of the file
    ..  N baseline JPEGs (4:2:0), each width x height, each starting on a 512-byte boundary
        and zero-padded to the next one

Frames are cropped (black bars removed) and scaled to the largest size whose corners stay
inside the round 466 px screen.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SCREEN_R = 230          # keep the frame's corners this far from the centre (screen radius 233)
AUDIO_RATE = 22050      # matches the firmware's speaker


def run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{r.stderr[-2000:]}")
    return r.stdout + r.stderr


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)])
    return json.loads(out[out.index("{"):])


def detect_crop(path: Path, start: float, duration: float) -> str | None:
    """ffmpeg cropdetect over the clip; returns 'w:h:x:y' or None."""
    out = run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(duration), "-i", str(path),
               "-vf", "cropdetect=limit=24:round=2:reset=0", "-an", "-f", "null", "-"])
    crops = [line.split("crop=")[-1].strip() for line in out.splitlines() if "crop=" in line]
    return crops[-1] if crops else None


def fit_size(aspect: float) -> tuple[int, int]:
    """Largest multiple-of-16 frame with this aspect whose half-diagonal fits SCREEN_R."""
    best = (16, 16)
    for w in range(16, 466, 16):
        h = int(round(w / aspect / 16)) * 16
        if h < 16:
            continue
        if math.hypot(w / 2, h / 2) <= SCREEN_R and w * h > best[0] * best[1]:
            best = (w, h)
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--start", type=float, required=True, help="seconds into the file")
    ap.add_argument("--duration", type=float, default=10.0)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--quality", type=int, default=5, help="ffmpeg -q:v (2 best .. 31 worst)")
    ap.add_argument("--name", default="rickroll")
    args = ap.parse_args()

    info = probe(args.video)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
    crop = detect_crop(args.video, args.start, args.duration)
    if crop:
        cw, ch = (int(x) for x in crop.split(":")[:2])
    else:
        cw, ch = int(v["width"]), int(v["height"])
    w, h = fit_size(cw / ch)
    print(f"source {v['width']}x{v['height']}, crop {crop or 'none'} -> frames {w}x{h} @ {args.fps} fps")

    out_dir = ROOT / "media" / "sd" / "m8"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        vf = (f"{'crop=' + crop + ',' if crop else ''}scale={w}:{h}:flags=lanczos,"
              f"fps={args.fps},format=yuvj420p")
        run(["ffmpeg", "-hide_banner", "-y", "-ss", str(args.start), "-t", str(args.duration), "-i", str(args.video),
             "-vf", vf, "-q:v", str(args.quality), "-pix_fmt", "yuvj420p", str(tmp / "f%04d.jpg")])
        frames = sorted(tmp.glob("f*.jpg"))
        if not frames:
            sys.exit("no frames extracted: check --start against the video length")
        blobs = [f.read_bytes() for f in frames]
        # Every frame starts on a 512-byte sector boundary and is zero-padded to one, so the
        # firmware reads each frame with a single direct SD transfer (decoders stop at EOI).
        header = b"M8V1" + struct.pack("<HHHH", w, h, args.fps, len(blobs))
        table_size = 4 * (len(blobs) + 1)
        align = lambda n: (n + 511) // 512 * 512
        offsets, pos = [], align(len(header) + table_size)
        for b in blobs:
            offsets.append(pos)
            pos += align(len(b))
        offsets.append(pos)
        m8v = out_dir / f"{args.name}.m8v"
        with open(m8v, "wb") as f:
            f.write(header)
            f.write(struct.pack(f"<{len(offsets)}I", *offsets))
            f.write(b"\0" * (offsets[0] - len(header) - table_size))
            for b in blobs:
                f.write(b)
                f.write(b"\0" * (align(len(b)) - len(b)))

        # Contact sheet: every 10th frame.
        picks = [Image.open(p) for p in frames[::10]]
        cols = 5
        rows = math.ceil(len(picks) / cols)
        sheet = Image.new("RGB", (cols * w, rows * h))
        for i, im in enumerate(picks):
            sheet.paste(im, ((i % cols) * w, (i // cols) * h))
        sheet.save(ROOT / "media" / "sd" / "preview.png")

    sizes = [len(b) for b in blobs]
    print(f"{m8v.relative_to(ROOT)}: {len(blobs)} frames, {pos / 1024:.0f} KB "
          f"(frame {min(sizes) / 1024:.1f}-{max(sizes) / 1024:.1f} KB, avg {sum(sizes) / len(sizes) / 1024:.1f} KB)")

    wav = out_dir / f"{args.name}.wav"
    if has_audio:
        fade_out = max(0.0, args.duration - 0.4)
        run(["ffmpeg", "-hide_banner", "-y", "-ss", str(args.start), "-t", str(args.duration), "-i", str(args.video),
             "-vn", "-ac", "1", "-ar", str(AUDIO_RATE), "-sample_fmt", "s16",
             "-af", f"afade=t=in:d=0.15,afade=t=out:st={fade_out}:d=0.4,loudnorm=I=-16:TP=-1.5:LRA=11",
             str(wav)])
        print(f"{wav.relative_to(ROOT)}: {wav.stat().st_size / 1024:.0f} KB")
    else:
        print("no audio stream in the source: the clip will play silently")
    print("Copy media/sd/m8 to the root of the FAT32 card.")


if __name__ == "__main__":
    main()
