// Magic 8 Ball engine: gestures (shake, flip, tap), the reveal choreography, idle physics
// and power. The big widgets (images, labels) are declared in magic-8-ball.yaml and handed
// over in begin(); bubbles and sparkles are small LVGL objects created here.
//
// Screen coordinates: x right, y down, offsets from the screen centre.
// Accelerometer: magic-8-ball.yaml maps the IMU so x = screen right, y = screen down and
// z = out of the screen. At rest the sensor reads +1 g on the axis pointing up, so lying
// screen-up gives z = +1, and the in-plane direction things roll toward is (-ax, -ay).
//
// Performance budget (measured on this board, for a triangle-sized 384 x 342 area):
//   flush to the panel 17.5 ms; liquid window 8 ms; alpha-blended triangle 23 ms;
//   the same triangle rotated or scaled 80-90 ms. So nothing big is ever transformed:
//   whole-screen swaps dip the panel's hardware brightness (free), the tumble is
//   pre-rendered die frames, and the answer only fades and drifts.
#pragma once

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <esp_random.h>
#include "driver/gpio.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "lvgl.h"
#include "answers_gen.h"
#include "assets_gen.h"

namespace m8 {

static const char *const TAG = "m8";

// ---- Tuning ---------------------------------------------------------------------------
static constexpr float SHAKE_G = 0.9f;         // |a| deviation from 1 g that counts as a shake hit
static constexpr int SHAKE_HITS = 3;           // hits within SHAKE_WINDOW_MS start a shake
static constexpr uint32_t SHAKE_WINDOW_MS = 1000;
static constexpr uint32_t SHAKE_QUIET_MS = 330;   // no hit for this long = shaking stopped
static constexpr float HARD_SHAKE_G = 4.5f;    // peak |a| for the "easy there" easter egg (a kid's
                                               // normal shake passes 3.4 g; that made it repetitive)
static constexpr int EGG_COOLDOWN_ASKS = 15;   // asks before the same easter egg can come back
static constexpr float ACTIVITY_G = 0.10f;     // movement that wakes the screen
static constexpr float FACE_DOWN_Z = -0.72f;
static constexpr uint32_t FACE_DOWN_MS = 450;     // held face down this long = dark
static constexpr uint32_t FLIP_MIN_MS = 700;      // face down this long (but not longer than
static constexpr uint32_t FLIP_MAX_MS = 6000;     // FLIP_MAX_MS: then it was put away), then up = ask
static constexpr uint32_t TAP_MAX_MS = 500;       // a touch shorter than this is a tap
static constexpr uint32_t LONG_PRESS_MS = 1200;   // a touch at least this long toggles sound
static constexpr uint32_t DOUBLE_TAP_MS = 380;    // second tap within this: switch mode
static constexpr uint32_t CARD_SHOW_MS = 3500;    // how long a mode card stays up
static constexpr int TAP_SLOP_PX = 30;            // a touch that moves more than this isn't a tap
static constexpr int SWIPE_MIN_PX = 70;           // and one that moves this far, quickly, is a swipe
static constexpr uint32_t SWIPE_MAX_MS = 900;

static constexpr uint32_t CHURN_MIN_MS = 900;
static constexpr uint32_t CHURN_MAX_MS = 3200;
static constexpr uint32_t RISE_MS = 1300;
static constexpr uint32_t SHOW_MS = 20000;
static constexpr uint32_t DIM_AFTER_MS = 45000;   // idle without movement
static constexpr uint32_t SLEEP_AFTER_MS = 150000;  // then dark; on battery, deep sleep
static constexpr uint32_t FACE_DOWN_SLEEP_MS = 60000;  // face down this long on battery: deep sleep
static constexpr int EMPTY_PERCENT = 2;             // at or below this (or EMPTY_MV) on battery:
static constexpr int EMPTY_MV = 3350;               // show BATTERY EMPTY and power the board off

static constexpr uint8_t BRIGHT_ON = 0xD0;        // CO5300 brightness (the model's default)
static constexpr uint8_t BRIGHT_DIM = 0x30;
static constexpr int PITY = 40;                   // a legendary is guaranteed after this many asks
static constexpr float ROLL_RAD = 48.0f * (float) M_PI / 180.0f;  // disc roll at full tilt
static constexpr int LINE_SPACE = -6;             // must match tools/make_assets.py

// Few enough that LVGL's 32-slot dirty-area list never overflows into a full redraw.
static constexpr int N_BUB = 10;
static constexpr int N_SPARK = 10;

// ---- Helpers ---------------------------------------------------------------------------
static inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
static inline float lerpf(float a, float b, float t) { return a + (b - a) * t; }
static inline float ease_out(float t) {
  t = clampf(t, 0, 1);
  float u = 1 - t;
  return 1 - u * u * u;
}
static inline float ease_in(float t) {
  t = clampf(t, 0, 1);
  return t * t * t;
}
static inline float ease_in_out(float t) {
  t = clampf(t, 0, 1);
  return t < 0.5f ? 4 * t * t * t : 1 - powf(-2 * t + 2, 3) / 2;
}
static inline float phase(uint32_t now, uint32_t start, uint32_t dur) {
  int32_t dt = (int32_t) (now - start);
  if (dt <= 0)
    return 0;
  if (dt >= (int32_t) dur)
    return 1;
  return (float) dt / (float) dur;
}
static inline uint32_t rnd(uint32_t n) { return n ? esp_random() % n : 0; }
static inline float rndf(float a, float b) { return a + (b - a) * (float) (esp_random() >> 8) / 16777216.0f; }

// An LVGL object plus the last values written, so unchanged values cost nothing.
struct Obj {
  lv_obj_t *o{nullptr};
  int x{INT32_MIN}, y{INT32_MIN}, opa{-1}, sx{-1}, sy{-1}, shade{-1}, w{-1};
  uint32_t color{0xFFFFFFFF};

