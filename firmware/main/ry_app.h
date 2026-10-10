/* Shared declarations for the RadioYield rig firmware (ESP32-C6, ESP-IDF v5.5.x). */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "ry_codec.h"
#include "sdkconfig.h"

#define RY_FW_VERSION "0.1.0"

#if CONFIG_RY_ROLE_DUT
#define RY_ROLE_NAME "DUT"
#elif CONFIG_RY_ROLE_REF
#define RY_ROLE_NAME "REF"
#else
#error "Select a role: CONFIG_RY_ROLE_DUT or CONFIG_RY_ROLE_REF (sdkconfig.defaults.dut/.ref)"
#endif

/* ---- output (console.c) ------------------------------------------------------ */
/* Prints one complete protocol line (adds "\n"), atomically w.r.t. other tasks. */
void ry_out(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void ry_console_init(void);
/* Blocking read of one line from the host. Returns length, or -1 on overflow
 * (the rest of the over-long line is discarded). */
int ry_console_readline(char *buf, size_t buflen);

/* ---- identity ------------------------------------------------------------------ */
extern uint8_t g_mac[6];
extern uint16_t g_dut_tag; /* low 16 bits of the WiFi STA MAC */

/* ---- REF receive pipeline (rx.c) ------------------------------------------------ */
typedef struct {
    volatile uint32_t ok;      /* valid RY packets of the active radio queued for output */
    volatile uint32_t bad;     /* frames that looked like ours but failed decode (CRC etc.) */
    volatile uint32_t other;   /* foreign frames / other radio / non-RY adverts (ignored) */
    volatile uint32_t ovf;     /* dropped because the output queue was full (REF-side loss!) */
} ry_rx_counters_t;
extern ry_rx_counters_t g_rx_cnt;
extern volatile bool g_rx_enabled;
extern volatile uint8_t g_rx_radio; /* ry_radio_t currently being received */

void ry_rx_init(void);
void ry_rx_reset_counters(void);
/* From task context (WiFi / NimBLE callbacks): a decoded packet. */
void ry_rx_push_pkt(const ry_pkt_t *p, int8_t rssi, uint8_t rx_ch, int16_t lqi);
/* From ISR context (802.15.4): raw frame copy, decoded later in the RX task. */
void ry_rx_push_154_from_isr(const uint8_t *frame, int8_t rssi, uint8_t lqi, uint8_t ch);

/* ---- radio backends ---------------------------------------------------------------
 * enter(): bring the radio up on ch at the requested TX power; *pwr_act_qdbm is the
 * power the driver reports back (after quantization), or the requested value when
 * the driver has no read-back. leave(): stop all activity so another radio can use
 * the shared 2.4 GHz front end. tx_one(): send one packet and wait until the radio
 * reports it sent (or failed). rx(): start/stop receiving. */
esp_err_t ry_wifi_enter(int ch, int pwr_qdbm, int *pwr_act_qdbm);
void ry_wifi_leave(void);
esp_err_t ry_wifi_tx_one(const uint8_t *buf, size_t len);
esp_err_t ry_wifi_rx(bool on);

esp_err_t ry_ble_enter(int ch, int pwr_qdbm, int *pwr_act_qdbm);
void ry_ble_leave(void);
esp_err_t ry_ble_tx_one(const uint8_t *adv, size_t len);
esp_err_t ry_ble_rx(bool on, int scan_ms);

esp_err_t ry_154_enter(int ch, int pwr_qdbm, int *pwr_act_qdbm);
void ry_154_leave(void);
esp_err_t ry_154_tx_one(const uint8_t *frame);
esp_err_t ry_154_rx(bool on);
bool ry_154_available(void);
