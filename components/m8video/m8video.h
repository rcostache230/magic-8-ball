#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <vector>

#include "esphome/components/audio/audio.h"
#include "esphome/core/component.h"
#include "lvgl.h"

namespace esphome::m8video {

enum class Prep : uint8_t { IDLE = 0, LOADING = 1, READY = 2, FAILED = 3 };

/// The rickroll player. prepare() loads in a background task while the ball churns; start()
/// shows frames (decoded on the main loop, one per due slot, between LVGL redraws) and plays
/// the audio through the media player; done() turns true at the end; stop() releases it all.
class M8Video : public Component {
 public:
  void loop() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

  /// Once LVGL is up: creates the (hidden) video image on the screen.
  void attach(lv_obj_t *screen);
  void set_audio_fns(std::function<void(audio::AudioFile *)> play, std::function<void()> stop,
                     std::function<bool()> busy) {
    this->audio_play_ = std::move(play);
    this->audio_stop_ = std::move(stop);
    this->audio_busy_ = std::move(busy);
  }

  bool available();                     // clip files are on the card
  void prepare(bool with_sound);        // async load; poll prep_state()
  int prep_state() const { return (int) this->prep_.load(); }
  void start();
  void stop();
  bool done() const { return this->phase_ == Phase::DONE; }

 protected:
  enum class Phase : uint8_t { OFF, WAIT_AUDIO, PLAYING, DONE };
  static void prepare_task_(void *arg);
  bool load_();
  void release_();
  bool show_frame_(int index);

  lv_obj_t *img_{nullptr};
  lv_image_dsc_t dsc_{};
  std::atomic<Prep> prep_{Prep::IDLE};
  Phase phase_{Phase::OFF};
  bool with_sound_{true};

  int fd_{-1};
  uint16_t w_{0}, h_{0}, fps_{15}, n_{0};
  std::vector<uint32_t> offsets_;
  uint8_t *in_buf_{nullptr};            // internal DMA, one padded JPEG
  size_t in_cap_{0};
  uint8_t *frame_{nullptr};             // PSRAM RGB565 (LE), 16-byte aligned
  void *dec_{nullptr};                  // jpeg_dec_handle_t
  uint8_t *wav_{nullptr};               // PSRAM, whole WAV file
  audio::AudioFile audio_file_{};

  uint32_t t0_{0}, wait_t0_{0};
  int shown_{-1};
  uint32_t decoded_{0}, skipped_{0}, decode_us_{0}, max_decode_us_{0};

  std::function<void(audio::AudioFile *)> audio_play_;
  std::function<void()> audio_stop_;
  std::function<bool()> audio_busy_;
};

}  // namespace esphome::m8video
