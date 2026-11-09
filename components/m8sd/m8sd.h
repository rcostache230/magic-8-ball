#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <string>

#include "esphome/core/component.h"
#include "sd_protocol_types.h"  // sdmmc_card_t (an anonymous typedef: can't be forward-declared)

namespace esphome::m8sd {

static constexpr const char *BASE = "/sd";

enum class CardState : uint8_t { MOUNTING, MOUNTED, NO_CARD, NOT_FAT32 };

/// Mounts the microSD card at /sd (from a task, so a missing or slow card never stalls boot)
/// and serves a small command protocol on the USB serial port for tools/sdcard.py:
///
///   host -> board   "#M8 INFO" | "#M8 LS <dir>" | "#M8 DEL <path>" | "#M8 FORMAT ERASE-ALL"
///                   "#M8 PUT <path> <size> <crc32 hex>" then exactly <size> raw bytes
///   board -> host   log lines containing "#M8> ..." (READY, ACK <bytes>, OK, ERR <why>, INFO, FILE)
///
/// Paths are relative to /sd. A PUT writes <path>.part, checks the CRC-32 (zlib), then
/// renames it into place, so an interrupted copy never leaves a half file under the real name.
class M8Sd : public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

  bool mounted() const { return this->state_.load() == CardState::MOUNTED; }
  CardState state() const { return this->state_.load(); }
  /// Extra USB commands ("#M8 <VERB> <arg>"), e.g. test hooks; return true if handled.
  void set_command_fn(std::function<bool(const std::string &, const std::string &)> f) {
    this->command_fn_ = std::move(f);
  }
  /// Before deep sleep: releases the card and its pins.
  void unmount();

 protected:
  static void mount_task_(void *arg);
  static void format_task_(void *arg);
  static void space_task_(void *arg);
  void refresh_space_();
  bool mount_(bool format_if_unreadable);
  void handle_line_(const std::string &line);
  void cmd_info_();
  void cmd_ls_(const std::string &dir);
  void cmd_del_(const std::string &path);
  void cmd_format_();
  void cmd_put_(const std::string &path, uint32_t size, uint32_t crc);
  bool make_parents_(const std::string &full);

  std::atomic<CardState> state_{CardState::MOUNTING};
  std::atomic<uint32_t> total_mb_{0}, free_mb_{0};
  sdmmc_card_t *card_{nullptr};
  std::string line_;
  std::function<bool(const std::string &, const std::string &)> command_fn_;
};

}  // namespace esphome::m8sd