  void pos(int nx, int ny) {
    if (nx == x && ny == y)
      return;
    x = nx;
    y = ny;
    lv_obj_align(o, LV_ALIGN_CENTER, nx, ny);
  }
  void alpha(float af) {
    int a = (int) lroundf(clampf(af, 0, 255));
    if (a == opa)
      return;
    opa = a;
    if (a <= 2) {
      lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
      return;
    }
    lv_obj_set_style_opa(o, (lv_opa_t) a, 0);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
  }
  int vis() const { return opa < 0 ? 0 : opa; }
  void scale(int nsx, int nsy) {
    if (nsx == sx && nsy == sy)
      return;
    sx = nsx;
    sy = nsy;
    lv_image_set_scale_x(o, nsx);
    lv_image_set_scale_y(o, nsy);
  }
  void darken(int a) {
    if (a == shade)
      return;
    shade = a;
    lv_obj_set_style_image_recolor_opa(o, (lv_opa_t) a, 0);
  }
  void text_color(uint32_t c) {
    if (c == color)
      return;
    color = c;
    lv_obj_set_style_text_color(o, lv_color_hex(c), 0);
  }
  void size(int d) {
    if (d == w)
      return;
    w = d;
    lv_obj_set_size(o, d, d);
  }
};

struct Bubble {
  Obj obj;
  bool live{false};
  float x{0}, y{0}, vy{0}, r{0}, sway{0}, ph{0};
  uint32_t born{0};
};

struct Spark {
  Obj obj;
  float x{0}, y{0};
  uint32_t start{0}, life{0};
};

// Frame profiler: logs (at DEBUG, tag m8.prof) any LVGL refresh slower than 45 ms,
// split into rendering and flushing to the panel.
struct Prof {
  uint32_t refr_start{0}, flush_start{0}, flush_us{0}, flush_px{0};
};
static Prof g_prof;
static void prof_cb(lv_event_t *e) {
  uint32_t t = esphome::micros();
  switch (lv_event_get_code(e)) {
    case LV_EVENT_REFR_START:
      g_prof = Prof{};
      g_prof.refr_start = t;
      break;
    case LV_EVENT_FLUSH_START: {
      g_prof.flush_start = t;
      auto *a = static_cast<lv_area_t *>(lv_event_get_param(e));
      if (a)
        g_prof.flush_px += (uint32_t) lv_area_get_size(a);
      break;
    }
    case LV_EVENT_FLUSH_FINISH:
      g_prof.flush_us += t - g_prof.flush_start;
      break;
    case LV_EVENT_REFR_READY: {
      uint32_t total = t - g_prof.refr_start;
      if (total > 45000)
        ESP_LOGD("m8.prof", "refresh %u ms (flush %u ms, %u px)", (unsigned) (total / 1000),
                 (unsigned) (g_prof.flush_us / 1000), (unsigned) g_prof.flush_px);
      break;
    }
    default:
      break;
  }
}

enum class St : uint8_t { BOOT, IDLE, CHURN, RISE, SHOW, SINK, VIDEO };

// Sound effects. Order = SOUNDS in tools/make_sounds.py and the files in magic-8-ball.yaml.
enum Snd : uint8_t {
  SND_TURNOVER, SND_BUBBLES, SND_REVEAL_COMMON, SND_REVEAL_RARE, SND_REVEAL_LEGENDARY, SND_REVEAL_GLITCH,
  SND_REVEAL_SECRET, SND_SINK, SND_WAKE, SND_CHARGE, SND_LOW_BATTERY, SND_SOUND_ON, SND_RIMSHOT, SND_MODE_SWITCH,
  SND_SKILL_ISSUE, SND_COUNT
};
static constexpr uint32_t BUBBLES_LOOP_MS = 1500;  // re-queue the bubbles while a long shake goes on
enum class Src : uint8_t { SHAKE, TAP, FLIP, REMOTE, MODE };

static const char *const MODE_NAMES[MODE_COUNT] = {"ORACLE MODE", "CLASSIC MODE", "ROAST MODE"};
static const char *const MODE_PILLS[MODE_COUNT] = {"MODE 1/3", "MODE 2/3", "MODE 3/3"};
static const uint32_t MODE_RGB[MODE_COUNT] = {0xC9A2FF, 0x9FB4FF, 0xFF9A6B};

// Secret swipes rig the next answer: up = SKILL ISSUE, left = a yes, right = a no, down = cancel.
// In Roast mode, down = the rickroll clip (if it's on the SD card).
enum Rig : uint8_t { RIG_NONE, RIG_SKILL, RIG_YES, RIG_NO, RIG_RICKROLL };
static const char *const RIG_NAMES[] = {"nothing (cancelled)", "SKILL ISSUE", "a yes", "a no", "the RICKROLL"};
static constexpr uint32_t SKILL_RGB = 0x8CFF6E;

class Engine {
 public:
  // ---- wiring ----
  void begin(lv_obj_t *window, lv_obj_t *sphere, lv_obj_t *disc, lv_obj_t *spec, lv_obj_t *die,
             lv_obj_t *tri_blue, lv_obj_t *tri_purple, lv_obj_t *tri_gold, lv_obj_t *tri_red, lv_obj_t *tri_skill,
             lv_obj_t *label,
             lv_obj_t *label_small,
             lv_obj_t *pill) {
    window_.o = window;
    sphere_.o = sphere;
    disc_.o = disc;
    spec_.o = spec;
    die_.o = die;
    tri_[0].o = tri_blue;
    tri_[1].o = tri_purple;
    tri_[2].o = tri_gold;
    tri_[3].o = tri_red;
    tri_[4].o = tri_skill;
    label_[0].o = label;
    label_[1].o = label_small;
    pill_.o = pill;

    uint32_t now = esphome::millis();
    window_.alpha(0);
    sphere_.alpha(255);
    spec_.alpha(255);
    spec_.pos(SPEC_X, SPEC_Y);
    lv_obj_set_style_image_recolor(disc, lv_color_black(), 0);
    disc_.alpha(255);
    die_.alpha(0);
    for (auto &t : tri_) {
      t.pos(0, TRI_IMG_DY);
      t.alpha(0);
    }
    for (auto &l : label_) {
      lv_obj_set_style_text_line_space(l.o, LINE_SPACE, 0);  // ESPHome's schema rejects negatives
      l.alpha(0);
    }
    pill_.alpha(0);

    lv_obj_t *parent = lv_obj_get_parent(window);
    for (auto &b : bub_) {
      lv_obj_t *o = lv_obj_create(parent);
      lv_obj_remove_style_all(o);
      lv_obj_remove_flag(o, (lv_obj_flag_t) (LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE));
      lv_obj_set_style_radius(o, LV_RADIUS_CIRCLE, 0);
      lv_obj_set_style_bg_color(o, lv_color_hex(0x7E95FF), 0);
      lv_obj_set_style_bg_opa(o, 34, 0);
      lv_obj_set_style_border_color(o, lv_color_hex(0xC2CEFF), 0);
      lv_obj_set_style_border_width(o, 1, 0);
      lv_obj_set_style_border_opa(o, 170, 0);
      lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
      // Bubbles swim in front of the die and behind the answer.
      lv_obj_move_to_index(o, lv_obj_get_index(tri_blue));
      b.obj.o = o;
      b.obj.opa = 0;
    }
    for (auto &s : spark_) {
      lv_obj_t *o = lv_obj_create(parent);
      lv_obj_remove_style_all(o);
      lv_obj_remove_flag(o, (lv_obj_flag_t) (LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE));
      lv_obj_set_style_radius(o, LV_RADIUS_CIRCLE, 0);
      lv_obj_set_style_bg_color(o, lv_color_hex(0xFFF1B8), 0);
      lv_obj_set_style_bg_opa(o, 255, 0);
      lv_obj_set_style_shadow_color(o, lv_color_hex(0xFFB424), 0);
      lv_obj_set_style_shadow_width(o, 12, 0);
      lv_obj_set_style_shadow_spread(o, 1, 0);
      lv_obj_set_style_shadow_opa(o, 220, 0);
      lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
      s.obj.o = o;
      s.obj.opa = 0;
    }

    lv_display_t *disp = lv_obj_get_display(window);
    lv_display_add_event_cb(disp, prof_cb, LV_EVENT_REFR_START, nullptr);
    lv_display_add_event_cb(disp, prof_cb, LV_EVENT_FLUSH_START, nullptr);
    lv_display_add_event_cb(disp, prof_cb, LV_EVENT_FLUSH_FINISH, nullptr);
    lv_display_add_event_cb(disp, prof_cb, LV_EVENT_REFR_READY, nullptr);

    // The disc starts above the top edge and rolls in.
    dpx_ = 0;
    dpy_ = -1.9f;
    st_ = St::BOOT;
    t0_ = now;
    last_activity_ = now;
    last_tick_ = now;
    ready_ = true;
    this->apply_mode_look_();
    ESP_LOGI(TAG, "Ready: %d answers, %d die frames, %s", N_ANSWERS, die_n_, MODE_NAMES[mode_]);
  }
  /// Mode: the stored one at boot, a callback to store changes, and the 8 disc for each look.
  void set_mode(int mode) { mode_ = (mode >= 0 && mode < MODE_COUNT) ? mode : ORACLE; }
  void set_mode_store_fn(std::function<void(int)> f) { mode_store_ = std::move(f); }
  void set_disc_images(const lv_image_dsc_t *normal, const lv_image_dsc_t *roast) {
    disc_dsc_[0] = normal;
    disc_dsc_[1] = roast;
  }
  void set_die_frames(const lv_image_dsc_t *const *frames, int n) {
    die_frames_ = frames;
    die_n_ = n;
  }
  void set_brightness_fn(std::function<void(uint8_t)> f) { set_brightness_ = std::move(f); }
  /// Plays a sound effect (Snd); `enqueue` waits for the current one instead of cutting it.
  void set_sound_fn(std::function<void(int, bool)> f) { sound_fn_ = std::move(f); }
  /// Sound on/off: the stored setting at boot, and a callback to store changes.
  void set_sound_enabled(bool on) { sound_on_ = on; }
  void set_sound_store_fn(std::function<void(bool)> f) { sound_store_ = std::move(f); }
  void play(int snd, bool enqueue = false) {
    if (sound_on_ && sound_fn_ && snd >= 0 && snd < SND_COUNT)
      sound_fn_(snd, enqueue);
  }
  void set_battery_label(lv_obj_t *label) {
    batt_.o = label;
    batt_.alpha(0);
  }
  /// Deep sleep (wake on motion). Called once the screen is dark on battery.
  void set_sleep_fn(std::function<void()> f) { sleep_fn_ = std::move(f); }
  /// Cuts power to the whole board; used when the battery is empty.
  void set_poweroff_fn(std::function<void()> f) { poweroff_fn_ = std::move(f); }

