#include "m8power.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "driver/gpio.h"

namespace esphome::m8power {

static const char *const TAG = "m8power";

// Resting Li-ion voltage to charge, only used if the AXP2101 fuel gauge reports nonsense.
static int percent_from_mv(int mv) {
  static const int CURVE[][2] = {{3300, 0},  {3500, 5},  {3600, 10}, {3700, 25}, {3750, 40}, {3800, 50},
                                 {3850, 60}, {3900, 70}, {4000, 80}, {4100, 90}, {4180, 100}};
  if (mv <= CURVE[0][0])
    return 0;
  for (size_t i = 1; i < sizeof(CURVE) / sizeof(CURVE[0]); i++) {
    if (mv <= CURVE[i][0]) {
      int v0 = CURVE[i - 1][0], p0 = CURVE[i - 1][1], v1 = CURVE[i][0], p1 = CURVE[i][1];
      return p0 + (mv - v0) * (p1 - p0) / (v1 - v0);
    }
  }
  return 100;
}

void M8Power::setup() {
  this->qmi_.set_i2c_bus(this->bus_);
  this->qmi_.set_i2c_address(QMI_ADDR);

  uint8_t id = 0;
  if (!this->read_byte(AXP_IC_TYPE, &id) || id != AXP_CHIP_ID) {
    ESP_LOGE(TAG, "AXP2101 not found (IC type 0x%02X)", id);
    this->mark_failed();
    return;
  }
  this->log_regs_("before");
  bool good = true;
  // ADC: battery voltage, VBUS and VSYS on. TS off: the board has no battery thermistor,
  // and Waveshare's examples (through XPowersLib's begin()) turn it off too.
  good &= this->rmw_(AXP_ADC_CTRL, 0xF0, 0x0D);
  good &= this->rmw_(AXP_BAT_DET, 0xFE, 0x01);          // battery detection on
  good &= this->rmw_(AXP_CHARGE_GAUGE_WDT, 0xF5, 0x0A);  // fuel gauge on, battery charging on
  good &= this->rmw_(AXP_CV, 0xF8, CV_4V2);              // charge to 4.2 V (standard Li-ion)
  good &= this->rmw_(AXP_ICC, 0xE0, this->icc_code_);    // constant-charge current
  this->ok_ = good;
  if (!good)
    ESP_LOGW(TAG, "Some charger settings could not be written");
  this->log_regs_("after");
  this->update();
}

void M8Power::update() {
  uint8_t s1 = 0, s2 = 0;
  if (!this->read_byte(AXP_STATUS1, &s1) || !this->read_byte(AXP_STATUS2, &s2)) {
    this->status_set_warning();
    return;
  }
  this->status_clear_warning();
  bool battery = s1 & 0x08;
  bool vbus = (s1 & 0x20) && !(s2 & 0x08);
  uint8_t state = s2 & 0x07;
  bool charging = (s2 >> 5) == 0x01;
  int mv = 0, pct = -1;
  if (battery) {
    uint8_t h = 0, l = 0, p = 0xFF;
    if (this->read_byte(AXP_VBAT_H, &h) && this->read_byte(AXP_VBAT_L, &l))
      mv = ((h & 0x1F) << 8) | l;
    if (this->read_byte(AXP_BAT_PERCENT, &p) && p <= 100)
      pct = p;
    else if (mv > 0)
      pct = percent_from_mv(mv);
  }
  bool changed = battery != this->battery_ || vbus != this->vbus_ || charging != this->charging_ ||
                 state != this->charge_state_ || pct != this->percent_;
  this->battery_ = battery;
  this->vbus_ = vbus;
  this->charge_state_ = state;
  this->charging_ = charging;
  this->vbat_mv_ = mv;
  this->percent_ = pct;
  uint32_t now = millis();
  if (changed || this->last_log_ == 0 || now - this->last_log_ > 60000) {
    this->last_log_ = now;
    static const char *const STATES[] = {"trickle", "pre-charge", "constant current", "constant voltage",
                                         "done", "not charging", "?", "?"};
    ESP_LOGI(TAG, "Battery %s %d mV %d%%, USB %s, charger %s%s", battery ? "present" : "absent", mv, pct,
             vbus ? "in" : "out", STATES[state], charging ? " (charging)" : "");
  }
}

void M8Power::dump_config() {
  ESP_LOGCONFIG(TAG, "M8 power (AXP2101):");
  LOG_I2C_DEVICE(this);
  LOG_UPDATE_INTERVAL(this);
  ESP_LOGCONFIG(TAG, "  Charge: 4.2 V, current code %u; wake threshold %u mg", this->icc_code_, this->wom_mg_);
  if (this->is_failed())
    ESP_LOGE(TAG, "  Not found");
}

bool M8Power::arm_wake_on_motion(uint8_t odr_code, uint8_t mg) {
  bool good = true;
  // Reset to defaults (finishes within 15 ms), then auto-increment on.
  good &= this->qmi_write_(QMI_RESET, 0xB0);
  bool reset_done = false;
  for (int i = 0; i < 20 && !reset_done; i++) {
    delay(5);
    reset_done = this->qmi_read_(QMI_RST_RESULT) == 0x80;
  }
  good &= reset_done;
  int v = this->qmi_read_(QMI_CTRL1);
  good &= v >= 0 && this->qmi_write_(QMI_CTRL1, (uint8_t) v | 0x40);
  // Sensors off while configuring; accel 8 g at a low-power rate.
  v = this->qmi_read_(QMI_CTRL7);
  good &= v >= 0 && this->qmi_write_(QMI_CTRL7, (uint8_t) v & ~0x03);
  v = this->qmi_read_(QMI_CTRL2);
  good &= v >= 0 && this->qmi_write_(QMI_CTRL2, (uint8_t) ((v & 0x80) | (QMI_ACC_RANGE_8G << 4) | (odr_code & 0x0F)));
  // Threshold, then INT2 with initial level high and a short blanking period. The initial
  // level only takes effect after blanking; each WoM event then toggles the line.
  good &= this->qmi_write_(QMI_CAL1_L, mg);
  good &= this->qmi_write_(QMI_CAL1_H, (0x03 << 6) | QMI_WOM_BLANKING);
  // CTRL9 handshake: issue the command, wait for done, acknowledge, wait for clear.
  good &= this->qmi_write_(QMI_CTRL9, QMI_CMD_WRITE_WOM);
  bool done = false;
  for (int i = 0; i < 100 && !done; i++) {
    delay(1);
    int s = this->qmi_read_(QMI_STATUS_INT);
    done = s >= 0 && (s & 0x80);
  }
  good &= done;
  good &= this->qmi_write_(QMI_CTRL9, 0x00);
  bool cleared = false;
  for (int i = 0; i < 100 && !cleared; i++) {
    delay(1);
    int s = this->qmi_read_(QMI_STATUS_INT);
    cleared = s >= 0 && !(s & 0x80);
  }
  good &= cleared;
  // Accelerometer on, INT2 output on.
  v = this->qmi_read_(QMI_CTRL7);
  good &= v >= 0 && this->qmi_write_(QMI_CTRL7, (uint8_t) v | 0x01);
  v = this->qmi_read_(QMI_CTRL1);
  good &= v >= 0 && this->qmi_write_(QMI_CTRL1, (uint8_t) v | 0x10);
  ESP_LOGI(TAG, "Wake-on-motion %s (ODR code %u, threshold %u mg)", good ? "armed" : "FAILED", odr_code, mg);
  return good;
}

static constexpr gpio_num_t WAKE_GPIO = GPIO_NUM_21;

void M8Power::probe_line_(const char *when) {
  gpio_set_direction(WAKE_GPIO, GPIO_MODE_INPUT);
  gpio_set_pull_mode(WAKE_GPIO, GPIO_PULLDOWN_ONLY);
  delay(3);
  int low_pull = gpio_get_level(WAKE_GPIO);
  gpio_set_pull_mode(WAKE_GPIO, GPIO_PULLUP_ONLY);
  delay(3);
  int high_pull = gpio_get_level(WAKE_GPIO);
  const char *verdict = low_pull == high_pull ? (low_pull ? "driven HIGH" : "driven LOW") : "NOT driven (floats)";
  ESP_LOGI(TAG, "Wake line %s: pull-down reads %d, pull-up reads %d -> %s", when, low_pull, high_pull, verdict);
}

void M8Power::start_wom_test(uint32_t ms, uint8_t odr_code, uint8_t mg) {
  this->probe_line_("before arming");
  bool ok = this->arm_wake_on_motion(odr_code, mg);
  this->probe_line_("after arming");
  // Leave the pull-up on and watch.
  this->test_level_ = gpio_get_level(WAKE_GPIO);
  this->test_until_ = millis() + ms;
  this->test_last_status_ = 0;
  ESP_LOGI(TAG, "WoM test %s: move the ball now (%u s)", ok ? "running" : "running, but arming FAILED",
           (unsigned) (ms / 1000));
}

void M8Power::loop() {
  if (this->test_until_ == 0)
    return;
  uint32_t now = millis();
  int level = gpio_get_level(WAKE_GPIO);
  bool changed = level != this->test_level_;
  if (changed || now - this->test_last_status_ > 2000) {
    this->test_last_status_ = now;
    // TIMESTAMP (0x30-0x32) counts samples: it shows whether the accelerometer is running.
    uint32_t ts = (this->qmi_read_(0x30) & 0xFF) | (this->qmi_read_(0x31) & 0xFF) << 8 | (this->qmi_read_(0x32) & 0xFF) << 16;
    ESP_LOGI(TAG, "WoM test: line %d%s, samples %u, STATUS1=%02X CTRL2=%02X CTRL7=%02X CAL1_L=%u CAL1_H=%02X", level,
             changed ? " (CHANGED)" : "", (unsigned) ts, this->qmi_read_(0x2F) & 0xFF, this->qmi_read_(QMI_CTRL2) & 0xFF,
             this->qmi_read_(QMI_CTRL7) & 0xFF, this->qmi_read_(QMI_CAL1_L) & 0xFF, this->qmi_read_(QMI_CAL1_H) & 0xFF);
    this->test_level_ = level;
  }
  if ((int32_t) (now - this->test_until_) > 0) {
    this->test_until_ = 0;
    ESP_LOGI(TAG, "WoM test finished");
  }
}

int M8Power::clear_wake_event() {
  gpio_set_direction(WAKE_GPIO, GPIO_MODE_INPUT);
  int status = this->qmi_read_(QMI_STATUS1);
  delay(2);
  int level = gpio_get_level(WAKE_GPIO);
  ESP_LOGD(TAG, "Cleared WoM (STATUS1=%02X), wake line %d", status & 0xFF, level);
  return level;
}

void M8Power::power_off() {
  ESP_LOGW(TAG, "Powering off (battery empty)");
  delay(50);
  this->rmw_(AXP_COMMON_CONFIG, 0xFE, 0x01);
}

bool M8Power::rmw_(uint8_t reg, uint8_t keep_mask, uint8_t set_bits) {
  uint8_t v = 0;
  if (!this->read_byte(reg, &v))
    return false;
  return this->write_byte(reg, (uint8_t) ((v & keep_mask) | set_bits));
}

bool M8Power::qmi_write_(uint8_t reg, uint8_t value) { return this->qmi_.write_byte(reg, value); }

int M8Power::qmi_read_(uint8_t reg) {
  uint8_t v = 0;
  return this->qmi_.read_byte(reg, &v) ? v : -1;
}

void M8Power::log_regs_(const char *when) {
  uint8_t r[9] = {};
  const uint8_t regs[9] = {AXP_INPUT_CUR_LIMIT, AXP_CHARGE_GAUGE_WDT, AXP_VOFF_SET, AXP_ADC_CTRL, AXP_IPRECHG,
                           AXP_ICC,             AXP_ITERM,            AXP_CV,       AXP_BAT_DET};
  for (int i = 0; i < 9; i++)
    this->read_byte(regs[i], &r[i]);
  ESP_LOGI(TAG,
           "Registers %s: in-limit[16]=%02X gauge/chg[18]=%02X voff[24]=%02X adc[30]=%02X prechg[61]=%02X "
           "icc[62]=%02X iterm[63]=%02X cv[64]=%02X batdet[68]=%02X",
           when, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]);
}

}  // namespace esphome::m8power
