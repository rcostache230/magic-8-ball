#!/usr/bin/env python3
"""Manage the ball's microSD card over the USB cable (the card stays in the board).

Run with ESPHome's Python (it has pyserial):

    ESPPY=$(head -1 "$(which esphome)" | sed 's/^#!//')
    $ESPPY tools/sdcard.py info                 # card state, size, free space, free RAM
    $ESPPY tools/sdcard.py format --yes         # FAT32 (erases the card; needed for exFAT cards)
    $ESPPY tools/sdcard.py ls [dir]
    $ESPPY tools/sdcard.py put <local file> <path on card>
    $ESPPY tools/sdcard.py sync                 # copy everything under media/sd/ to the card
    $ESPPY tools/sdcard.py del <path on card>
    $ESPPY tools/sdcard.py raw RIG 4 && $ESPPY tools/sdcard.py raw ASK   # test: play the rickroll

Talks to the m8sd component (components/m8sd). Opening the port leaves the reset lines alone,
so the board keeps running; each command first checks the card is mounted.
"""
from __future__ import annotations

import argparse
import glob
import queue
import re
import sys
import threading
import time
import zlib
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;]*m")
# The board's USB driver silently drops anything beyond its 512-byte receive buffer while it is
# busy writing to the card, so keep less than that in flight.
WINDOW = 448
BLOCK = 64


class Board:
    def __init__(self, port: str | None):
        port = port or next(iter(sorted(glob.glob("/dev/cu.usbmodem*"))), None)
        if not port:
            sys.exit("Board not found on USB (no /dev/cu.usbmodem*)")
        # Leave DTR/RTS as macOS sets them on open (both asserted). Lowering DTR first leaves
        # "RTS on, DTR off" for a moment, which is the ESP32-S3's reset signal.
        self.s = serial.Serial(port, 115200, timeout=0.05)
        self.lines: queue.Queue[str] = queue.Queue()
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        buf = b""
        while self.alive:
            try:
                buf += self.s.read(4096)
            except serial.SerialException:
                break
            *done, buf = buf.split(b"\n")
            for raw in done:
                line = ANSI.sub("", raw.decode(errors="replace")).strip()
                if "#M8> " in line:
                    self.lines.put(line.split("#M8> ", 1)[1])

    def send(self, cmd: str):
        self.s.write(f"\n#M8 {cmd}\n".encode())
        self.s.flush()

    def expect(self, timeout: float, *prefixes: str) -> str:
        """Next protocol reply starting with one of prefixes (ERR always ends the wait)."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                reply = self.lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if reply.startswith("ERR"):
                sys.exit(f"board: {reply}")
            if any(reply.startswith(p) for p in prefixes):
                return reply
        sys.exit(f"no reply from the board (waited {timeout:.0f} s for {'/'.join(prefixes)})")

    def wait_ready(self, timeout: float = 20.0) -> str:
        """Opening the port reboots the board on macOS: wait until the card has settled."""
        end = time.time() + timeout
        while time.time() < end:
            self.drain()
            self.send("INFO")
            t = time.time() + 1.5
            while time.time() < t:
                try:
                    r = self.lines.get(timeout=0.1)
                except queue.Empty:
                    continue
                if r.startswith("INFO") and "state=mounting" not in r:
                    return r
        sys.exit("the board didn't report a ready card (is the firmware with m8sd flashed?)")

    def drain(self):
        while not self.lines.empty():
            self.lines.get_nowait()

    def close(self):
        self.alive = False
        self.s.close()


def put(b: Board, local: Path, remote: str):
    data = local.read_bytes()
    crc = zlib.crc32(data) & 0xFFFFFFFF
    b.drain()
    b.send(f"PUT {remote} {len(data)} {crc:08x}")
    b.expect(10, "READY")
    sent, acked, t0, last_pct = 0, 0, time.time(), -1
    while sent < len(data):
        while sent - acked + BLOCK > WINDOW:
            r = b.expect(10, "ACK")
            acked = max(acked, int(r.split()[1]))
        n = min(BLOCK, len(data) - sent)
        b.s.write(data[sent:sent + n])
        sent += n
        while not b.lines.empty():   # pick up ACKs without blocking
            r = b.lines.get_nowait()
            if r.startswith("ERR"):
                sys.exit(f"board: {r}")
            if r.startswith("ACK"):
                acked = max(acked, int(r.split()[1]))
        pct = 100 * sent // len(data)
        if pct != last_pct and pct % 10 == 0:
            last_pct = pct
            print(f"\r  {remote}: {pct:3d}%  ({sent // 1024} / {len(data) // 1024} KB)", end="", flush=True)
    r = b.expect(60, "OK")
    print(f"\r  {remote}: done, {len(data) // 1024} KB in {time.time() - t0:.1f} s, CRC verified on the board")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["info", "format", "ls", "put", "sync", "del", "raw"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--yes", action="store_true", help="confirm format (erases the card)")
    ap.add_argument("--port")
    a = ap.parse_args()

    b = Board(a.port)
    try:
        info = b.wait_ready()
        if a.cmd == "info":
            print(info)
        elif a.cmd == "format":
            if not a.yes:
                sys.exit("Formatting erases the whole card. Re-run with --yes to confirm.")
            b.send("FORMAT ERASE-ALL")
            print("Formatting as FAT32 (can take a minute on a big card)...")
            print(b.expect(240, "OK"))
            b.send("INFO")
            print(b.expect(5, "INFO"))
        elif a.cmd == "ls":
            b.send(f"LS {a.args[0] if a.args else ''}".strip())
            while True:
                r = b.expect(10, "FILE", "OK")
                if r.startswith("OK"):
                    break
                print(" ", r[5:])
        elif a.cmd == "put":
            if len(a.args) != 2:
                sys.exit("usage: put <local file> <path on card>")
            put(b, Path(a.args[0]), a.args[1])
        elif a.cmd == "sync":
            src = ROOT / "media" / "sd"
            files = [p for p in sorted(src.rglob("*")) if p.is_file() and p.name != "preview.png"]
            if not files:
                sys.exit(f"nothing to copy under {src}")
            for p in files:
                put(b, p, p.relative_to(src).as_posix())
        elif a.cmd == "raw":   # test hooks, e.g. raw RIG 4 / raw ASK
            b.send(" ".join(a.args))
            print(b.expect(10, "OK"))
        elif a.cmd == "del":
            b.send(f"DEL {a.args[0]}")
            print(b.expect(10, "OK"))
    finally:
        b.close()


if __name__ == "__main__":
    main()
