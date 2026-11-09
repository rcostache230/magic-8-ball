"""microSD card for the Magic 8 Ball: mounts it at /sd and lets tools/sdcard.py manage it
over the USB cable (info, format, list, copy files in, delete), so the card never has to
leave the board.

Wiring (Waveshare HARDWARE_REFERENCE.md): SDMMC 1-bit, CMD GPIO1, CLK GPIO2, D0 GPIO3,
D3/CS GPIO41 (held high so the card stays in SD mode). The card must be FAT32 (MBR):
ESP-IDF's FatFs has exFAT compiled out; tools/sdcard.py can format it from the board.
"""
import esphome.codegen as cg
from esphome.components import esp32
import esphome.config_validation as cv
from esphome.const import CONF_ID

DEPENDENCIES = ["esp32"]

m8sd_ns = cg.esphome_ns.namespace("m8sd")
M8Sd = m8sd_ns.class_("M8Sd", cg.Component)

CONFIG_SCHEMA = cv.Schema({cv.GenerateID(): cv.declare_id(M8Sd)}).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    # ESPHome leaves FatFs out by default. fatfs REQUIRES wear_levelling; long file names
    # (require_fatfs) and directory calls like mkdir/rename/opendir (require_vfs_dir) too.
    esp32.include_builtin_idf_component("fatfs")
    esp32.include_builtin_idf_component("wear_levelling")
    esp32.require_fatfs()
    esp32.require_vfs_dir()
