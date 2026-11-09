# Development notes

How the firmware is put together, the dev tools, and what was learned bringing it up on this board.

## Files

| File | What it is |
|---|---|
| `magic-8-ball.yaml` | The device: display, touch, IMU, widgets, Wi-Fi/OTA |
| `magic8.h` | The engine: gestures, the reveal choreography, idle physics, power |
| `answers.txt` | **The answers**, for all three modes. The first column is the kind (mode, tier or egg) plus a mood (yes / no / maybe) that the left and right swipes use. Edit freely, then regenerate (below) |
| `tools/make_assets.py` | Renders all art, checks every answer fits its triangle, writes `answers_gen.h`, `assets_gen.h`, `die_frames.yaml`, `assets/preview.png` |
| `components/m8power/` | Small ESPHome driver for the AXP2101 (charger setup, battery, power-off) and the IMU's wake-on-motion. ESPHome has no AXP2101 driver |
| `tools/make_sounds.py` | Synthesizes the 12 sound effects into `sounds/` (22.05 kHz mono WAV, about 0.9 MB in all) |
| `components/m8sd/` | The microSD card: mounts it at `/sd` (FAT32 only) and takes commands over USB (info, format, list, copy in, delete), so the card never leaves the board |
| `components/m8video/` | The rickroll player: frames decoded with Espressif's SIMD `esp_new_jpeg` into an LVGL image, audio through the media player |
| `tools/make_video.py` | Cuts a clip from any video into the card format: `media/sd/m8/<name>.m8v` (512-byte-aligned JPEG frames sized to the round screen) + `.wav` |
| `tools/sdcard.py` | Manages the card over the USB cable: `info`, `format --yes`, `ls`, `put`, `sync` (copies `media/sd/`), `del`, and `raw RIG 4` / `raw ASK` test hooks |
| `media/` | Your source video and the converted card files (git-ignored: bring your own clip) |
| `tools/remote.py` | Ask / force a rarity / stream logs / power report / sleep test over Wi-Fi, no shaking needed |
| `fonts/` | Barlow and Barlow Condensed (SIL OFL) |
| `secrets.yaml` | Your Wi-Fi, API key and OTA password, made from `secrets.yaml.example` (git-ignored) |

## Everyday use

```bash
python3 tools/make_assets.py                                   # after editing answers.txt or the art
esphome run magic-8-ball.yaml --device magic-8-ball.local      # build + update over Wi-Fi
```

