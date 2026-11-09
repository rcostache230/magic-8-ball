#pragma once

#include "esphome/core/component.h"
#include "esphome/components/i2c/i2c.h"

namespace esphome::m8power {

// AXP2101 power management IC (address 0x34). Addresses and bit meanings from XPowersLib
// (AXP2101Constants.h / XPowersAXP2101.tpp), the library Waveshare's own examples use.
static constexpr uint8_t AXP_STATUS1 = 0x00;         // b5 VBUS good, b3 battery present
static constexpr uint8_t AXP_STATUS2 = 0x01;         // b7:5 direction (1 charge, 2 discharge), b3 VBUS absent, b2:0 charger state
static constexpr uint8_t AXP_IC_TYPE = 0x03;         // 0x4A for the AXP2101
static constexpr uint8_t AXP_CHIP_ID = 0x4A;
static constexpr uint8_t AXP_COMMON_CONFIG = 0x10;   // b0: power the system off
static constexpr uint8_t AXP_INPUT_CUR_LIMIT = 0x16; // b2:0: 100/500/900/1000/1500/2000 mA
static constexpr uint8_t AXP_CHARGE_GAUGE_WDT = 0x18;  // b3 fuel gauge, b1 battery charging enable
static constexpr uint8_t AXP_VOFF_SET = 0x24;        // VSYS power-off threshold
static constexpr uint8_t AXP_ADC_CTRL = 0x30;        // b0 VBAT, b1 TS, b2 VBUS, b3 VSYS, b4 die temperature
static constexpr uint8_t AXP_VBAT_H = 0x34;          // VBAT in mV: (H & 0x1F) << 8 | L
static constexpr uint8_t AXP_VBAT_L = 0x35;
static constexpr uint8_t AXP_IPRECHG = 0x61;
static constexpr uint8_t AXP_ICC = 0x62;             // b4:0 constant-charge current code
static constexpr uint8_t AXP_ITERM = 0x63;
static constexpr uint8_t AXP_CV = 0x64;              // b2:0 charge voltage: 3 = 4.2 V
static constexpr uint8_t AXP_BAT_DET = 0x68;         // b0 battery detection
static constexpr uint8_t AXP_BAT_PERCENT = 0xA4;     // fuel gauge, 0-100 %

static constexpr uint8_t CV_4V2 = 3;

// QMI8658 IMU (address 0x6B). From SensorLib (SensorQMI8658.hpp, QMI8658Constants.h).
static constexpr uint8_t QMI_ADDR = 0x6B;
static constexpr uint8_t QMI_CTRL1 = 0x02;      // b6 address auto-increment, b4 INT2 enable
static constexpr uint8_t QMI_CTRL2 = 0x03;      // b6:4 accel range, b3:0 accel ODR
static constexpr uint8_t QMI_CTRL7 = 0x08;      // b0 accel enable, b1 gyro enable
static constexpr uint8_t QMI_CTRL9 = 0x0A;      // command register
static constexpr uint8_t QMI_CAL1_L = 0x0B;     // WoM threshold, 1 mg / LSB
static constexpr uint8_t QMI_CAL1_H = 0x0C;     // b7:6 pin + initial level, b5:0 blanking samples
static constexpr uint8_t QMI_STATUS_INT = 0x2D; // b7 CTRL9 command done
static constexpr uint8_t QMI_RST_RESULT = 0x4D; // 0x80 once a reset has finished
static constexpr uint8_t QMI_RESET = 0x60;      // write 0xB0 to reset
static constexpr uint8_t QMI_CMD_WRITE_WOM = 0x08;
static constexpr uint8_t QMI_ACC_RANGE_8G = 2;
static constexpr uint8_t QMI_STATUS1 = 0x2F;    // b2 WoM; reading it resets the WoM line to its initial level
// WoM compares consecutive samples, so the rate matters. Measured on this board while
// handling the ball: 3 Hz (code 15) detected nothing; 11 Hz at 50 mg and 128 Hz at 200 mg
// both caught every pick-up. 11 Hz runs the accelerometer at 31% duty instead of 100%.
static constexpr uint8_t QMI_ACC_ODR_LP_11HZ = 14;  // low-power mode (gyro must be off)
static constexpr uint8_t QMI_WOM_BLANKING = 2;      // samples ignored after arming

enum ChargeState : uint8_t { TRICKLE = 0, PRECHARGE = 1, CONSTANT_CURRENT = 2, CONSTANT_VOLTAGE = 3, DONE = 4, STOPPED = 5 };

class M8Power : public PollingComponent, public i2c::I2CDevice {
 public:
  void setup() override;
  void update() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::DATA; }

  void set_charge_current_code(uint8_t code) { this->icc_code_ = code; }
  void set_wake_threshold_mg(uint8_t mg) { this->wom_mg_ = mg; }

  bool ok() const { return this->ok_; }
  bool battery_present() const { return this->battery_; }
  bool usb_power() const { return this->vbus_; }
  bool charging() const { return this->charging_; }
  uint8_t charge_state() const { return this->charge_state_; }
  int battery_mv() const { return this->vbat_mv_; }
  /// 0-100, or -1 when there is no battery.
  int battery_percent() const { return this->percent_; }

  /// Puts the QMI8658 into wake-on-motion: its INT2 line (GPIO21) toggles on movement.
  /// Resets the IMU, so only call this right before deep sleep.
  bool arm_wake_on_motion() { return this->arm_wake_on_motion(this->wom_odr_, this->wom_mg_); }
  bool arm_wake_on_motion(uint8_t odr_code, uint8_t mg);
  /// Clears a pending wake-on-motion event, which puts the wake line back to its initial
  /// (high) level. Returns the line level afterwards (1 = ready to wake on the next move).
  int clear_wake_event();
  /// Cuts power to the whole board. USB or the PWR button turns it back on.
  void power_off();
  /// Diagnostic: arms wake-on-motion while staying awake and logs every change of the
  /// wake line (GPIO21) for `ms`. Afterwards the IMU must be set up again.
  void start_wom_test(uint32_t ms, uint8_t odr_code, uint8_t mg);
  /// Logs the charger registers and the battery state (for checking over Wi-Fi).
  void report() {
    this->log_regs_("now");
    this->last_log_ = 0;
    this->update();
  }

 protected:
  bool rmw_(uint8_t reg, uint8_t keep_mask, uint8_t set_bits);
  void probe_line_(const char *when);
  bool qmi_write_(uint8_t reg, uint8_t value);
  int qmi_read_(uint8_t reg);
  void log_regs_(const char *when);

  i2c::I2CDevice qmi_;
  uint8_t icc_code_{11};
  uint8_t wom_mg_{50};
  uint8_t wom_odr_{QMI_ACC_ODR_LP_11HZ};
  bool ok_{false};
  bool battery_{false}, vbus_{false}, charging_{false};
  uint8_t charge_state_{STOPPED};
  int vbat_mv_{0}, percent_{-1};
  uint32_t last_log_{0};
  uint32_t test_until_{0}, test_last_status_{0};
  int test_level_{-1};
};

}  // namespace esphome::m8power