  /// Test hook: deep sleep right away, even on USB power.
  void sleep_now() {
    if (sleep_fn_) {
      ESP_LOGI(TAG, "Deep sleep (requested)");
      sleep_fn_();
    }
  }

  /// Battery state from the PMU, every couple of seconds.
  void set_power(bool battery, bool usb, bool charging, int charge_state, int percent, int mv) {
    uint32_t now = esphome::millis();
    if (p_known_ && usb && !p_usb_) {
      ESP_LOGI(TAG, "USB power in: %s %d%%", charging ? "charging" : "not charging", percent);
      this->activity_(now);
      if (st_ == St::IDLE || st_ == St::BOOT)
        ping_start_ = now;  // a little "got it" flash
      this->play(SND_CHARGE);
    } else if (p_known_ && !usb && p_usb_) {
      ESP_LOGI(TAG, "USB power out: %d%%", percent);
      this->activity_(now);
      unplug_show_until_ = now + 5000;
    }
    // A soft warning each time the charge drops to 15% (and again at 5%) on battery.
    if (p_known_ && battery && !usb && percent >= 0 && p_pct_ >= 0 &&
        ((p_pct_ > 15 && percent <= 15) || (p_pct_ > 5 && percent <= 5))) {
      this->activity_(now);
      this->play(SND_LOW_BATTERY);
    }
    p_known_ = true;
    p_batt_ = battery;
    p_usb_ = usb;
    p_charging_ = charging;
    p_state_ = charge_state;
    p_pct_ = percent;
    // Protect the cell: three low readings in a row on battery, then power off.
    bool low = battery && !usb && ((percent >= 0 && percent <= EMPTY_PERCENT) || (mv > 0 && mv < EMPTY_MV));
    empty_count_ = low ? empty_count_ + 1 : 0;
    if (usb)
      empty_shutdown_ = false;
    if (empty_count_ >= 3 && !empty_shutdown_) {
      ESP_LOGW(TAG, "Battery empty (%d%%, %d mV): powering off", percent, mv);
      empty_shutdown_ = true;
      empty_at_ = now;
      this->activity_(now);
      this->play(SND_LOW_BATTERY);
    }
  }
  void set_hour_fn(std::function<int()> f) { hour_now_ = std::move(f); }

  // ---- inputs ----
  void on_accel(float x, float y, float z) {
    uint32_t now = esphome::millis();
    float dt = last_sample_ ? (float) (now - last_sample_) / 1000.0f : 0.02f;
    dt = clampf(dt, 0.004f, 0.1f);
    last_sample_ = now;
    float mag = sqrtf(x * x + y * y + z * z);
    if (mag < 0.25f)
      return;  // no real data (the IMU is being reconfigured); a real ball always feels gravity

    float a = dt / (0.10f + dt);  // tilt: fast low-pass
    lx_ += (x - lx_) * a;
    ly_ += (y - ly_) * a;
    lz_ += (z - lz_) * a;
    float b = dt / (1.5f + dt);  // baseline: slow low-pass
    bx_ += (x - bx_) * b;
    by_ += (y - by_) * b;
    bz_ += (z - bz_) * b;
    if (fabsf(x - bx_) + fabsf(y - by_) + fabsf(z - bz_) > ACTIVITY_G)
      this->activity_(now);

    if (fabsf(mag - 1.0f) > SHAKE_G && now - last_hit_ > 80) {
      hits_[hit_i_++ % 8] = now;
      last_hit_ = now;
      int n = 0;
      for (uint32_t h : hits_)
        if (h && now - h < SHAKE_WINDOW_MS)
          n++;
      if (n >= SHAKE_HITS && !shaking_) {
        shaking_ = true;
        peak_ = 0;
        ESP_LOGD(TAG, "Shake started");
        this->trigger(Src::SHAKE);
      }
    }
    if (shaking_) {
      if (mag > peak_)
        peak_ = mag;
      if (now - last_hit_ > SHAKE_QUIET_MS) {
        shaking_ = false;
        ESP_LOGD(TAG, "Shake stopped, peak %.1f g", peak_);
      }
    }

    // Face down / flip. Ignored while shaking: a shake passes through every orientation.
    bool calm = !shaking_ && now - last_hit_ > 400;
    if (z < FACE_DOWN_Z && calm) {
      if (!down_since_)
        down_since_ = now;
      if (!face_down_ && now - down_since_ > FACE_DOWN_MS) {
        face_down_ = true;
        ESP_LOGD(TAG, "Face down");
      }
    } else if (z > -0.1f) {
      if (face_down_) {
        face_down_ = false;
        uint32_t held = now - down_since_;
        ESP_LOGD(TAG, "Face up after %u ms", (unsigned) held);
        if (held >= FLIP_MIN_MS && held <= FLIP_MAX_MS)
          this->trigger(Src::FLIP);
      }
      down_since_ = 0;
    }

    if (now - last_imu_log_ > 2000) {
      last_imu_log_ = now;
      ESP_LOGD("m8.imu", "a=[%.2f %.2f %.2f] |a|=%.2f state=%d bright=%d", lx_, ly_, lz_, mag, (int) st_,
               bright_sent_);
    }
  }

  void touch_down(int x, int y) {
    uint32_t now = esphome::millis();
    touch_at_ = now;
    tx0_ = tx_ = x;
    ty0_ = ty_ = y;
    touch_dark_ = bright_ < BRIGHT_DIM - 4;
    this->activity_(now);
  }
  void touch_move(int x, int y) {
    tx_ = x;
    ty_ = y;
  }
  void touch_up() {
    uint32_t now = esphome::millis();
    uint32_t held = now - touch_at_;
    int dx = tx_ - tx0_, dy = ty_ - ty0_;
    int adx = abs(dx), ady = abs(dy);
    if (touch_at_ && (adx > TAP_SLOP_PX || ady > TAP_SLOP_PX)) {  // moved: a swipe or nothing
      bool clear_direction = adx > ady * 13 / 10 || ady > adx * 13 / 10;
      if (!touch_dark_ && held <= SWIPE_MAX_MS && (adx >= SWIPE_MIN_PX || ady >= SWIPE_MIN_PX) && clear_direction) {
        ESP_LOGD(TAG, "Swipe dx=%d dy=%d in %u ms", dx, dy, (unsigned) held);
        if (ady > adx) {
          int down = mode_ == ROAST_MODE && video_.available && video_.available() ? RIG_RICKROLL : RIG_NONE;
          this->rig_(now, dy < 0 ? RIG_SKILL : down, 0, dy < 0 ? -1 : 1);
        }
        else
          this->rig_(now, dx < 0 ? RIG_YES : RIG_NO, dx < 0 ? -1 : 1, 0);
      }
      touch_at_ = 0;
      return;
    }
    if (touch_at_ && held <= TAP_MAX_MS && st_ == St::VIDEO) {  // a tap skips the clip
      touch_at_ = 0;
      this->end_video_(now);
      return;
    }
    if (touch_at_ && held <= TAP_MAX_MS && !touch_dark_) {  // a tap on a dark screen only wakes it
      // One tap asks, two switch mode: hold the first one back briefly to tell them apart.
      if (tap_pending_ && now - tap_at_ <= DOUBLE_TAP_MS) {
        tap_pending_ = false;
        this->next_mode_(now);
      } else {
        tap_pending_ = true;
        tap_at_ = now;
      }
    } else if (touch_at_ && held >= LONG_PRESS_MS && !touch_dark_) {
      sound_on_ = !sound_on_;
      if (sound_store_)
        sound_store_(sound_on_);
      this->toast_(now, sound_on_ ? "SOUND ON" : "SOUND OFF", sound_on_ ? 0x6DFFB0 : 0xFFFFFF);
      this->play(SND_SOUND_ON);
      ESP_LOGI(TAG, "Sound %s", sound_on_ ? "on" : "off");
    }
    touch_at_ = 0;
  }
  void on_button() { this->trigger(Src::TAP); }

