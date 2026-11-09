#include "m8video.h"

#include <cstring>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include "esp_heap_caps.h"
#include "esp_jpeg_dec.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

namespace esphome::m8video {

static const char *const TAG = "m8video";
static const char *const VIDEO_PATH = "/sd/m8/rickroll.m8v";
static const char *const AUDIO_PATH = "/sd/m8/rickroll.wav";
static constexpr size_t BOUNCE = 16384;          // internal DMA chunk for loading the WAV
static constexpr uint32_t AUDIO_WAIT_MS = 800;   // start the picture anyway if audio hasn't begun

void M8Video::attach(lv_obj_t *screen) {
  this->img_ = lv_image_create(screen);
  // Opaque background: lets LVGL treat the video as covering what's behind it, so each frame
  // redraws only the video, not the ball underneath.
  lv_obj_set_style_bg_color(this->img_, lv_color_black(), 0);
  lv_obj_set_style_bg_opa(this->img_, LV_OPA_COVER, 0);
  lv_obj_add_flag(this->img_, LV_OBJ_FLAG_HIDDEN);
  lv_obj_remove_flag(this->img_, LV_OBJ_FLAG_CLICKABLE);
}

bool M8Video::available() {
  struct stat st {};
  return stat(VIDEO_PATH, &st) == 0 && st.st_size > 64;
}

void M8Video::prepare(bool with_sound) {
  if (this->prep_.load() == Prep::LOADING)
    return;
  if (this->wav_ != nullptr) {  // the last clip's audio buffer
    if (this->audio_busy_ && this->audio_busy_()) {
      this->prep_.store(Prep::FAILED);  // still playing: skip the rickroll this time
      return;
    }
    heap_caps_free(this->wav_);
    this->wav_ = nullptr;
  }
  this->release_();
  this->with_sound_ = with_sound;
  this->prep_.store(Prep::LOADING);
  xTaskCreatePinnedToCore(M8Video::prepare_task_, "m8video_load", 6144, this, 4, nullptr, 0);
}

void M8Video::prepare_task_(void *arg) {
  auto *self = static_cast<M8Video *>(arg);
  bool ok = self->load_();
  self->prep_.store(ok ? Prep::READY : Prep::FAILED);
  vTaskDelete(nullptr);
}

bool M8Video::load_() {
  uint32_t t0 = millis();
  this->fd_ = open(VIDEO_PATH, O_RDONLY);
  if (this->fd_ < 0) {
    ESP_LOGW(TAG, "No clip at %s", VIDEO_PATH);
    return false;
  }
  uint8_t hdr[12];
  if (read(this->fd_, hdr, 12) != 12 || memcmp(hdr, "M8V1", 4) != 0) {
    ESP_LOGW(TAG, "Not an M8V1 file");
    return false;
  }
  this->w_ = hdr[4] | hdr[5] << 8;
  this->h_ = hdr[6] | hdr[7] << 8;
  this->fps_ = hdr[8] | hdr[9] << 8;
  this->n_ = hdr[10] | hdr[11] << 8;
  if (this->w_ == 0 || this->h_ == 0 || this->fps_ == 0 || this->n_ == 0 || this->w_ > 466 || this->h_ > 466) {
    ESP_LOGW(TAG, "Bad clip header %ux%u %u fps %u frames", this->w_, this->h_, this->fps_, this->n_);
    return false;
  }
  this->offsets_.resize(this->n_ + 1);
  size_t table = 4 * (this->n_ + 1);
  if (read(this->fd_, this->offsets_.data(), table) != (ssize_t) table)
    return false;
  uint32_t biggest = 0;
  for (int i = 0; i < this->n_; i++)
    biggest = std::max(biggest, this->offsets_[i + 1] - this->offsets_[i]);
  this->in_cap_ = std::max<size_t>((biggest + 511) / 512 * 512, BOUNCE);
  this->in_buf_ = (uint8_t *) heap_caps_aligned_alloc(4, this->in_cap_, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
  size_t frame_bytes = (size_t) this->w_ * this->h_ * 2;
  this->frame_ = (uint8_t *) heap_caps_aligned_alloc(16, frame_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (this->in_buf_ == nullptr || this->frame_ == nullptr) {
    ESP_LOGW(TAG, "Out of memory (in %u B, frame %u B)", (unsigned) this->in_cap_, (unsigned) frame_bytes);
    return false;
  }
  memset(this->frame_, 0, frame_bytes);

  jpeg_dec_config_t cfg = DEFAULT_JPEG_DEC_CONFIG();
  cfg.output_type = JPEG_PIXEL_FORMAT_RGB565_LE;  // LVGL RGB565 images are little-endian here
  jpeg_dec_handle_t dec = nullptr;
  if (jpeg_dec_open(&cfg, &dec) != JPEG_ERR_OK || dec == nullptr) {
    ESP_LOGW(TAG, "JPEG decoder didn't open");
    return false;
  }
  this->dec_ = dec;

  // The whole WAV into PSRAM for the media player, via an internal DMA bounce buffer
  // (SD reads straight into PSRAM go one sector at a time).
  if (this->with_sound_) {
    int afd = open(AUDIO_PATH, O_RDONLY);
    struct stat st {};
    if (afd >= 0 && fstat(afd, &st) == 0 && st.st_size > 44) {
      this->wav_ = (uint8_t *) heap_caps_malloc(st.st_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
      size_t got = 0;
      while (this->wav_ != nullptr && got < (size_t) st.st_size) {
        ssize_t r = read(afd, this->in_buf_, std::min(this->in_cap_, (size_t) st.st_size - got));
        if (r <= 0)
          break;
        memcpy(this->wav_ + got, this->in_buf_, r);
        got += r;
      }
      if (this->wav_ != nullptr && got == (size_t) st.st_size) {
        this->audio_file_.data = this->wav_;
        this->audio_file_.length = got;
        this->audio_file_.file_type = audio::AudioFileType::WAV;
      } else {
        ESP_LOGW(TAG, "Couldn't load the audio (largest PSRAM block %u B): playing silently",
                 (unsigned) heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM));
        heap_caps_free(this->wav_);
        this->wav_ = nullptr;
      }
    }
    if (afd >= 0)
      close(afd);
  }
  ESP_LOGI(TAG, "Clip ready in %u ms: %ux%u, %u fps, %u frames, audio %s; PSRAM free %u", (unsigned) (millis() - t0),
           this->w_, this->h_, this->fps_, this->n_, this->wav_ ? "loaded" : "none",
           (unsigned) heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
  return true;
}

void M8Video::start() {
  if (this->prep_.load() != Prep::READY || this->img_ == nullptr)
    return;
  memset(&this->dsc_, 0, sizeof this->dsc_);
  this->dsc_.header.magic = LV_IMAGE_HEADER_MAGIC;
  this->dsc_.header.cf = LV_COLOR_FORMAT_RGB565;
  this->dsc_.header.w = this->w_;
  this->dsc_.header.h = this->h_;
  this->dsc_.header.stride = this->w_ * 2;
  this->dsc_.data_size = (uint32_t) this->w_ * this->h_ * 2;
  this->dsc_.data = this->frame_;
  lv_image_set_src(this->img_, &this->dsc_);
  // Centred on even coordinates: the CO5300 redraws in 2-pixel steps, and an odd position
  // would widen every frame's dirty area past the image.
  lv_obj_set_pos(this->img_, ((466 - this->w_) / 2) & ~1, ((466 - this->h_) / 2) & ~1);
  lv_obj_move_to_index(this->img_, -1);
  this->shown_ = -1;
  this->decoded_ = this->skipped_ = this->decode_us_ = this->max_decode_us_ = 0;
  this->show_frame_(0);
  lv_obj_remove_flag(this->img_, LV_OBJ_FLAG_HIDDEN);
  if (this->wav_ != nullptr && this->audio_play_) {
    this->audio_play_(&this->audio_file_);
    this->phase_ = Phase::WAIT_AUDIO;
    this->wait_t0_ = millis();
  } else {
    this->phase_ = Phase::PLAYING;
    this->t0_ = millis();
  }
}

bool M8Video::show_frame_(int index) {
  uint32_t off = this->offsets_[index], len = this->offsets_[index + 1] - off;
  if (len > this->in_cap_ || pread(this->fd_, this->in_buf_, len, off) != (ssize_t) len) {
    ESP_LOGW(TAG, "Read failed at frame %d", index);
    return false;
  }
  int64_t t = esp_timer_get_time();
  jpeg_dec_io_t io{};
  io.inbuf = this->in_buf_;
  io.inbuf_len = (int) len;
  jpeg_dec_header_info_t info{};
  auto *dec = (jpeg_dec_handle_t) this->dec_;
  if (jpeg_dec_parse_header(dec, &io, &info) != JPEG_ERR_OK || info.width != this->w_ || info.height != this->h_) {
    ESP_LOGW(TAG, "Bad JPEG at frame %d", index);
    return false;
  }
  io.outbuf = this->frame_;
  if (jpeg_dec_process(dec, &io) != JPEG_ERR_OK) {
    ESP_LOGW(TAG, "Decode failed at frame %d", index);
    return false;
  }
  uint32_t us = (uint32_t) (esp_timer_get_time() - t);
  this->decode_us_ += us;
  this->max_decode_us_ = std::max(this->max_decode_us_, us);
  this->decoded_++;
  this->shown_ = index;
  lv_obj_invalidate(this->img_);
  return true;
}

void M8Video::loop() {
  // Free the WAV only once the media player has let go of it.
  if (this->phase_ == Phase::OFF && this->wav_ != nullptr && this->prep_.load() != Prep::LOADING &&
      !(this->audio_busy_ && this->audio_busy_())) {
    heap_caps_free(this->wav_);
    this->wav_ = nullptr;
  }
  if (this->phase_ == Phase::WAIT_AUDIO) {
    if ((this->audio_busy_ && this->audio_busy_()) || millis() - this->wait_t0_ > AUDIO_WAIT_MS) {
      this->phase_ = Phase::PLAYING;
      this->t0_ = millis();
    }
    return;
  }
  if (this->phase_ != Phase::PLAYING)
    return;
  int due = (int) ((uint64_t) (millis() - this->t0_) * this->fps_ / 1000);
  if (due >= this->n_) {
    ESP_LOGI(TAG, "Clip done: %u frames shown, %u skipped, decode avg %u ms, max %u ms", (unsigned) this->decoded_,
             (unsigned) this->skipped_, (unsigned) (this->decoded_ ? this->decode_us_ / this->decoded_ / 1000 : 0),
             (unsigned) (this->max_decode_us_ / 1000));
    this->phase_ = Phase::DONE;
    return;
  }
  if (due > this->shown_) {
    this->skipped_ += due - this->shown_ - 1;  // running late: jump straight to the due frame
    if (!this->show_frame_(due))
      this->phase_ = Phase::DONE;
  }
}

void M8Video::stop() {
  bool audio = this->phase_ == Phase::WAIT_AUDIO || this->phase_ == Phase::PLAYING;
  if (audio && this->audio_stop_)
    this->audio_stop_();
  this->phase_ = Phase::OFF;
  if (this->img_ != nullptr)
    lv_obj_add_flag(this->img_, LV_OBJ_FLAG_HIDDEN);
  this->release_();
}

void M8Video::release_() {
  if (this->prep_.load() == Prep::LOADING)
    return;  // the load task owns everything until it finishes
  if (this->fd_ >= 0)
    close(this->fd_);
  this->fd_ = -1;
  if (this->dec_ != nullptr)
    jpeg_dec_close((jpeg_dec_handle_t) this->dec_);
  this->dec_ = nullptr;
  heap_caps_free(this->in_buf_);
  this->in_buf_ = nullptr;
  heap_caps_free(this->frame_);
  this->frame_ = nullptr;
  // wav_ is freed in loop() once the media player is idle.
  this->prep_.store(Prep::IDLE);
}

}  // namespace esphome::m8video
