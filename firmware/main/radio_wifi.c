/* WiFi backend: ESP-NOW broadcast. Broadcast frames are not ACKed or retried by the
 * MAC, so a frame the REF misses is a real over-the-air loss, not hidden by retries.
 * API: ESP-IDF v5.5 esp_now.h / esp_wifi.h (send callback takes esp_now_send_info_t
 * from v5.5; older releases took a MAC pointer, handled below). */
#include <string.h>

#include "esp_event.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "ry_app.h"

static const char *TAG = "ry_wifi";
static const uint8_t BCAST[ESP_NOW_ETH_ALEN] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

static bool s_inited, s_started;
static volatile bool s_rx_on;
static SemaphoreHandle_t s_sent;
static volatile esp_now_send_status_t s_last_status;

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
static void send_cb(const esp_now_send_info_t *tx_info, esp_now_send_status_t status)
{
    (void)tx_info;
#else
static void send_cb(const uint8_t *mac, esp_now_send_status_t status)
{
    (void)mac;
#endif
    s_last_status = status;
    xSemaphoreGive(s_sent);
}

static void recv_cb(const esp_now_recv_info_t *info, const uint8_t *data, int len)
{
    if (!s_rx_on || len <= 0) return;
    ry_pkt_t p;
    int rc = ry_pkt_decode(data, (size_t)len, &p, NULL);
    if (rc != RY_OK) {
        if (rc == RY_ERR_CRC || rc == RY_ERR_VERSION || rc == RY_ERR_FIELD) g_rx_cnt.bad++;
        else g_rx_cnt.other++;
        return;
    }
    int8_t rssi = 127;
    uint8_t ch = 0;
    if (info && info->rx_ctrl) {
        rssi = (int8_t)info->rx_ctrl->rssi;
        ch = (uint8_t)info->rx_ctrl->channel;
    }
    ry_rx_push_pkt(&p, rssi, ch, -1);
}

static esp_err_t wifi_init_once(void)
{
    if (s_inited) return ESP_OK;
    esp_err_t err = esp_netif_init();
    if (err != ESP_OK) return err;
    err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) return err;
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&cfg);
    if (err != ESP_OK) return err;
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    s_sent = xSemaphoreCreateBinary();
    s_inited = true;
    return ESP_OK;
}

esp_err_t ry_wifi_enter(int ch, int pwr_qdbm, int *pwr_act_qdbm)
{
    esp_err_t err = wifi_init_once();
    if (err != ESP_OK) return err;
    if (!s_started) {
        if ((err = esp_wifi_start()) != ESP_OK) return err;
        esp_wifi_set_ps(WIFI_PS_NONE); /* modem sleep would make the REF miss frames */
        if ((err = esp_now_init()) != ESP_OK) return err;
        esp_now_register_send_cb(send_cb);
        esp_now_register_recv_cb(recv_cb);
        esp_now_peer_info_t peer;
        memset(&peer, 0, sizeof(peer));
        memcpy(peer.peer_addr, BCAST, ESP_NOW_ETH_ALEN);
        peer.channel = 0; /* = current channel */
        peer.ifidx = WIFI_IF_STA;
        peer.encrypt = false;
        if ((err = esp_now_add_peer(&peer)) != ESP_OK) return err;
        s_started = true;
    }
    if ((err = esp_wifi_set_channel((uint8_t)ch, WIFI_SECOND_CHAN_NONE)) != ESP_OK) return err;
    /* 0.25 dBm units, valid [8, 84]; the driver quantizes (see esp_wifi.h). */
    if ((err = esp_wifi_set_max_tx_power((int8_t)pwr_qdbm)) != ESP_OK) return err;
    int8_t act = 0;
    if (esp_wifi_get_max_tx_power(&act) == ESP_OK) *pwr_act_qdbm = act;
    else *pwr_act_qdbm = pwr_qdbm;
    ESP_LOGI(TAG, "ch %d pwr %d -> %d qdBm", ch, pwr_qdbm, *pwr_act_qdbm);
    return ESP_OK;
}

void ry_wifi_leave(void)
{
    s_rx_on = false;
    if (!s_started) return;
    esp_now_deinit();
    esp_wifi_stop();
    s_started = false;
}

esp_err_t ry_wifi_tx_one(const uint8_t *buf, size_t len)
{
    if (!s_started) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_sent, 0); /* clear a stale give */
    esp_err_t err;
    int tries = 0;
    while ((err = esp_now_send(BCAST, buf, len)) == ESP_ERR_ESPNOW_NO_MEM && tries++ < 50) {
        vTaskDelay(pdMS_TO_TICKS(1)); /* TX queue full: back off, do not drop the seq */
    }
    if (err != ESP_OK) return err;
    if (xSemaphoreTake(s_sent, pdMS_TO_TICKS(100)) != pdTRUE) return ESP_ERR_TIMEOUT;
    return s_last_status == ESP_NOW_SEND_SUCCESS ? ESP_OK : ESP_FAIL;
}

esp_err_t ry_wifi_rx(bool on)
{
    if (!s_started) return ESP_ERR_INVALID_STATE;
    s_rx_on = on;
    return ESP_OK;
}