  /// Remote test hook: 0 cancel, 1 SKILL ISSUE, 2 a yes, 3 a no, 4 the rickroll (any mode).
  void rig(int r) { this->rig_(esphome::millis(), r >= RIG_NONE && r <= RIG_RICKROLL ? r : RIG_NONE, 0, 0); }

  /// The rickroll player (components/m8video), wired from YAML.
  struct VideoFns {
    std::function<bool()> available;
    std::function<void(bool)> prepare;  // with sound
    std::function<int()> state;         // 0 idle, 1 loading, 2 ready, 3 failed
    std::function<void()> start, stop;
    std::function<bool()> done;
  };
  void set_video(VideoFns v) { video_ = std::move(v); }

  void rig_(uint32_t now, int r, int dir_x, int dir_y) {
    rig_state_ = r;
    ESP_LOGI(TAG, "Next answer rigged: %s", RIG_NAMES[r]);
    // The only acknowledgement looks like the ball reacting to the finger: the 8 sloshes
    // that way (or the answer on screen nudges), nothing anyone else would read as a signal.
    if (sphere_.vis() > 0) {
      dvx_ += 2.0f * dir_x;
      dvy_ += 2.0f * dir_y;
    } else {
      kick_x_ = 10.0f * dir_x;
      kick_y_ = 10.0f * dir_y;
      kick_at_ = now;
    }
  }

  void next_mode_(uint32_t now) {
    if (!ready_ || st_ == St::CHURN || st_ == St::RISE || st_ == St::VIDEO)
      return;
    mode_ = (mode_ + 1) % MODE_COUNT;
    if (mode_store_)
      mode_store_(mode_);
    ESP_LOGI(TAG, "Switched to %s", MODE_NAMES[mode_]);
    card_pending_ = true;
    this->trigger(Src::MODE);  // the ball turns over and the new mode's card surfaces
  }

  void apply_mode_look_() {
    const lv_image_dsc_t *d = disc_dsc_[mode_ == ROAST_MODE ? 1 : 0];
    if (d != nullptr && disc_.o != nullptr)
      lv_image_set_src(disc_.o, d);
  }

  void ask(int rarity, int index) {
    forced_rarity_ = rarity;
    forced_index_ = index;
    this->trigger(Src::REMOTE);
  }

  void trigger(Src s) {
    if (!ready_)
      return;
    uint32_t now = esphome::millis();
    this->activity_(now);
    if (st_ == St::CHURN || st_ == St::RISE || st_ == St::VIDEO)
      return;
    src_ = s;
    rick_ = false;
    if (rig_state_ == RIG_RICKROLL && s != Src::MODE && video_.prepare) {
      rig_state_ = RIG_NONE;
      rick_ = true;
      video_.prepare(sound_on_);  // loads from the SD card while the ball churns
    }
    if (s != Src::MODE)
      asks_[ask_i_++ % 3] = now;
    // Animate out from whatever is on screen right now (also handles interrupted fades).
    from_ball_ = sphere_.vis() > 0;
    swapped_ = false;
    if (!from_ball_)
      this->veil_to_(1, 6.0f);  // e.g. interrupted mid-dip on the way back to the ball
    c_tri_ = tri_[tri_idx_].vis();
    c_lbl_ = label_[lbl_idx_].vis();
    c_pill_ = pill_.vis();
    c_y_ = tri_[tri_idx_].y == INT32_MIN ? TRI_IMG_DY : tri_[tri_idx_].y;
    die_on_ = false;
    disc_away_ = true;
    st_ = St::CHURN;
    t0_ = now;
    frames_ = 0;
    if (from_ball_) {
      this->play(SND_TURNOVER);
      this->play(SND_BUBBLES, true);
      bubbles_next_ = now + 700 + BUBBLES_LOOP_MS;
    } else {
      this->play(SND_BUBBLES);
      bubbles_next_ = now + BUBBLES_LOOP_MS;
    }
    ESP_LOGI(TAG, "Ask (%s)",
             s == Src::SHAKE  ? "shake"
             : s == Src::TAP  ? "tap"
             : s == Src::FLIP ? "flip"
             : s == Src::MODE ? "mode switch"
                              : "remote");
  }

  // ---- per-frame update (called every 16 ms) ----
  void tick() {
    if (!ready_)
      return;
    uint32_t now = esphome::millis();
    uint32_t gap = now - last_tick_;
    float dt = clampf((float) gap / 1000.0f, 0.001f, 0.1f);
    last_tick_ = now;
    frames_++;

    if (tap_pending_ && now - tap_at_ > DOUBLE_TAP_MS) {
      tap_pending_ = false;
      this->trigger(Src::TAP);
    }
    this->update_power_(now, dt);
    bool asleep = bright_sent_ == 0 && bright_target_ == 0;
    if (!asleep || st_ != St::IDLE)
      this->update_disc_(dt);

    switch (st_) {
      case St::BOOT:
        if (!woke_sound_ && now - t0_ > 250) {
          woke_sound_ = true;
          this->play(SND_WAKE);
          this->toast_(now, MODE_NAMES[mode_], MODE_RGB[mode_]);  // which mode it's in
        }
        if (now - t0_ > 900)
          this->enter_idle_(now);
        break;
      case St::IDLE:
        break;
      case St::CHURN:
        this->churn_(now);
        break;
      case St::RISE:
        this->rise_(now);
        break;
      case St::SHOW:
        this->show_(now);
        break;
      case St::SINK:
        this->sink_(now);
        break;
      case St::VIDEO:
        if (video_.done && video_.done())
          this->end_video_(now);
        break;
    }
    this->update_die_(dt);
    this->update_bubbles_(now, dt);
    this->update_sparks_(now);
    this->update_battery_pill_(now);
    if (empty_shutdown_ && !powered_off_ && now - empty_at_ > 3500 && poweroff_fn_) {
      powered_off_ = true;
      poweroff_fn_();
    }
  }

 protected:
  // ---- states ----
  void churn_(uint32_t now) {
    uint32_t el = now - t0_;
    if (from_ball_) {
      // The 8 rolls away, the ball turns over in the dark, the liquid window lights up.
      if (!swapped_) {
        if (el > 140)
          this->veil_to_(0, 6.0f);
        if (el > 140 && veil_ <= 0.01f) {
          sphere_.alpha(0);
          spec_.alpha(0);
          disc_.alpha(0);
          window_.alpha(255);
          swapped_ = true;
          swap_at_ = now;
          this->veil_to_(1, 4.5f);
        }
      }
    } else {
      // A previous answer sinks back into the murk (fade and drift).
      float q = phase(now, t0_, 380);
      Obj &t = tri_[tri_idx_];
      t.alpha(c_tri_ * (1 - q) * (1 - q));
      t.pos(t.x == INT32_MIN ? 0 : t.x, c_y_ + (int) lroundf(50 * ease_in(q)));
      label_[lbl_idx_].alpha(c_lbl_ * (1 - phase(now, t0_, 160)));
      pill_.alpha(c_pill_ * (1 - phase(now, t0_, 160)));
    }

    bool window_up = !from_ball_ || swapped_;
    if (window_up) {
      // The die tumbles deep in the liquid, faster while it's being shaken.
      if (!die_on_) {
        die_on_ = true;
        die_t0_ = now;
        die_x0_ = rndf(-35, 35);
      }
      float fade = ease_out(phase(now, die_t0_, 450));
      die_y_ = lerpf(112, 62, ease_out(phase(now, die_t0_, 1600)));
      die_x_ = die_x0_ + 16 * sinf((float) (now - die_t0_) * 0.0021f);
      die_opa_ = 130 * fade;
      die_fps_ = shaking_ ? 30 : 15;
      if (now - last_bubble_ > 80 + rnd(70)) {
        this->spawn_bubble_(now, false);
        last_bubble_ = now;
      }
    }

    if ((int32_t) (now - bubbles_next_) > 0) {
      this->play(SND_BUBBLES, true);
      bubbles_next_ = now + BUBBLES_LOOP_MS;
    }

    bool still = !shaking_ && now - last_hit_ > SHAKE_QUIET_MS;
    uint32_t min_ms = src_ == Src::SHAKE ? CHURN_MIN_MS : src_ == Src::MODE ? 500 : 800;
    bool settled = die_on_ && now - die_t0_ > 450;
    if (((el > min_ms && still) || el > CHURN_MAX_MS) && settled) {
      if (rick_) {
        int vs = video_.state ? video_.state() : 3;
        if (vs == 2) {
          this->start_video_(now);
          return;
        }
        if (vs == 1 && el < 6000)
          return;  // still loading: keep churning a little longer
        rick_ = false;  // couldn't load it: give a normal answer
        ESP_LOGW(TAG, "Rickroll clip not ready (state %d): normal answer", vs);
      }
      this->start_rise_(now);
    }
  }

