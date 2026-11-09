#!/usr/bin/env python3
"""Poke the Magic 8 Ball over Wi-Fi and watch its log, without shaking it.

Run with ESPHome's Python (it ships aioesphomeapi):

    ESPPY=$(head -1 "$(which esphome)" | sed 's/^#!//')
    $ESPPY tools/remote.py ask                 # a normal ask
    $ESPPY tools/remote.py rarity 2            # 0 common, 1 rare, 2 legendary, 3 glitch, 4 secret
    $ESPPY tools/remote.py index 12            # a specific answer (see answers_gen.h)
    $ESPPY tools/remote.py logs 20             # just stream the log for 20 s
    $ESPPY tools/remote.py power               # charger registers and battery state
    $ESPPY tools/remote.py sleep               # deep sleep now (move the ball to wake it)
    $ESPPY tools/remote.py sound 4             # play a sound (index = Snd enum in magic8.h)

Reads the API key from ../secrets.yaml. Default host magic-8-ball.local (override with --host).
"""
import argparse
import asyncio
import re
from pathlib import Path

from aioesphomeapi import APIClient, LogLevel

ROOT = Path(__file__).resolve().parent.parent


def api_key() -> str:
    for line in (ROOT / "secrets.yaml").read_text().splitlines():
        m = re.match(r'\s*api_key\s*:\s*"?([^"\s]+)"?', line)
        if m:
            return m.group(1)
    raise SystemExit("api_key not found in secrets.yaml")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ask", "rarity", "index", "logs", "power", "sleep", "womtest", "sound", "rig"])
    ap.add_argument("value", nargs="?", type=int, default=0)
    ap.add_argument("value2", nargs="?", type=int, default=0)
    ap.add_argument("--host", default="magic-8-ball.local")
    ap.add_argument("--watch", type=float, default=6.0, help="seconds of log to show afterwards")
    args = ap.parse_args()
    if args.cmd == "logs":
        args.watch = float(args.value or 20)

    client = APIClient(args.host, 6053, None, noise_psk=api_key())
    await client.connect(login=True)

    def on_log(msg) -> None:
        text = re.sub(r"\x1b\[[0-9;]*m", "", msg.message.decode(errors="replace"))
        print(text, flush=True)

    unsub = client.subscribe_logs(on_log, log_level=LogLevel.LOG_LEVEL_DEBUG)
    if args.cmd != "logs":
        _, services = await client.list_entities_services()
        by_name = {s.name: s for s in services}
        name = {"ask": "ask", "rarity": "ask_rarity", "index": "ask_index", "power": "power_report", "sleep": "sleep_now", "womtest": "wom_test", "sound": "play_sound", "rig": "rig"}[args.cmd]
        if args.cmd == "womtest":
            data = {"odr": args.value or 15, "mg": args.value2 or 80}
        else:
            key = {"rarity": "rarity", "rig": "what"}.get(args.cmd, "index")
            data = {} if args.cmd in ("ask", "power", "sleep") else {key: args.value}
        res = client.execute_service(by_name[name], data)
        if asyncio.iscoroutine(res):
            await res
    await asyncio.sleep(args.watch)
    if callable(unsub):
        unsub()
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
