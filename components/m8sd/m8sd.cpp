#include "m8sd.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include "driver/gpio.h"
#include "driver/sdmmc_host.h"
#include "driver/usb_serial_jtag.h"
#include "esp_heap_caps.h"
#include "esp_vfs_fat.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdmmc_cmd.h"

#include "esphome/core/application.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"

namespace esphome::m8sd {

static const char *const TAG = "m8sd";

static constexpr gpio_num_t PIN_CMD = GPIO_NUM_1;
static constexpr gpio_num_t PIN_CLK = GPIO_NUM_2;
static constexpr gpio_num_t PIN_D0 = GPIO_NUM_3;
static constexpr gpio_num_t PIN_D3 = GPIO_NUM_41;  // card D3/CS: must be high for SD mode
static constexpr size_t PUT_CHUNK = 16384;         // SD write size (a multiple of 512)
// ESP-IDF's USB-Serial-JTAG driver empties the hardware FIFO into a 512-byte ring (ESPHome's
// logger installs it that size) and drops whatever doesn't fit, so USB flow control can't help.
// The host therefore keeps < 512 bytes in flight, and we acknowledge often and cheaply.
static constexpr uint32_t PUT_ACK_EVERY = 128;
static constexpr uint32_t PUT_IDLE_TIMEOUT_MS = 4000;

// CRC-32 as zlib / Python's zlib.crc32 (reflected, poly 0xEDB88320).
static uint32_t crc32_update(uint32_t crc, const uint8_t *p, size_t n) {
  static uint32_t table[256];
  static bool ready = false;
  if (!ready) {
    for (uint32_t i = 0; i < 256; i++) {
      uint32_t c = i;
      for (int k = 0; k < 8; k++)
        c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
      table[i] = c;
    }
    ready = true;
  }
  crc = ~crc;
  while (n--)
    crc = table[(crc ^ *p++) & 0xFF] ^ (crc >> 8);
  return ~crc;
}

// Reject anything that could leave /sd.
static bool safe_path(const std::string &p) {
  return !p.empty() && p.size() < 200 && p[0] != '/' && p.find("..") == std::string::npos &&
         p.find('\\') == std::string::npos;
}

void M8Sd::setup() {
  // Mount off the main loop: card initialisation can sit in command timeouts.
  xTaskCreatePinnedToCore(M8Sd::mount_task_, "m8sd_mount", 4096, this, 5, nullptr, 0);
}

void M8Sd::mount_task_(void *arg) {
  auto *self = static_cast<M8Sd *>(arg);
  self->mount_(false);
  vTaskDelete(nullptr);
}

bool M8Sd::mount_(bool format_if_unreadable) {
  this->state_.store(CardState::MOUNTING);
  // In 1-bit mode the IDF driver never drives D3, and a card that sees D3 low at CMD0
  // drops into SPI mode, so hold it high ourselves.
  gpio_config_t d3{};
  d3.pin_bit_mask = 1ULL << PIN_D3;
  d3.mode = GPIO_MODE_OUTPUT;
  gpio_config(&d3);
  gpio_set_level(PIN_D3, 1);

  sdmmc_host_t host = SDMMC_HOST_DEFAULT();
  host.max_freq_khz = SDMMC_FREQ_DEFAULT;  // 20 MHz: GPIO-matrix routed pins
  host.flags &= ~SDMMC_HOST_FLAG_DDR;
  // One internal DMA buffer for unaligned / partial-sector transfers, instead of the driver
  // allocating and freeing one per call (fragmenting the internal heap Wi-Fi and LVGL use).
  static void *dma_buf = heap_caps_malloc(16 * 512, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
  host.dma_aligned_buffer = dma_buf;
  sdmmc_slot_config_t slot = SDMMC_SLOT_CONFIG_DEFAULT();
  slot.width = 1;
  slot.clk = PIN_CLK;
  slot.cmd = PIN_CMD;
  slot.d0 = PIN_D0;
  // The S3 defaults point d1..d3 at pins this board uses for I2C / QSPI / display CS.
  slot.d1 = GPIO_NUM_NC;
  slot.d2 = GPIO_NUM_NC;
  slot.d3 = GPIO_NUM_NC;
  slot.d4 = GPIO_NUM_NC;
  slot.d5 = GPIO_NUM_NC;
  slot.d6 = GPIO_NUM_NC;
  slot.d7 = GPIO_NUM_NC;
  slot.cd = SDMMC_SLOT_NO_CD;
  slot.wp = SDMMC_SLOT_NO_WP;
  slot.flags |= SDMMC_SLOT_FLAG_INTERNAL_PULLUP;

  esp_vfs_fat_mount_config_t cfg = VFS_FAT_MOUNT_DEFAULT_CONFIG();
  // Only ever true on an explicit "#M8 FORMAT ERASE-ALL": a fresh 64 GB card is exFAT, which
  // FatFs can't read, and this flag would silently wipe it.
  cfg.format_if_mount_failed = format_if_unreadable;
  cfg.max_files = 4;
  cfg.allocation_unit_size = 32 * 1024;

  esp_err_t err = esp_vfs_fat_sdmmc_mount(BASE, &host, &slot, &cfg, &this->card_);
  if (err == ESP_OK) {
    this->state_.store(CardState::MOUNTED);
    ESP_LOGI(TAG, "Card mounted at %s: %s, %llu MB", BASE, this->card_->cid.name,
             (unsigned long long) this->card_->csd.capacity * this->card_->csd.sector_size / (1024 * 1024));
    this->refresh_space_();  // can take seconds on a big FAT32 card: we're in a task here
    return true;
  }
  this->card_ = nullptr;
  // ESP_FAIL = the card answered but there is no FAT filesystem we can read (exFAT, GPT, raw).
  this->state_.store(err == ESP_FAIL ? CardState::NOT_FAT32 : CardState::NO_CARD);
  ESP_LOGW(TAG, "%s (%s)",
           err == ESP_FAIL ? "Card isn't FAT32 (format it with tools/sdcard.py format)" : "No card, or it didn't answer",
           esp_err_to_name(err));
  return false;
}

// Free space: FatFs may scan the whole FAT (8 MB on a 64 GB card over 1-bit SD), which takes
// seconds and tripped the main loop's watchdog. Only ever run this from a task.
void M8Sd::refresh_space_() {
  uint64_t total = 0, free_b = 0;
  uint32_t t0 = millis();
  if (esp_vfs_fat_info(BASE, &total, &free_b) == ESP_OK) {
    this->total_mb_.store((uint32_t) (total >> 20));
    this->free_mb_.store((uint32_t) (free_b >> 20));
  }
  ESP_LOGI(TAG, "Free space %u of %u MB (took %u ms)", (unsigned) this->free_mb_.load(),
           (unsigned) this->total_mb_.load(), (unsigned) (millis() - t0));
}

void M8Sd::space_task_(void *arg) {
  static_cast<M8Sd *>(arg)->refresh_space_();
  vTaskDelete(nullptr);
}

void M8Sd::unmount() {
  if (this->state_.load() == CardState::MOUNTED && this->card_ != nullptr)
    esp_vfs_fat_sdcard_unmount(BASE, this->card_);
  this->card_ = nullptr;
  this->state_.store(CardState::NO_CARD);
  gpio_reset_pin(PIN_D3);
}

void M8Sd::dump_config() {
  ESP_LOGCONFIG(TAG, "microSD (SDMMC 1-bit, CMD 1, CLK 2, D0 3, D3 41) at %s", BASE);
}

// ---- USB command protocol --------------------------------------------------------------
void M8Sd::loop() {
  uint8_t buf[64];
  int n = usb_serial_jtag_read_bytes(buf, sizeof buf, 0);
  for (int i = 0; i < n; i++) {
    char c = (char) buf[i];
    if (c == '\r')
      continue;
    if (c == '\n') {
      if (this->line_.rfind("#M8 ", 0) == 0)
        this->handle_line_(this->line_.substr(4));
      this->line_.clear();
      continue;
    }
    if (this->line_.size() < 256)
      this->line_.push_back(c);
  }
}

void M8Sd::handle_line_(const std::string &line) {
  char cmd[16] = {0}, a[208] = {0};
  unsigned size = 0, crc = 0;
  int got = sscanf(line.c_str(), "%15s %207s %u %x", cmd, a, &size, &crc);
  std::string verb(cmd);
  if (verb == "INFO") {
    this->cmd_info_();
  } else if (verb == "LS") {
    this->cmd_ls_(got >= 2 ? a : "");
  } else if (verb == "DEL" && got >= 2) {
    this->cmd_del_(a);
  } else if (verb == "FORMAT" && got >= 2 && strcmp(a, "ERASE-ALL") == 0) {
    this->cmd_format_();
  } else if (verb == "PUT" && got == 4) {
    this->cmd_put_(a, size, crc);
  } else if (this->command_fn_ && this->command_fn_(verb, got >= 2 ? a : "")) {
    ESP_LOGI(TAG, "#M8> OK %s", verb.c_str());
  } else {
    ESP_LOGW(TAG, "#M8> ERR unknown command: %s", line.c_str());
  }
}

void M8Sd::cmd_info_() {
  static const char *const NAMES[] = {"mounting", "mounted", "no-card", "not-fat32"};
  CardState s = this->state_.load();
  ESP_LOGI(TAG, "#M8> INFO state=%s total_mb=%u free_mb=%u free_internal=%u free_psram=%u", NAMES[(int) s],
           (unsigned) this->total_mb_.load(), (unsigned) this->free_mb_.load(),
           (unsigned) heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
           (unsigned) heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}

void M8Sd::cmd_ls_(const std::string &dir) {
  if (!this->mounted() || (!dir.empty() && !safe_path(dir))) {
    ESP_LOGW(TAG, "#M8> ERR not mounted or bad path");
    return;
  }
  std::string full = std::string(BASE) + (dir.empty() ? "" : "/" + dir);
  DIR *d = opendir(full.c_str());
  if (d == nullptr) {
    ESP_LOGW(TAG, "#M8> ERR can't open %s (errno %d)", full.c_str(), errno);
    return;
  }
  struct dirent *e;
  while ((e = readdir(d)) != nullptr) {
    std::string p = full + "/" + e->d_name;
    struct stat st {};
    stat(p.c_str(), &st);
    ESP_LOGI(TAG, "#M8> FILE %s %ld %s", e->d_type == DT_DIR ? "d" : "f", (long) st.st_size, e->d_name);
    App.feed_wdt();
  }
  closedir(d);
  ESP_LOGI(TAG, "#M8> OK");
}

void M8Sd::cmd_del_(const std::string &path) {
  if (!this->mounted() || !safe_path(path)) {
    ESP_LOGW(TAG, "#M8> ERR not mounted or bad path");
    return;
  }
  std::string full = std::string(BASE) + "/" + path;
  if (unlink(full.c_str()) == 0) {
    xTaskCreatePinnedToCore(M8Sd::space_task_, "m8sd_space", 4096, this, 3, nullptr, 0);
    ESP_LOGI(TAG, "#M8> OK");
  }
  else
    ESP_LOGW(TAG, "#M8> ERR delete failed (errno %d)", errno);
}

void M8Sd::cmd_format_() {
  // Writing the FAT tables of a 64 GB card takes a while: do it in a task, not the main loop.
  if (this->state_.load() == CardState::MOUNTING) {
    ESP_LOGW(TAG, "#M8> ERR busy");
    return;
  }
  ESP_LOGW(TAG, "Formatting the card as FAT32 (erases everything on it)...");
  this->state_.store(CardState::MOUNTING);
  xTaskCreatePinnedToCore(M8Sd::format_task_, "m8sd_format", 6144, this, 5, nullptr, 0);
}

void M8Sd::format_task_(void *arg) {
  auto *self = static_cast<M8Sd *>(arg);
  bool ok;
  if (self->card_ != nullptr) {  // mounted FAT card: reformat in place
    esp_vfs_fat_mount_config_t cfg = VFS_FAT_MOUNT_DEFAULT_CONFIG();
    cfg.allocation_unit_size = 32 * 1024;
    ok = esp_vfs_fat_sdcard_format_cfg(BASE, self->card_, &cfg) == ESP_OK;
    self->state_.store(ok ? CardState::MOUNTED : CardState::NOT_FAT32);
    if (ok)
      self->refresh_space_();
  } else {                       // unreadable (exFAT/GPT): formatted while mounting
    ok = self->mount_(true);
  }
  if (ok)
    ESP_LOGI(TAG, "#M8> OK formatted");
  else
    ESP_LOGW(TAG, "#M8> ERR format failed");
  vTaskDelete(nullptr);
}

bool M8Sd::make_parents_(const std::string &full) {
  size_t pos = strlen(BASE) + 1;
  while ((pos = full.find('/', pos)) != std::string::npos) {
    std::string dir = full.substr(0, pos);
    struct stat st {};
    if (stat(dir.c_str(), &st) != 0 && mkdir(dir.c_str(), 0775) != 0 && errno != EEXIST)
      return false;
    pos++;
  }
  return true;
}

void M8Sd::cmd_put_(const std::string &path, uint32_t size, uint32_t crc_expected) {
  if (!this->mounted() || !safe_path(path)) {
    ESP_LOGW(TAG, "#M8> ERR not mounted or bad path");
    return;
  }
  std::string full = std::string(BASE) + "/" + path;
  std::string part = full + ".part";
  if (!this->make_parents_(full)) {
    ESP_LOGW(TAG, "#M8> ERR can't create folders for %s", path.c_str());
    return;
  }
  // Internal, DMA-capable buffer: SD writes from PSRAM go one sector at a time.
  auto *buf = static_cast<uint8_t *>(heap_caps_aligned_alloc(4, PUT_CHUNK, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
  int fd = buf ? open(part.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0664) : -1;
  if (fd < 0) {
    ESP_LOGW(TAG, "#M8> ERR can't open %s (errno %d)", part.c_str(), errno);
    heap_caps_free(buf);
    return;
  }
  ESP_LOGI(TAG, "#M8> READY %s %u", path.c_str(), (unsigned) size);
  // Blocking receive: the UI pauses while a file streams in. Other tasks (Wi-Fi, audio)
  // keep running; the watchdog is fed as we go.
  uint32_t got = 0, fill = 0, crc = 0, next_ack = PUT_ACK_EVERY, last_rx = millis(), t0 = millis();
  bool ok = true;
  while (got < size) {
    uint32_t want = std::min<uint32_t>(PUT_CHUNK - fill, size - got);
    int n = usb_serial_jtag_read_bytes(buf + fill, want, pdMS_TO_TICKS(20));
    if (n > 0) {
      crc = crc32_update(crc, buf + fill, n);
      fill += n;
      got += n;
      last_rx = millis();
      if (fill == PUT_CHUNK || got == size) {
        if (write(fd, buf, fill) != (ssize_t) fill) {
          ESP_LOGW(TAG, "#M8> ERR write failed at %u (errno %d)", (unsigned) got, errno);
          ok = false;
          break;
        }
        fill = 0;
      }
      if (got >= next_ack || got == size) {
        char ack[32];
        int len = snprintf(ack, sizeof ack, "\n#M8> ACK %u\n", (unsigned) got);
        usb_serial_jtag_write_bytes(ack, len, pdMS_TO_TICKS(20));
        next_ack = got + PUT_ACK_EVERY;
      }
    } else if (millis() - last_rx > PUT_IDLE_TIMEOUT_MS) {
      ESP_LOGW(TAG, "#M8> ERR timeout after %u of %u bytes", (unsigned) got, (unsigned) size);
      ok = false;
      break;
    }
    App.feed_wdt();
  }
  close(fd);
  heap_caps_free(buf);
  if (ok && crc != crc_expected) {
    ESP_LOGW(TAG, "#M8> ERR crc mismatch: got %08x, expected %08x", (unsigned) crc, (unsigned) crc_expected);
    ok = false;
  }
  if (!ok) {
    unlink(part.c_str());
    return;
  }
  unlink(full.c_str());  // FatFs rename won't replace an existing file
  if (rename(part.c_str(), full.c_str()) != 0) {
    ESP_LOGW(TAG, "#M8> ERR rename failed (errno %d)", errno);
    return;
  }
  uint32_t ms = millis() - t0;
  xTaskCreatePinnedToCore(M8Sd::space_task_, "m8sd_space", 4096, this, 3, nullptr, 0);
  ESP_LOGI(TAG, "#M8> OK %s %u bytes in %u ms (%u KB/s)", path.c_str(), (unsigned) size, (unsigned) ms,
           (unsigned) (ms ? size / ms : 0));
}

}  // namespace esphome::m8sd
