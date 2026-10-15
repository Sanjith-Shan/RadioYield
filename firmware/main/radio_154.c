/* IEEE 802.15.4 backend: raw data frames via the esp_ieee802154 driver (ESP-IDF v5.5
 * components/ieee802154/include/esp_ieee802154.h). Behind CONFIG_RY_ENABLE_IEEE802154,
 * because this is the least-trodden of the three paths (no OpenThread / Zigbee stack;
 * the raw driver is used directly, like examples/ieee802154/ieee802154_cli).
 *
 * DUT: data frames with no ACK request (AR=0) and no CCA, so the MAC never retries and
 * the medium is not sensed: a lost frame is lost. REF: promiscuous, rx_when_idle.
 * Driver callbacks run in ISR context; they only copy the frame / give a semaphore.
 * TX power: esp_ieee802154_set_txpower(dBm), read back with esp_ieee802154_get_txpower().
 */
#include <string.h>

#include "ry_app.h"

#if CONFIG_RY_ENABLE_IEEE802154

#include "esp_attr.h"
#include "esp_ieee802154.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "ry_154";
static bool s_enabled;
static volatile bool s_rx_on;
static SemaphoreHandle_t s_tx_done;
static volatile int s_tx_result; /* 0 ok, else esp_ieee802154_tx_error_t + 1 */
static volatile uint8_t s_ch = 11;

static void IRAM_ATTR on_rx_done(uint8_t *frame, esp_ieee802154_frame_info_t *info)
{
    if (s_rx_on) ry_rx_push_154_from_isr(frame, info->rssi, info->lqi, info->channel ? info->channel : s_ch);
    esp_ieee802154_receive_handle_done(frame);
}

static void IRAM_ATTR on_tx_done(const uint8_t *frame, const uint8_t *ack, esp_ieee802154_frame_info_t *ack_info)
{
    (void)frame;
    (void)ack_info;
    if (ack) esp_ieee802154_receive_handle_done(ack); /* never expected: AR=0 */
    s_tx_result = 0;
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(s_tx_done, &woken);
    if (woken) portYIELD_FROM_ISR();
}

static void IRAM_ATTR on_tx_failed(const uint8_t *frame, esp_ieee802154_tx_error_t error)
{
    (void)frame;
    s_tx_result = (int)error + 1;
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(s_tx_done, &woken);
    if (woken) portYIELD_FROM_ISR();
}

bool ry_154_available(void) { return true; }

esp_err_t ry_154_enter(int ch, int pwr_qdbm, int *pwr_act_qdbm)
{
    esp_err_t err;
    if (!s_tx_done) s_tx_done = xSemaphoreCreateBinary();
    if (!s_enabled) {
        esp_ieee802154_event_cb_list_t cbs;
        memset(&cbs, 0, sizeof(cbs));
        cbs.rx_done_cb = on_rx_done;
        cbs.tx_done_cb = on_tx_done;
        cbs.tx_failed_cb = on_tx_failed;
        /* must be registered while the subsystem is disabled */
        if ((err = esp_ieee802154_event_callback_list_register(cbs)) != ESP_OK) return err;
        if ((err = esp_ieee802154_enable()) != ESP_OK) return err;
        esp_ieee802154_set_panid(CONFIG_RY_154_PAN_ID);
        esp_ieee802154_set_short_address(g_dut_tag);
        esp_ieee802154_set_promiscuous(true);
        esp_ieee802154_set_coordinator(false);
        s_enabled = true;
    }
    s_ch = (uint8_t)ch;
    if ((err = esp_ieee802154_set_channel((uint8_t)ch)) != ESP_OK) return err;
    if ((err = esp_ieee802154_set_txpower((int8_t)(pwr_qdbm / 4))) != ESP_OK) return err;
    *pwr_act_qdbm = esp_ieee802154_get_txpower() * 4;
    ESP_LOGI(TAG, "ch %d pwr %d -> %d qdBm", ch, pwr_qdbm, *pwr_act_qdbm);
    return ESP_OK;
}

void ry_154_leave(void)
{
    s_rx_on = false;
    if (!s_enabled) return;
    esp_ieee802154_set_rx_when_idle(false);
    esp_ieee802154_disable();
    esp_ieee802154_event_callback_list_unregister();
    s_enabled = false;
}

esp_err_t ry_154_tx_one(const uint8_t *frame)
{
    if (!s_enabled) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_tx_done, 0);
    esp_err_t err = esp_ieee802154_transmit(frame, false);
    if (err != ESP_OK) return err;
    if (xSemaphoreTake(s_tx_done, pdMS_TO_TICKS(50)) != pdTRUE) return ESP_ERR_TIMEOUT;
    return s_tx_result == 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t ry_154_rx(bool on)
{
    if (!s_enabled) return ESP_ERR_INVALID_STATE;
    s_rx_on = on;
    esp_ieee802154_set_rx_when_idle(on);
    if (on) return esp_ieee802154_receive();
    return esp_ieee802154_sleep();
}

#else /* !CONFIG_RY_ENABLE_IEEE802154 */

bool ry_154_available(void) { return false; }
esp_err_t ry_154_enter(int ch, int pwr_qdbm, int *pwr_act_qdbm)
{
    (void)ch; (void)pwr_qdbm; (void)pwr_act_qdbm;
    return ESP_ERR_NOT_SUPPORTED;
}
void ry_154_leave(void) {}
esp_err_t ry_154_tx_one(const uint8_t *frame)
{
    (void)frame;
    return ESP_ERR_NOT_SUPPORTED;
}
esp_err_t ry_154_rx(bool on)
{
    (void)on;
    return ESP_ERR_NOT_SUPPORTED;
}

#endif