  // The rickroll: everything else off, the clip plays full brightness, then the end card.
  void start_video_(uint32_t now) {
    window_.alpha(0);
    sphere_.alpha(0);
    spec_.alpha(0);
    disc_.alpha(0);
    die_opa_ = 0;
    die_.alpha(0);
    for (auto &t : tri_)
      t.alpha(0);
    for (auto &l : label_)
      l.alpha(0);
    pill_.alpha(0);
    for (auto &b : bub_) {
      b.live = false;
      b.obj.alpha(0);
    }
    this->veil_to_(1, 8.0f);
    st_ = St::VIDEO;
    t0_ = now;
    ESP_LOGI(TAG, "Rickroll!");
    if (video_.start)
      video_.start();
  }

  void end_video_(uint32_t now) {
    if (video_.stop)
      video_.stop();
    rick_ = false;
    window_.alpha(255);
    for (int i = 0; i < N_ANSWERS; i++)
      if (ANSWERS[i].rarity == RICKROLLED)
        forced_index_ = i;
    this->start_rise_(now);  // "YOU GOT RICKROLLED"
  }

  void veil_to_(float target, float rate) {
    veil_target_ = target;
    veil_rate_ = rate;
  }

  void start_rise_(uint32_t now) {
    for (auto &t : tri_)
      t.alpha(0);
    for (auto &l : label_)
      l.alpha(0);
    pill_.alpha(0);

    answer_ = this->pick_answer_(now);
    const Answer &a = ANSWERS[answer_];
    rarity_ = a.rarity;
    // Triangle: gold legendary; red for anything Roast; purple for rare, Oracle eggs and the
    // Oracle card; blue otherwise (common, glitch, classic).
    bool roasty = rarity_ == ROAST || rarity_ == RICKROLLED || (rarity_ == SECRET && a.mode == ROAST_MODE) ||
                  (rarity_ == CARD && a.tag == ROAST_MODE);
    if (rarity_ == SKILL)
      tri_idx_ = 4;
    else if (rarity_ == LEGENDARY)
      tri_idx_ = 2;
    else if (roasty)
      tri_idx_ = 3;
    else if (rarity_ == RARE || rarity_ == SECRET || (rarity_ == CARD && a.tag == ORACLE))
      tri_idx_ = 1;
    else
      tri_idx_ = 0;
    lbl_idx_ = a.small ? 1 : 0;
    lv_label_set_text_static(label_[lbl_idx_].o, a.text);
    text_rgb_ = rarity_ == LEGENDARY ? 0x2B1A00 : rarity_ == SKILL ? SKILL_RGB : 0xFFFFFF;
    label_[lbl_idx_].text_color(text_rgb_);
    const char *pill = nullptr;
    uint32_t pill_rgb = 0xFFFFFF;
    switch (rarity_) {
      case RARE:
        pill = "RARE";
        pill_rgb = 0xC9A2FF;
        break;
      case LEGENDARY:
        pill = "LEGENDARY";
        pill_rgb = 0xFFD86B;
        break;
      case GLITCH:
        pill = "GLITCH";
        pill_rgb = 0x4DF3FF;
        break;
      case SECRET:
        pill = "SECRET";
        pill_rgb = 0x6DFFB0;
        break;
      case ROAST:
      case RICKROLLED:
        pill = "ROASTED";
        pill_rgb = 0xFF9A6B;
        break;
      case CARD:
        pill = MODE_PILLS[a.tag < MODE_COUNT ? a.tag : 0];
        pill_rgb = 0xFFFFFF;
        break;
      case SKILL:
        pill = "DIAGNOSIS";
        pill_rgb = SKILL_RGB;
        break;
      default:
        break;
    }
    has_pill_ = pill != nullptr;
    if (pill) {
      lv_label_set_text_static(pill_.o, pill);
      pill_.text_color(pill_rgb);
      lv_obj_set_style_border_color(pill_.o, lv_color_hex(pill_rgb), 0);
    }
    glitch_until_ = rarity_ == GLITCH ? now + 2300 : 0;
    next_glitch_ = now + 4000;
    for (auto &s : spark_) {
      s.life = 0;
      s.start = now + 600 + rnd(900);
    }
    rise_die_x_ = die_x_;
    rise_die_y_ = die_y_;
    rise_die_opa_ = die_opa_;
    pinged_ = false;
    int snd = SND_REVEAL_COMMON;  // common, classic
    switch (rarity_) {
      case RARE:
        snd = SND_REVEAL_RARE;
        break;
      case LEGENDARY:
        snd = SND_REVEAL_LEGENDARY;
        break;
      case GLITCH:
        snd = SND_REVEAL_GLITCH;
        break;
      case SECRET:
        snd = roasty ? SND_RIMSHOT : SND_REVEAL_SECRET;
        break;
      case ROAST:
      case RICKROLLED:
        snd = SND_RIMSHOT;
        break;
      case CARD:
        snd = SND_MODE_SWITCH;
        break;
      case SKILL:
        snd = SND_SKILL_ISSUE;
        break;
      default:
        break;
    }
    this->play(snd);  // cuts the bubbles; its landing sound comes with the flash
    ESP_LOGI(TAG, "Answer %d (rarity %d, %u frames churned): %s", answer_, rarity_, (unsigned) frames_, a.text);
    st_ = St::RISE;
    t0_ = now;
    frames_ = 0;
  }

  // The die floats up to the glass and slows; its face comes forward as the answer.
  void rise_(uint32_t now) {
    uint32_t el = now - t0_;
    float pd = ease_in_out(phase(now, t0_, 520));
    die_x_ = lerpf(rise_die_x_, 0, pd);
    die_y_ = lerpf(rise_die_y_, -8, pd);
    die_fps_ = lerpf(15, 4, pd);
    die_opa_ = lerpf(rise_die_opa_, 215, pd) * (1 - ease_in_out(phase(now, t0_ + 380, 400)));

    float pf = phase(now, t0_ + 330, 650);
    float face = 255 * ease_in_out(pf);
    tri_[tri_idx_].alpha(face);
    label_[lbl_idx_].alpha(face);  // the words are printed on the face: they surface with it
    this->place_answer_(now, 0, (int) lroundf(26 * (1 - ease_out(pf))));
    if (has_pill_)
      pill_.alpha(255 * ease_in_out(phase(now, t0_ + 900, 350)));
    if (!pinged_ && el >= 960) {
      pinged_ = true;
      ping_start_ = now;  // a flash of light as the face touches the glass
      if (rarity_ == SKILL)
        thud_until_ = now + 500;  // SKILL ISSUE lands with a thud
    }
    if (el >= RISE_MS) {
      die_opa_ = 0;
      ESP_LOGD(TAG, "Rise: %u frames in %u ms", (unsigned) frames_, (unsigned) el);
      st_ = St::SHOW;
      t0_ = now;
    }
  }

  void show_(uint32_t now) {
    float bob = 4.0f * sinf((float) (now - t0_) * 2.0f * (float) M_PI / 3400.0f);
    this->place_answer_(now, 0, (int) lroundf(bob));
    if (now - last_bubble_ > 1500 + rnd(1500)) {
      this->spawn_bubble_(now, true);
      last_bubble_ = now;
    }
    if (rarity_ == GLITCH && now > next_glitch_) {
      glitch_until_ = now + 220;
      next_glitch_ = now + 3000 + rnd(3000);
    }
    if (now - t0_ > (rarity_ == CARD ? CARD_SHOW_MS : SHOW_MS))
      this->start_sink_(now);
  }