Test without shaking (ESPHome's Python ships `aioesphomeapi`):

```bash
ESPPY=$(head -1 "$(which esphome)" | sed 's/^#!//')
$ESPPY tools/remote.py ask                # normal ask
$ESPPY tools/remote.py rarity 2           # 0 common, 1 rare, 2 legendary, 3 glitch, 4 secret
$ESPPY tools/remote.py logs 30            # watch the log
$ESPPY tools/remote.py power              # charger registers + battery state
$ESPPY tools/remote.py sleep              # deep sleep now; pick the ball up to wake it
$ESPPY tools/remote.py womtest 14 50      # 30 s wake-on-motion line test (rate code, mg)
$ESPPY tools/remote.py sound 4            # play one sound (index = the Snd enum in magic8.h)
$ESPPY tools/remote.py rig 1              # rig the next answer: 0 cancel, 1 SKILL ISSUE, 2 yes, 3 no
```

Changing the rickroll clip (the card stays in the board, plugged in over USB):

```bash
python3 tools/make_video.py media/your_video.mp4 --start 42.8 --duration 12
ESPPY=$(head -1 "$(which esphome)" | sed 's/^#!//')
$ESPPY tools/sdcard.py sync                 # copies media/sd/m8/* to the card, CRC-checked
$ESPPY tools/sdcard.py raw RIG 4 && $ESPPY tools/sdcard.py raw ASK   # play it now
```

After changing a sound in `tools/make_sounds.py`: `python3 tools/make_sounds.py`, then `esphome run`. The order of `SOUNDS` there, the `Snd` enum in `magic8.h` and the `files:` list in the YAML must match.

**After an OTA update, leave the board alone for a minute.** ESP-IDF only accepts a new image once it has run for about 60 s. A reset before that (including opening the USB serial port with a script that toggles RTS) silently rolls back to the previous firmware: the log then says `OTA rollback detected`. Flashing over USB has no such window.


## Lessons from bringing it up

- **ESPHome 2026.8 supports this board natively.** It has the `mipi_spi` model `WAVESHARE-ESP32-S3-TOUCH-AMOLED-1.75` (CO5300, QSPI), `cst9220` for touch (it also accepts the CST9217) and `qmi8658` under `motion:`. No custom drivers needed.
- **The IMU is mounted rotated 180° about the screen's vertical axis.** Unmapped, lying flat screen-up reads z = −1, which looked like *face down*: the screen went dark on the table and every pick-up fired an answer. Measured fix: `axis_map: {x: -x, y: y, z: -z}`.
- **Software transforms are the performance wall.** Measured for a triangle-sized 384 × 342 area:
  - flush to the panel: 17.5 ms
  - liquid window image: 8 ms
  - alpha-blended triangle: 23 ms
  - the same triangle rotated or scaled: 80–90 ms (128 ms with anti-aliasing)
  - ARGB8888 is no faster than RGB565A8

  So nothing big is transformed: the tumble is 16 pre-rendered die frames, the answer only fades and drifts, and ball↔window swaps dip the panel's hardware brightness instead of cross-fading two 440 px images.
- **What made it 2–3× faster overall:**
  - `CONFIG_SPIRAM_XIP_FROM_PSRAM` puts images in octal PSRAM, not flash.
  - A 64 KB data cache.
  - LVGL `buffer_size: 12%`, which keeps the draw buffer in internal RAM.
  - An 80 MHz QSPI bus.
- **Keep moving objects under ~15.** LVGL's dirty-area list has 32 slots. Sixteen moving bubbles overflowed it and turned every frame into a full-screen redraw.
- **Pre-dither gradients.** ESPHome converts images to RGB565 by truncating (`r >> 3`), so dark gradients band on the AMOLED. `make_assets.py` applies an ordered dither to exact RGB565 levels.
- **Brightness is a panel command, not PWM.** `set_brightness()` sends 0x51 (plus MADCTL; `display.mipi_spi` logs that at DEBUG, so it's silenced). Fading it costs no rendering.
- **No fallback hotspot and `reboot_timeout: 0s`** on both Wi-Fi and the API. It's a toy that leaves the house and must not reboot looking for the network.
- **Burn-in:** an AMOLED with a static ball all day would burn in. That's why it dims at 45 s and goes dark at 2.5 min.
- **AXP2101 setup** follows XPowersLib (the library Waveshare's examples use):
  - registers: charge voltage 0x64 = 3 (4.2 V), current 0x62 = 11 (500 mA), ADC 0x30 with TS off (no thermistor on this board), battery detection 0x68, fuel gauge + charging enable 0x18;
  - factory USB input limit is 1500 mA (0x16 = 4), left as is;
  - power off is 0x10 bit 0.
- **QMI8658 wake-on-motion, measured on this board:**
  - **Toggles, not pulses:** the INT2 line (GPIO21) is push-pull and *toggles* on every event. Reading STATUS1 (0x2F) resets it to the initial level, and that level only takes effect after the blanking period. So `go_to_sleep` arms it, waits 1.2 s, reads STATUS1 to park the line high, then sleeps waking on low.
  - **The threshold compares consecutive samples.** At the 3 Hz low-power rate it caught **none** of the pick-ups; at 11 Hz / 50 mg and 128 Hz / 200 mg it caught every one. Using 11 Hz (31% duty).
  - **ESPHome's ext0 wake sets no pull-up or pull-down.** That's fine here because the line is driven.
- **Deep sleep holds the panel (GPIO39) and touch (GPIO40) reset lines low.** That's their lowest-power state. An `on_boot` at priority 1000 releases the hold before the drivers try to reset those chips.
- **Audio chain (from Waveshare's BSP):**
  - I2S: MCLK GPIO42, BCLK GPIO9, LRCK GPIO45, data out GPIO8. The ES8311 is an I2S slave using MCLK.
  - The NS4150B amplifier enable is **GPIO46, active high**. It's switched on just before each sound and off when the media player goes idle (no hiss, no idle drain), and held low during deep sleep.
  - ESPHome's `es8311` + `i2s_audio` speaker + `speaker` media player with embedded `files:` cover it. The engine calls `play_file()` from C++.
- **SD card:**
  - The card must be **FAT32**: ESP-IDF's FatFs has exFAT compiled out (`FF_FS_EXFAT 0`). `sdcard.py format --yes` formats it from the board.
  - ESPHome leaves FatFs out by default, so `m8sd` re-includes `fatfs` and `wear_levelling` and calls `require_fatfs()` and `require_vfs_dir()`.
  - **Free space must never be computed on the main loop:** on a 64 GB card FatFs scanned the whole FAT in **9.4 s** and the task watchdog rebooted the board. It now runs in a task.
- **USB file transfer:** ESP-IDF's USB-Serial-JTAG driver empties the hardware FIFO into a **512-byte** ring buffer and **silently drops** what doesn't fit. That defeats USB's own flow control. `sdcard.py` therefore keeps under 512 bytes in flight (448), with an ack every 128. It's safe but slow (about 7 KB/s; the clip took about 5½ minutes).
- **Opening the serial port must not touch DTR/RTS on macOS.** Setting DTR low before RTS momentarily makes "RTS on, DTR off", the ESP32-S3's reset signal, and every reset before the one-minute mark counted toward ESPHome's safe mode.
- **Video:** the build uses ESP-IDF **5.5.5** from ESPHome's cache, not PlatformIO's 5.3.2.
  - `esp_new_jpeg` decodes a 352×272 frame in **13–15 ms** (TJpgDec would be about 80 ms), so all 180 frames play at 15 fps with none skipped.
  - The video image needs an opaque background and even coordinates, or LVGL repaints the ball behind every frame.
  - The WAV (529 KB) is loaded into PSRAM during the churn and played by the media player; the frame clock starts when the audio does.
- **ESPHome lambdas:** `id(x).member` is rewritten to `x->member`, but a bare `id(x)` is already the pointer. `&id(x)` gives a pointer-to-pointer.

