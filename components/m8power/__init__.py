"""Battery and power management for the Magic 8 Ball (AXP2101 PMU + QMI8658 wake-on-motion).

ESPHome has no AXP2101 driver, so this small component sets up the charger, reads the
battery, arms the IMU's wake-on-motion before deep sleep, and can power the board off.
Register details come from XPowersLib and SensorLib as shipped in Waveshare's
ESP32-S3-Touch-AMOLED-1.75 repository (see README.md).
"""
import esphome.codegen as cg
from esphome.components import i2c
import esphome.config_validation as cv
from esphome.const import CONF_ID

DEPENDENCIES = ["i2c"]

CONF_CHARGE_CURRENT = "charge_current"
CONF_WOM_THRESHOLD = "wake_threshold"

m8power_ns = cg.esphome_ns.namespace("m8power")
M8Power = m8power_ns.class_("M8Power", cg.PollingComponent, i2c.I2CDevice)

# AXP2101 constant-charge current codes (register 0x62, bits 4:0).
CHARGE_CURRENTS = {
    100: 4, 125: 5, 150: 6, 175: 7, 200: 8, 300: 9, 400: 10,
    500: 11, 600: 12, 700: 13, 800: 14, 900: 15, 1000: 16,
}


CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(M8Power),
            # in mA
            cv.Optional(CONF_CHARGE_CURRENT, default=500): cv.one_of(*CHARGE_CURRENTS, int=True),
            cv.Optional(CONF_WOM_THRESHOLD, default=50): cv.int_range(min=20, max=255),
        }
    )
    .extend(cv.polling_component_schema("2s"))
    .extend(i2c.i2c_device_schema(0x34))
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await i2c.register_i2c_device(var, config)
    cg.add(var.set_charge_current_code(CHARGE_CURRENTS[config[CONF_CHARGE_CURRENT]]))
    cg.add(var.set_wake_threshold_mg(config[CONF_WOM_THRESHOLD]))