  void start_sink_(uint32_t now) {
    c_tri_ = tri_[tri_idx_].vis();
    c_lbl_ = label_[lbl_idx_].vis();
    c_pill_ = pill_.vis();
    swapped_ = false;
    this->play(SND_SINK);
    st_ = St::SINK;
    t0_ = now;
  }

  // The answer sinks, the screen dips, the ball comes back and the 8 rolls in from the top.
  void sink_(uint32_t now) {
    uint32_t el = now - t0_;
    if (!swapped_) {
      float p = phase(now, t0_, 420);
      tri_[tri_idx_].alpha(c_tri_ * (1 - p) * (1 - p));
      this->place_answer_(now, 0, (int) lroundf(30 * ease_in(p)));
      label_[lbl_idx_].alpha(c_lbl_ * (1 - phase(now, t0_, 220)));
      pill_.alpha(c_pill_ * (1 - phase(now, t0_, 220)));
      if (el > 300)
        this->veil_to_(0, 5.0f);
      if (el > 300 && veil_ <= 0.01f) {
        this->show_ball_();
        disc_away_ = false;
        dpx_ = 0;
        dpy_ = -1.9f;
        dvx_ = dvy_ = 0;
        swapped_ = true;
        swap_at_ = now;
        this->veil_to_(1, 3.5f);
      }
    } else if (now - swap_at_ > 700) {
      this->enter_idle_(now);
    }
  }

  void show_ball_() {
    this->apply_mode_look_();  // a new mode's 8 appears as the ball comes back
    for (auto &t : tri_)
      t.alpha(0);
    for (auto &l : label_)
      l.alpha(0);
    pill_.alpha(0);
    die_opa_ = 0;
    window_.alpha(0);
    sphere_.alpha(255);
    spec_.alpha(255);
  }

  void enter_idle_(uint32_t now) {
    this->show_ball_();
    this->veil_to_(1, 4.0f);
    disc_away_ = false;
    st_ = St::IDLE;
    t0_ = now;
  }

  // Triangle, text and pill move together; (dx, dy) is added to the resting layout.
  void place_answer_(uint32_t now, int dx, int dy) {
    // Gentle parallax: when the ball moves, the answer drifts a few pixels toward the low side.
    dx += (int) lroundf(clampf(-(lx_ - bx_) * 2, -1, 1) * 8);
    dy += (int) lroundf(clampf(-(ly_ - by_) * 2, -1, 1) * 8);
    if (now < thud_until_) {
      float k = (float) (thud_until_ - now) / 500.0f;
      dx += (int) lroundf(rndf(-7, 7) * k);
      dy += (int) lroundf(rndf(-5, 5) * k);
    }
    if (kick_at_ && now - kick_at_ < 600) {
      float k = expf(-(float) (now - kick_at_) / 120.0f);
      dx += (int) lroundf(kick_x_ * k);
      dy += (int) lroundf(kick_y_ * k);
    }
    int jx = 0;
    if (now < glitch_until_) {
      if (rnd(100) < 40)
        jx = (int) rnd(19) - 9;
      static const uint32_t GLITCH_RGB[] = {0xFFFFFF, 0x4DF3FF, 0xFF4DD8, 0xFFFFFF};
      label_[lbl_idx_].text_color(GLITCH_RGB[rnd(4)]);
    } else {
      label_[lbl_idx_].text_color(text_rgb_);
    }
    tri_[tri_idx_].pos(dx + jx, TRI_IMG_DY + dy);
    label_[lbl_idx_].pos(dx - jx, ANSWERS[answer_].dy + dy);
    pill_.pos(dx, -98 - 34 + dy);
  }

  // ---- answers ----
  int pick_answer_(uint32_t now) {
    if (card_pending_) {  // a mode switch shows the new mode's card
      card_pending_ = false;
      for (int i = 0; i < N_ANSWERS; i++)
        if (ANSWERS[i].rarity == CARD && ANSWERS[i].tag == mode_)
          return i;
    }
    int forced_index = forced_index_, forced_rarity = forced_rarity_;
    forced_index_ = forced_rarity_ = -1;
    if (forced_index >= 0 && forced_index < N_ANSWERS)
      return this->remember_(forced_index);
    ask_count_++;

    // A rigged answer (secret swipe) beats everything else, then it's used up.
    if (rig_state_ != RIG_NONE && forced_rarity < 0) {
      int r = rig_state_;
      rig_state_ = RIG_NONE;
      if (r == RIG_SKILL) {
        int pick = this->pick_from_([](const Answer &a) { return a.rarity == SKILL; });
        if (pick >= 0)
          return this->remember_(pick);
      } else {
        int mood = r == RIG_YES ? MOOD_YES : MOOD_NO;
        int rarity = COMMON;
        if (mode_ == CLASSIC_MODE) {
          rarity = CLASSIC;
        } else if (mode_ == ROAST_MODE) {
          rarity = ROAST;
        } else {  // Oracle keeps its usual odds, so a rigged answer looks like any other
          uint32_t roll = rnd(1000);
          rarity = roll < 30 ? LEGENDARY : roll < 190 ? RARE : COMMON;
        }
        int pick = this->pick_from_([rarity, mood](const Answer &a) { return a.rarity == rarity && a.mood == mood; });
        if (pick < 0)  // e.g. no "no" in that tier: fall back to a common one
          pick = this->pick_from_([mood](const Answer &a) { return a.rarity == COMMON && a.mood == mood; });
        if (pick >= 0)
          return this->remember_(pick);
      }
    }

    // Easter eggs (not in Classic): rare, rotating variants, and a cooldown per egg so the
    // same one can't come back for a while.
    if (forced_rarity < 0 && mode_ != CLASSIC_MODE) {
      bool spam = asks_[0] && asks_[1] && asks_[2] && now - asks_[0] < 25000 && now - asks_[1] < 25000 &&
                  now - asks_[2] < 25000;
      int hour = hour_now_ ? hour_now_() : -1;
      bool night = hour >= 0 && (hour >= 22 || hour < 6);
      int egg = -1;
      if (src_ == Src::SHAKE && peak_ > HARD_SHAKE_G && rnd(100) < 30)
        egg = EGG_HARD;
      else if (spam && rnd(100) < 35)
        egg = EGG_SPAM;
      else if (night && rnd(100) < 25)
        egg = EGG_NIGHT;
      if (egg >= 0 && ask_count_ - egg_last_[egg] >= EGG_COOLDOWN_ASKS) {
        int m = mode_;
        int pick = this->pick_from_([m, egg](const Answer &a) {
          return a.rarity == SECRET && a.mode == m && a.tag == egg;
        });
        if (pick >= 0) {
          egg_last_[egg] = ask_count_;
          if (egg == EGG_SPAM)
            asks_[0] = asks_[1] = asks_[2] = 0;
          return this->remember_(pick);
        }
      }
    }

    int rarity = forced_rarity;
    if (rarity < 0 || rarity > CARD) {
      if (mode_ == CLASSIC_MODE) {
        rarity = CLASSIC;
      } else if (mode_ == ROAST_MODE) {
        rarity = ROAST;
      } else {
        uint32_t r = rnd(1000);
        if (since_legendary_ >= PITY || r < 30)
          rarity = LEGENDARY;
        else if (r < 60)
          rarity = GLITCH;
        else if (r < 220)
          rarity = RARE;
        else
          rarity = COMMON;
      }
    }
    int pick = this->pick_from_([rarity](const Answer &a) { return a.rarity == rarity; });
    return this->remember_(pick >= 0 ? pick : 0);
  }
  /// A random answer matching `match`, preferring ones not shown recently; -1 if none match.
  template<typename F> int pick_from_(F match) {
    int cand[N_ANSWERS];
    int n = 0;
    for (int i = 0; i < N_ANSWERS; i++)
      if (match(ANSWERS[i]) && !this->recent_(i))
        cand[n++] = i;
    if (n == 0)
      for (int i = 0; i < N_ANSWERS; i++)
        if (match(ANSWERS[i]))
          cand[n++] = i;
    return n ? cand[rnd(n)] : -1;
  }
  bool recent_(int i) const {
    for (int h : history_)
      if (h == i)
        return true;
    return false;
  }
  int remember_(int i) {
    history_[hist_i_++ % 10] = i;
    if (mode_ == ORACLE)  // the legendary pity timer only counts Oracle asks
      since_legendary_ = ANSWERS[i].rarity == LEGENDARY ? 0 : since_legendary_ + 1;
    return i;
  }

