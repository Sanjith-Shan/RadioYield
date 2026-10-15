/* BLE backend (NimBLE host, legacy advertising only; CONFIG_BT_NIMBLE_50_FEATURE_SUPPORT
 * is off so the legacy ble_gap_adv_* / ble_gap_disc APIs are the ones compiled in).
 *
 * DUT: one "packet" = one legacy ADV_NONCONN_IND advertising event. For each seq the
 * firmware sets the advertising data, starts advertising with a duration of
 * CONFIG_RY_BLE_ADV_WINDOW_MS and an advertising interval longer than that window, and
 * waits for BLE_GAP_EVENT_ADV_COMPLETE. So each seq is sent in exactly one advertising
 * event, on the channels in the channel map (37, 38, 39, or all three). If the
 * controller were to delay the first event past the window, that seq would never be
 * sent; the RIG.md 1 m self-check catches that (PER must be ~0 at 1 m).
 *
 * REF: passive scan, no duplicate filtering, scan window == scan interval. NimBLE's
 * advertising report does not say which advertising channel the PDU arrived on and the
 * standard HCI has no per-channel scan selection, so RX lines carry rx_ch=0 ("not
 * reported"); the DUT's commanded channel map travels in the payload (tx_ch). With a
 * single-channel map the REF only hears the DUT while its own scan is parked on that
 * channel, so PER is then not an RF number (the collector reports RX_FRAC instead).
 *
 * TX power: esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV, ...) from esp_bt.h (ESP32-C6
 * levels -15..+20 dBm, 3 dB steps, top step +20).
 */
#include <string.h>

#include "ry_app.h"

#if CONFIG_BT_NIMBLE_ENABLED

#include "esp_bt.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "host/ble_hs.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"

static const char *TAG = "ry_ble";
static bool s_inited;
static volatile bool s_synced;
static uint8_t s_own_addr_type;
static uint8_t s_chmap = 0x07;
static SemaphoreHandle_t s_adv_done;
static volatile bool s_scanning;

static const struct {
    int dbm;
    esp_power_level_t lvl;
} k_levels[] = {
    {-15, ESP_PWR_LVL_N15}, {-12, ESP_PWR_LVL_N12}, {-9, ESP_PWR_LVL_N9}, {-6, ESP_PWR_LVL_N6},
    {-3, ESP_PWR_LVL_N3},   {0, ESP_PWR_LVL_N0},    {3, ESP_PWR_LVL_P3},  {6, ESP_PWR_LVL_P6},
    {9, ESP_PWR_LVL_P9},    {12, ESP_PWR_LVL_P12},  {15, ESP_PWR_LVL_P15}, {18, ESP_PWR_LVL_P18},
    {20, ESP_PWR_LVL_P20},
};

static void on_sync(void)
{
    if (ble_hs_id_infer_auto(0, &s_own_addr_type) != 0) s_own_addr_type = BLE_OWN_ADDR_PUBLIC;
    s_synced = true;
}

static void on_reset(int reason)
{
    ESP_LOGW(TAG, "host reset, reason %d", reason);
    s_synced = false;
}

static void host_task(void *param)
{
    (void)param;
    nimble_port_run(); /* returns only after nimble_port_stop() */
    nimble_port_freertos_deinit();
}

static int gap_cb(struct ble_gap_event *ev, void *arg)
{
    (void)arg;
    ry_pkt_t p;
    switch (ev->type) {
    case BLE_GAP_EVENT_ADV_COMPLETE:
        xSemaphoreGive(s_adv_done);
        return 0;
    case BLE_GAP_EVENT_DISC: {
        int rc = ry_ble_adv_find(ev->disc.data, ev->disc.length_data, &p);
        if (rc == RY_OK) ry_rx_push_pkt(&p, ev->disc.rssi, 0, -1);
        else if (rc == RY_ERR_CRC || rc == RY_ERR_VERSION || rc == RY_ERR_FIELD) g_rx_cnt.bad++;
        else g_rx_cnt.other++;
        return 0;
    }
    case BLE_GAP_EVENT_DISC_COMPLETE:
        s_scanning = false;
        return 0;
    default:
        return 0;
    }
}

static esp_err_t ble_init_once(void)
{
    if (s_inited) return ESP_OK;
    esp_err_t err = nimble_port_init();
    if (err != ESP_OK) return err;
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;
    s_adv_done = xSemaphoreCreateBinary();
    nimble_port_freertos_init(host_task);
    for (int i = 0; i < 300 && !s_synced; i++) vTaskDelay(pdMS_TO_TICKS(10));
    if (!s_synced) return ESP_ERR_TIMEOUT;
    s_inited = true;
    return ESP_OK;
}

