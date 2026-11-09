"""Plays a short video clip from the SD card (the Roast-mode rickroll).

Frames: m8/rickroll.m8v (JPEGs, see tools/make_video.py), decoded with Espressif's SIMD
esp_new_jpeg into one PSRAM frame shown through an LVGL image. Audio: m8/rickroll.wav,
loaded into PSRAM and played through the existing speaker media player.
"""
import esphome.codegen as cg
from esphome.components.esp32 import add_idf_component
import esphome.config_validation as cv
from esphome.const import CONF_ID

DEPENDENCIES = ["esp32", "m8sd"]

m8video_ns = cg.esphome_ns.namespace("m8video")
M8Video = m8video_ns.class_("M8Video", cg.Component)

CONFIG_SCHEMA = cv.Schema({cv.GenerateID(): cv.declare_id(M8Video)}).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    add_idf_component(name="espressif/esp_new_jpeg", ref="1.0.2")