  // ---- the tumbling die ----
  void update_die_(float dt) {
    if (die_opa_ <= 2 || die_frames_ == nullptr || die_n_ == 0) {
      die_.alpha(0);
      return;
    }
    die_frame_ += die_fps_ * dt;
    while (die_frame_ >= die_n_)
      die_frame_ -= die_n_;
    int f = (int) die_frame_;
    if (f != die_shown_) {
      die_shown_ = f;
      lv_image_set_src(die_.o, die_frames_[f]);
    }
    die_.pos((int) lroundf(die_x_), (int) lroundf(die_y_));
    die_.alpha(die_opa_);
  }

  // ---- the rolling 8 ----
  void update_disc_(float dt) {
    float tx, ty;
    if (disc_away_) {
      tx = 0;
      ty = 2.4f;  // over the bottom horizon
    } else {
      // React to changes in tilt, not absolute tilt: held upright like a phone, the 8
      // still settles in the middle; moving the ball sloshes it toward the low side.
      tx = clampf(-(lx_ - bx_) * 2.6f, -1.5f, 1.5f);
      ty = clampf(-(ly_ - by_) * 2.6f, -1.5f, 1.5f);
      float m = sqrtf(tx * tx + ty * ty);
      if (m > 1) {
        tx /= m;
        ty /= m;
      }
    }
    // Under-damped spring: the disc lags and sloshes like it floats in the ball.
    float k = disc_away_ ? 110.0f : 55.0f, c = disc_away_ ? 16.0f : 8.5f;
    dvx_ += (k * (tx - dpx_) - c * dvx_) * dt;
    dvy_ += (k * (ty - dpy_) - c * dvy_) * dt;
    dpx_ += dvx_ * dt;
    dpy_ += dvy_ * dt;

    float m = sqrtf(dpx_ * dpx_ + dpy_ * dpy_);
    float th = fminf(m * ROLL_RAD, 85.0f * (float) M_PI / 180.0f);
    float cphi = m > 1e-4f ? dpx_ / m : 1, sphi = m > 1e-4f ? dpy_ / m : 0;
    float off = BALL_R * sinf(th);
    int nx = (int) lroundf(off * cphi), ny = (int) lroundf(off * sphi);
    float f = cosf(th);
    int sx = (int) lroundf(256 * (1 - (1 - f) * cphi * cphi)) & ~1;
    int sy = (int) lroundf(256 * (1 - (1 - f) * sphi * sphi)) & ~1;
    bool moving = fabsf(dvx_) + fabsf(dvy_) > 0.03f;
    // Hysteresis: a resting disc ignores 1 px of sensor noise.
    if (disc_.x == INT32_MIN || moving || abs(nx - disc_.x) >= 2 || abs(ny - disc_.y) >= 2) {
      disc_.pos(nx, ny);
      disc_.scale(sx, sy);
    }
    // Lambert shading at the disc's centre, relative to facing the viewer.
    float ndl = sinf(th) * (cphi * LIGHT_X + sphi * LIGHT_Y) + f * LIGHT_Z;
    float lit = clampf(ndl / LIGHT_Z, 0, 1);
    disc_.darken((int) lroundf((1 - (0.4f + 0.6f * lit)) * 230) & ~3);
    float edge = clampf((80.0f - th * 180.0f / (float) M_PI) / 22.0f, 0, 1);
    disc_.alpha(fminf(255 * edge, (float) sphere_.vis()));
  }

  // ---- bubbles and sparkles ----
  void spawn_bubble_(uint32_t now, bool small) {
    for (auto &b : bub_) {
      if (b.live)
        continue;
      b.live = true;
      b.r = small ? rndf(2.5f, 5.0f) : rndf(3.0f, 9.0f);
      b.x = rndf(-115, 115);
      b.y = rndf(150, 185);
      b.vy = -(80 + b.r * 18 + rndf(0, 70));
      b.sway = rndf(3, 13);
      b.ph = rndf(0, 6.28f);
      b.born = now;
      b.obj.size(((int) lroundf(b.r * 2)) | 1);
      return;
    }
  }

  void update_bubbles_(uint32_t now, float dt) {
    float win = window_.vis() / 255.0f;
    for (auto &b : bub_) {
      if (!b.live)
        continue;
      b.y += b.vy * dt;
      float age = (float) (now - b.born) / 1000.0f;
      float x = b.x + b.sway * sinf(b.ph + age * 5.0f);
      float d = sqrtf(x * x + b.y * b.y);
      float a = clampf(age / 0.15f, 0, 1) * clampf((b.y + 190) / 70.0f, 0, 1) *
                clampf((WINDOW_R - 6 - b.r - d) / 12.0f, 0, 1) * win;
      if (b.y < -190 || win <= 0.01f) {
        b.live = false;
        b.obj.alpha(0);
        continue;
      }
      b.obj.pos((int) lroundf(x), (int) lroundf(b.y));
      b.obj.alpha(255 * a);
    }
  }

  void update_sparks_(uint32_t now) {
    bool on = rarity_ == LEGENDARY && (st_ == St::SHOW || st_ == St::RISE);
    for (auto &s : spark_) {
      if (!on) {
        s.obj.alpha(0);
        continue;
      }
      if (s.life == 0 || (int32_t) (now - s.start) > (int32_t) s.life) {
        if (s.life != 0)
          s.start = now + rnd(700);
        s.life = 700 + rnd(900);
        // Somewhere in the window but outside the triangle.
        for (int tries = 0; tries < 12; tries++) {
          float ang = rndf(0, 6.283f), rad = rndf(80, 200);
          s.x = rad * cosf(ang);
          s.y = rad * sinf(ang);
          if (!this->in_triangle_(s.x, s.y, 14))
            break;
        }
        s.obj.size(4 + (int) rnd(3) * 2);
      }
      if ((int32_t) (now - s.start) < 0) {
        s.obj.alpha(0);
        continue;
      }
      float p = (float) (now - s.start) / (float) s.life;
      float tw = sinf(p * (float) M_PI);
      s.obj.pos((int) lroundf(s.x), (int) lroundf(s.y));
      s.obj.alpha(255 * tw * tw * tri_[2].vis() / 255.0f);
    }
  }

  bool in_triangle_(float x, float y, float pad) const {
    // Point-down triangle with its centroid at the origin: top edge at y = -98, apex at y = 196.
    const float h = 294.4f, side = 340.0f;
    if (y < -h / 3 - pad || y > 2 * h / 3 + pad)
      return false;
    float half = side / 2 * (1 - (y + h / 3) / h) + pad;
    return fabsf(x) < half;
  }

  // ---- battery pill on the idle ball ----
  void update_battery_pill_(uint32_t now) {
    if (batt_.o == nullptr)
      return;
    char text[24] = "";
    uint32_t rgb = 0xFFFFFF;
    float pulse = 1;
    bool lit = bright_target_ > 0;
    auto breathe = [&](float speed) { return lit ? 0.55f + 0.45f * (0.5f + 0.5f * sinf((float) now * speed)) : 1.0f; };
    bool toast = (int32_t) (toast_until_ - now) > 0;
    if (empty_shutdown_) {
      snprintf(text, sizeof text, "BATTERY EMPTY");
      rgb = 0xFF5A4D;
    } else if (toast) {
      snprintf(text, sizeof text, "%s", toast_text_);
      rgb = toast_rgb_;
    } else if (p_known_ && p_batt_ && p_pct_ >= 0) {
      if (p_usb_) {
        if (p_state_ == 4 || p_pct_ >= 100) {
          snprintf(text, sizeof text, "CHARGED");
          rgb = 0x6DFFB0;
        } else if (p_charging_) {
          snprintf(text, sizeof text, "CHARGING %d%%", p_pct_);
          rgb = 0x6DFFB0;
          pulse = breathe(0.0028f);
        } else {
          snprintf(text, sizeof text, "%d%%", p_pct_);
        }
      } else if ((int32_t) (unplug_show_until_ - now) > 0) {
        snprintf(text, sizeof text, "%d%%", p_pct_);
      } else if (p_pct_ <= 5) {
        snprintf(text, sizeof text, "LOW BATTERY %d%%", p_pct_);
        rgb = 0xFF5A4D;
        pulse = breathe(0.006f);
      } else if (p_pct_ <= 15) {
        snprintf(text, sizeof text, "LOW BATTERY %d%%", p_pct_);
        rgb = 0xFFB424;
      }
    }
    bool show = text[0] && (st_ == St::IDLE || st_ == St::BOOT || empty_shutdown_);
    if (show && strcmp(text, batt_text_) != 0) {
      strncpy(batt_text_, text, sizeof batt_text_ - 1);
      lv_label_set_text(batt_.o, batt_text_);
    }
    if (show && rgb != batt_rgb_) {
      batt_rgb_ = rgb;
      batt_.text_color(rgb);
      lv_obj_set_style_border_color(batt_.o, lv_color_hex(rgb), 0);
    }
    float base = empty_shutdown_ ? 255.0f : (float) sphere_.vis();
    // Quantised so a breathing pill redraws a few times a second, not every frame.
    float a = show ? base * floorf(pulse * 8) / 8 : 0;
    batt_.alpha(a);
  }