esp_err_t ry_ble_enter(int ch, int pwr_qdbm, int *pwr_act_qdbm)
{
    esp_err_t err = ble_init_once();
    if (err != ESP_OK) return err;
    switch (ch) {
    case 37: s_chmap = 0x01; break;
    case 38: s_chmap = 0x02; break;
    case 39: s_chmap = 0x04; break;
    case RY_BLE_CH_ALL: s_chmap = 0x07; break;
    default: return ESP_ERR_INVALID_ARG;
    }
    /* nearest supported level (ties go down) */
    int best = 0;
    for (int i = 1; i < (int)(sizeof(k_levels) / sizeof(k_levels[0])); i++) {
        int d_best = pwr_qdbm - k_levels[best].dbm * 4, d_i = pwr_qdbm - k_levels[i].dbm * 4;
        if ((d_i < 0 ? -d_i : d_i) < (d_best < 0 ? -d_best : d_best)) best = i;
    }
    err = esp_ble_tx_power_set(ESP_BLE_PWR_TYPE_ADV, k_levels[best].lvl);
    if (err != ESP_OK) return err;
    *pwr_act_qdbm = k_levels[best].dbm * 4;
    return ESP_OK;
}

void ry_ble_leave(void)
{
    if (!s_inited) return;
    if (ble_gap_adv_active()) ble_gap_adv_stop();
    if (ble_gap_disc_active()) ble_gap_disc_cancel();
    s_scanning = false;
}

esp_err_t ry_ble_tx_one(const uint8_t *adv, size_t len)
{
    if (!s_inited || !s_synced) return ESP_ERR_INVALID_STATE;
    if (ble_gap_adv_active()) ble_gap_adv_stop();
    xSemaphoreTake(s_adv_done, 0);
    int rc = ble_gap_adv_set_data(adv, (int)len);
    if (rc != 0) return ESP_FAIL;
    struct ble_gap_adv_params ap;
    memset(&ap, 0, sizeof(ap));
    ap.conn_mode = BLE_GAP_CONN_MODE_NON;
    ap.disc_mode = BLE_GAP_DISC_MODE_NON;
    /* interval (0.625 ms units) longer than the window -> one event per start */
    uint16_t itvl = (uint16_t)((CONFIG_RY_BLE_ADV_ITVL_MS * 1000) / 625);
    ap.itvl_min = itvl;
    ap.itvl_max = itvl;
    ap.channel_map = s_chmap;
    rc = ble_gap_adv_start(s_own_addr_type, NULL, CONFIG_RY_BLE_ADV_WINDOW_MS, &ap, gap_cb, NULL);
    if (rc != 0) return ESP_FAIL;
    if (xSemaphoreTake(s_adv_done, pdMS_TO_TICKS(CONFIG_RY_BLE_ADV_WINDOW_MS + 200)) != pdTRUE) {
        ble_gap_adv_stop();
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

esp_err_t ry_ble_rx(bool on, int scan_ms)
{
    if (!s_inited || !s_synced) return ESP_ERR_INVALID_STATE;
    if (!on) {
        if (ble_gap_disc_active()) ble_gap_disc_cancel();
        s_scanning = false;
        return ESP_OK;
    }
    if (ble_gap_disc_active()) ble_gap_disc_cancel();
    struct ble_gap_disc_params dp;
    memset(&dp, 0, sizeof(dp));
    long units = ((long)scan_ms * 1000) / 625;
    if (units < 4) units = 4;
    if (units > 0x4000) units = 0x4000; /* 10.24 s, HCI maximum */
    dp.itvl = (uint16_t)units;
    dp.window = (uint16_t)units; /* continuous listening */
    dp.passive = 1;
    dp.filter_duplicates = 0; /* every advertising report, not one per address */
    dp.filter_policy = 0;
    dp.limited = 0;
    int rc = ble_gap_disc(s_own_addr_type, BLE_HS_FOREVER, &dp, gap_cb, NULL);
    if (rc != 0) return ESP_FAIL;
    s_scanning = true;
    return ESP_OK;
}

#else /* !CONFIG_BT_NIMBLE_ENABLED */

esp_err_t ry_ble_enter(int ch, int pwr_qdbm, int *pwr_act_qdbm)
{
    (void)ch; (void)pwr_qdbm; (void)pwr_act_qdbm;
    return ESP_ERR_NOT_SUPPORTED;
}
void ry_ble_leave(void) {}
esp_err_t ry_ble_tx_one(const uint8_t *adv, size_t len)
{
    (void)adv; (void)len;
    return ESP_ERR_NOT_SUPPORTED;
}
esp_err_t ry_ble_rx(bool on, int scan_ms)
{
    (void)on; (void)scan_ms;
    return ESP_ERR_NOT_SUPPORTED;
}

#endif