  void toast_(uint32_t now, const char *text, uint32_t rgb) {
    snprintf(toast_text_, sizeof toast_text_, "%s", text);
    toast_rgb_ = rgb;
    toast_until_ = now + 2000;
  }

  // ---- power: brightness, dim, sleep, face down, scene veil and ping ----
  void activity_(uint32_t now) { last_activity_ = now; }

  void update_power_(uint32_t now, float dt) {
    uint8_t target = BRIGHT_ON;
    if (face_down_) {
      target = 0;
    } else if (st_ == St::IDLE) {
      uint32_t quiet = now - last_activity_;
      if (quiet > SLEEP_AFTER_MS)
        target = 0;
      else if (quiet > DIM_AFTER_MS)
        target = BRIGHT_DIM;
    }
    if (empty_shutdown_)
      target = BRIGHT_ON;
    bright_target_ = target;
    // Deep sleep on battery once the screen is dark: after the idle timeout, or after
    // lying face down for a minute. Plugged in, it only goes dark (and stays reachable).
    bool on_battery = p_known_ && p_batt_ && !p_usb_ && !empty_shutdown_;
    if (on_battery && !sleep_requested_ && sleep_fn_ && bright_sent_ == 0) {
      bool idle_dark = st_ == St::IDLE && now - last_activity_ > SLEEP_AFTER_MS;
      bool down_long = face_down_ && down_since_ && now - down_since_ > FACE_DOWN_SLEEP_MS;
      if (idle_dark || down_long) {
        sleep_requested_ = true;
        ESP_LOGI(TAG, "Deep sleep (%s), battery %d%%", idle_dark ? "idle" : "face down", p_pct_);
        sleep_fn_();
      }
    }
    float rate = target > bright_ ? 900.0f : (face_down_ ? 1200.0f : 160.0f);  // units per second
    if (bright_ < target)
      bright_ = fminf(bright_ + rate * dt, target);
    else if (bright_ > target)
      bright_ = fmaxf(bright_ - rate * dt, target);
    // The veil is a 0..1 multiplier for scene transitions.
    if (veil_ < veil_target_)
      veil_ = fminf(veil_ + veil_rate_ * dt, veil_target_);
    else if (veil_ > veil_target_)
      veil_ = fmaxf(veil_ - veil_rate_ * dt, veil_target_);
    float v = veil_ * veil_;  // eases the dark end, where the eye is most sensitive
    float out = bright_ * v;
    int goal = (int) lroundf(target * veil_target_);
    // The ping: a brief overdrive past the normal level when an answer lands.
    if (ping_start_ && now - ping_start_ < 450 && target == BRIGHT_ON) {
      float q = (float) (now - ping_start_) / 450.0f;
      float boost = rarity_ == LEGENDARY ? 1.0f : 0.7f;
      out += sinf(q * (float) M_PI) * (255 - BRIGHT_ON) * boost;
      goal = -1;
    }
    int b = (int) lroundf(clampf(out, 0, 255));
    if (b != bright_sent_ && (abs(b - bright_sent_) >= 4 || b == goal || b == 0 || b == BRIGHT_ON)) {
      bright_sent_ = b;
      if (set_brightness_)
        set_brightness_((uint8_t) b);
    }
  }

  // ---- state ----
  bool ready_{false};
  Obj window_, sphere_, disc_, spec_, die_, tri_[5], label_[2], pill_;
  Bubble bub_[N_BUB];
  Spark spark_[N_SPARK];
  std::function<void(uint8_t)> set_brightness_;
  std::function<int()> hour_now_;
  std::function<void()> sleep_fn_, poweroff_fn_;
  std::function<void(int, bool)> sound_fn_;
  std::function<void(bool)> sound_store_;
  bool sound_on_{true}, woke_sound_{false};
  int mode_{ORACLE};
  std::function<void(int)> mode_store_;
  const lv_image_dsc_t *disc_dsc_[2]{};
  bool card_pending_{false}, tap_pending_{false};
  uint32_t tap_at_{0};
  int ask_count_{0};
  int egg_last_[EGG_COUNT]{-1000, -1000, -1000};
  int tx0_{0}, ty0_{0}, tx_{0}, ty_{0};
  int rig_state_{RIG_NONE};
  bool rick_{false};
  VideoFns video_;
  uint32_t thud_until_{0}, kick_at_{0};
  float kick_x_{0}, kick_y_{0};
  uint32_t bubbles_next_{0}, toast_until_{0}, toast_rgb_{0xFFFFFF};
  char toast_text_[16]{};

  Obj batt_;
  char batt_text_[24]{};
  uint32_t batt_rgb_{0};
  bool p_known_{false}, p_batt_{false}, p_usb_{false}, p_charging_{false};
  int p_state_{5}, p_pct_{-1};
  uint32_t unplug_show_until_{0};
  int empty_count_{0};
  bool empty_shutdown_{false}, powered_off_{false}, sleep_requested_{false};
  uint32_t empty_at_{0};
  const lv_image_dsc_t *const *die_frames_{nullptr};
  int die_n_{0};

  float lx_{0}, ly_{0}, lz_{1}, bx_{0}, by_{0}, bz_{1};
  uint32_t last_sample_{0}, last_imu_log_{0};
  uint32_t hits_[8]{};
  uint8_t hit_i_{0};
  uint32_t last_hit_{0};
  bool shaking_{false};
  float peak_{0};
  uint32_t down_since_{0};
  bool face_down_{false};
  uint32_t last_activity_{0};
  uint32_t touch_at_{0};
  bool touch_dark_{false};

  float dpx_{0}, dpy_{0}, dvx_{0}, dvy_{0};
  bool disc_away_{false};

  bool die_on_{false};
  uint32_t die_t0_{0};
  float die_x0_{0}, die_x_{0}, die_y_{0}, die_opa_{0}, die_fps_{15}, die_frame_{0};
  int die_shown_{-1};
  float rise_die_x_{0}, rise_die_y_{0}, rise_die_opa_{0};

  St st_{St::BOOT};
  Src src_{Src::TAP};
  uint32_t t0_{0}, last_tick_{0}, last_bubble_{0}, frames_{0};
  float c_tri_{0}, c_lbl_{0}, c_pill_{0};
  int c_y_{0};
  bool from_ball_{true}, swapped_{false}, pinged_{false};
  uint32_t swap_at_{0}, ping_start_{0};
  float veil_{1}, veil_target_{1}, veil_rate_{4};
  int answer_{0}, rarity_{COMMON}, tri_idx_{0}, lbl_idx_{0};
  bool has_pill_{false};
  uint32_t text_rgb_{0xFFFFFF};
  uint32_t glitch_until_{0}, next_glitch_{0};
  uint32_t asks_[3]{};
  uint8_t ask_i_{0};
  int history_[10]{-1, -1, -1, -1, -1, -1, -1, -1, -1, -1};
  uint8_t hist_i_{0};
  int since_legendary_{0};
  int forced_rarity_{-1}, forced_index_{-1};

  float bright_{0};
  uint8_t bright_target_{BRIGHT_ON};
  int bright_sent_{-1};
};

static Engine &engine() {
  static Engine e;
  return e;
}

}  // namespace m8
