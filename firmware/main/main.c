/* RadioYield rig firmware: command loop, radio selection, TX engine.
 * Protocol: firmware/PROTOCOL.md. Role (DUT or REF) is fixed at build time. */
#include <stdio.h>
#include <string.h>

#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "ry_app.h"

uint8_t g_mac[6];
uint16_t g_dut_tag;

/* current radio configuration, owned by the console task */
static struct {
    ry_radio_t radio;
    int ch;
    int pwr_qdbm;     /* as commanded by the host */
    int pwr_act_qdbm; /* as reported by the driver after the build's table offset */
    int len;
    int scan_ms;
} s_cfg = {.radio = RY_RADIO_NONE, .len = RY_PKT_HDR_LEN, .scan_ms = 1000};

#if CONFIG_RY_ROLE_DUT
typedef struct {
    uint32_t n;
    uint32_t interval_ms;
    uint16_t run_id;
} tx_job_t;
static QueueHandle_t s_tx_q;
static volatile bool s_tx_busy, s_tx_abort;
#endif

static void leave_all(void)
{
    ry_wifi_leave();
    ry_ble_leave();
    ry_154_leave();
}

static const char *err_code(esp_err_t e)
{
    switch (e) {
    case ESP_ERR_NOT_SUPPORTED: return "RADIO";
    case ESP_ERR_INVALID_ARG: return "RANGE";
    case ESP_ERR_INVALID_STATE: return "STATE";
    case ESP_ERR_TIMEOUT: return "TIMEOUT";
    default: return "DRIVER";
    }
}

static void reply_param_err(const char *verb, const char *key, int rc)
{
    ry_out("ERR %s %s key=%s", verb, rc == RY_ERR_MISSING ? "MISSING" : rc == RY_ERR_RANGE ? "RANGE" : "SYNTAX", key);
}

/* ---- INFO / PING ---------------------------------------------------------------- */
static void cmd_info(void)
{
    ry_out("OK INFO role=%s fw=%s proto=%d idf=%s chip=esp32c6 mac=%02x:%02x:%02x:%02x:%02x:%02x tag=%04x "
           "radios=wifi%s%s pwr_offset_qdbm=%d label=%s ble_window_ms=%d ble_itvl_ms=%d",
           RY_ROLE_NAME, RY_FW_VERSION, RY_PROTO_VERSION, esp_get_idf_version(), g_mac[0], g_mac[1], g_mac[2],
           g_mac[3], g_mac[4], g_mac[5], g_dut_tag,
#if CONFIG_BT_NIMBLE_ENABLED
           ",ble",
#else
           "",
#endif
           ry_154_available() ? ",154" : "", CONFIG_RY_TXPWR_OFFSET_QDBM,
           CONFIG_RY_FW_LABEL[0] ? CONFIG_RY_FW_LABEL : "none", CONFIG_RY_BLE_ADV_WINDOW_MS,
           CONFIG_RY_BLE_ADV_ITVL_MS);
}

/* ---- SET -------------------------------------------------------------------------- */
static void cmd_set(const ry_cmd_t *c)
{
#if CONFIG_RY_ROLE_DUT
    if (s_tx_busy) {
        ry_out("ERR SET BUSY tx in progress");
        return;
    }
#endif
    const char *rs = ry_cmd_get(c, "radio");
    ry_radio_t radio;
    if (!rs) { reply_param_err("SET", "radio", RY_ERR_MISSING); return; }
    if (ry_radio_parse(rs, &radio) != RY_OK || radio == RY_RADIO_NONE) {
        reply_param_err("SET", "radio", RY_ERR_RANGE);
        return;
    }
    long ch, pwr, len, scan;
    int rc;
    const char *chs = ry_cmd_get(c, "ch");
    if (radio == RY_RADIO_BLE && chs && strcmp(chs, "all") == 0) {
        ch = RY_BLE_CH_ALL;
    } else {
        long lo = radio == RY_RADIO_WIFI ? 1 : radio == RY_RADIO_BLE ? 37 : 11;
        long hi = radio == RY_RADIO_WIFI ? 13 : radio == RY_RADIO_BLE ? 39 : 26;
        if ((rc = ry_cmd_get_int(c, "ch", lo, hi, 0, 0, &ch)) != RY_OK) { reply_param_err("SET", "ch", rc); return; }
    }
    /* REF ignores pwr_qdbm for RF purposes but still validates it */
    long plo = radio == RY_RADIO_WIFI ? 8 : -60, phi = radio == RY_RADIO_WIFI ? 84 : 80;
    if ((rc = ry_cmd_get_int(c, "pwr_qdbm", plo, phi, 1, radio == RY_RADIO_WIFI ? 80 : 0, &pwr)) != RY_OK) {
        reply_param_err("SET", "pwr_qdbm", rc);
        return;
    }
    long lmax = radio == RY_RADIO_WIFI ? RY_PKT_MAX_LEN : radio == RY_RADIO_154 ? RY_154_MAX_PAYLOAD : RY_PKT_HDR_LEN;
    if ((rc = ry_cmd_get_int(c, "len", RY_PKT_HDR_LEN, lmax, 1, RY_PKT_HDR_LEN, &len)) != RY_OK) {
        reply_param_err("SET", "len", rc);
        return;
    }
    if ((rc = ry_cmd_get_int(c, "scan_ms", 3, 10240, 1, 1000, &scan)) != RY_OK) {
        reply_param_err("SET", "scan_ms", rc);
        return;
    }

    /* build-time TX power table offset (the "EVT2 firmware" variant), clamped to range */
    long eff = pwr + CONFIG_RY_TXPWR_OFFSET_QDBM;
    if (eff < plo) eff = plo;
    if (eff > phi) eff = phi;

    bool was_rx = g_rx_enabled;
    g_rx_enabled = false;
    if (radio != s_cfg.radio) leave_all();
    int act = (int)eff;
    esp_err_t e;
    switch (radio) {
    case RY_RADIO_WIFI: e = ry_wifi_enter((int)ch, (int)eff, &act); break;
    case RY_RADIO_BLE: e = ry_ble_enter((int)ch, (int)eff, &act); break;
    default: e = ry_154_enter((int)ch, (int)eff, &act); break;
    }
    if (e != ESP_OK) {
        s_cfg.radio = RY_RADIO_NONE;
        leave_all();
        ry_out("ERR SET %s %s", err_code(e), esp_err_to_name(e));
        return;
    }
    s_cfg.radio = radio;
    s_cfg.ch = (int)ch;
    s_cfg.pwr_qdbm = (int)pwr;
    s_cfg.pwr_act_qdbm = act;
    s_cfg.len = (int)len;
    s_cfg.scan_ms = (int)scan;
    g_rx_radio = (uint8_t)radio;
    (void)was_rx; /* RX is always left stopped after SET; the host restarts it */
    if (radio == RY_RADIO_BLE && ch == RY_BLE_CH_ALL)
        ry_out("OK SET radio=ble ch=all pwr_qdbm=%ld pwr_act_qdbm=%d len=%ld scan_ms=%ld", pwr, act, len, scan);
    else
        ry_out("OK SET radio=%s ch=%ld pwr_qdbm=%ld pwr_act_qdbm=%d len=%ld scan_ms=%ld", ry_radio_name(radio), ch,
               pwr, act, len, scan);
}

/* ---- TX (DUT) -------------------------------------------------------------------- */
#if CONFIG_RY_ROLE_DUT
static void tx_task(void *arg)
{
    (void)arg;
    tx_job_t job;
    static uint8_t buf[RY_154_MAX_PSDU + 1 > RY_PKT_MAX_LEN ? RY_154_MAX_PSDU + 1 : RY_PKT_MAX_LEN];
    for (;;) {
        if (xQueueReceive(s_tx_q, &job, portMAX_DELAY) != pdTRUE) continue;
        ry_pkt_t p;
        memset(&p, 0, sizeof(p));
        p.radio = (uint8_t)s_cfg.radio;
        p.channel = (uint8_t)s_cfg.ch;
        p.run_id = job.run_id;
        p.pwr_qdbm = (int16_t)s_cfg.pwr_qdbm;
        p.dut_tag = g_dut_tag;
        uint32_t ok = 0, fail = 0, sent = 0;
        esp_err_t first_err = ESP_OK;
        int64_t t0 = esp_timer_get_time();
        TickType_t wake = xTaskGetTickCount();
        TickType_t period = pdMS_TO_TICKS(job.interval_ms);
        if (period == 0) period = 1;
        for (uint32_t i = 0; i < job.n && !s_tx_abort; i++) {
            p.seq = i;
            esp_err_t e;
            int n;
            switch (s_cfg.radio) {
            case RY_RADIO_WIFI:
                n = ry_pkt_encode(&p, (size_t)s_cfg.len, buf, sizeof(buf));
                e = n > 0 ? ry_wifi_tx_one(buf, (size_t)n) : ESP_ERR_INVALID_ARG;
                break;
            case RY_RADIO_BLE:
                n = ry_ble_adv_build(&p, buf, sizeof(buf));
                e = n > 0 ? ry_ble_tx_one(buf, (size_t)n) : ESP_ERR_INVALID_ARG;
                break;
            default:
                n = ry_154_frame_build(&p, (size_t)s_cfg.len, CONFIG_RY_154_PAN_ID, g_dut_tag, buf, sizeof(buf));
                e = n > 0 ? ry_154_tx_one(buf) : ESP_ERR_INVALID_ARG;
                break;
            }
            sent++;
            if (e == ESP_OK) ok++;
            else {
                fail++;
                if (first_err == ESP_OK) first_err = e;
            }
            xTaskDelayUntil(&wake, period);
        }
        int64_t dur_ms = (esp_timer_get_time() - t0) / 1000;
        ry_out("TXDONE run=%u n=%lu sent=%lu ok=%lu fail=%lu aborted=%d dur_ms=%lld radio=%s ch=%d pwr_qdbm=%d "
               "pwr_act_qdbm=%d len=%d err=%s",
               (unsigned)job.run_id, (unsigned long)job.n, (unsigned long)sent, (unsigned long)ok,
               (unsigned long)fail, s_tx_abort ? 1 : 0, (long long)dur_ms, ry_radio_name(s_cfg.radio), s_cfg.ch,
               s_cfg.pwr_qdbm, s_cfg.pwr_act_qdbm, s_cfg.len, first_err == ESP_OK ? "none" : esp_err_to_name(first_err));
        s_tx_abort = false;
        s_tx_busy = false;
    }
}
#endif

static void cmd_tx(const ry_cmd_t *c)
{
#if CONFIG_RY_ROLE_DUT
    if (s_cfg.radio == RY_RADIO_NONE) { ry_out("ERR TX STATE no radio selected, send SET first"); return; }
    if (s_tx_busy) { ry_out("ERR TX BUSY tx in progress"); return; }
    long n, itv, run;
    int rc;
    long min_itv = s_cfg.radio == RY_RADIO_BLE ? CONFIG_RY_BLE_ADV_WINDOW_MS + 5 : 1;
    if ((rc = ry_cmd_get_int(c, "n", 1, 1000000, 0, 0, &n)) != RY_OK) { reply_param_err("TX", "n", rc); return; }
    if ((rc = ry_cmd_get_int(c, "interval_ms", min_itv, 60000, 0, 0, &itv)) != RY_OK) {
        reply_param_err("TX", "interval_ms", rc);
        return;
    }
    if ((rc = ry_cmd_get_int(c, "run", 0, 65535, 0, 0, &run)) != RY_OK) { reply_param_err("TX", "run", rc); return; }
    tx_job_t job = {.n = (uint32_t)n, .interval_ms = (uint32_t)itv, .run_id = (uint16_t)run};
    s_tx_busy = true;
    s_tx_abort = false;
    xQueueSend(s_tx_q, &job, portMAX_DELAY);
    ry_out("OK TX run=%ld n=%ld interval_ms=%ld", run, n, itv);
#else
    (void)c;
    ry_out("ERR TX ROLE this board is a REF");
#endif
}

static void cmd_stop(void)
{
#if CONFIG_RY_ROLE_DUT
    if (s_tx_busy) s_tx_abort = true;
#endif
    ry_out("OK STOP");
}

/* ---- RX (REF) -------------------------------------------------------------------- */
static void cmd_rx(const ry_cmd_t *c)
{
#if CONFIG_RY_ROLE_REF
    esp_err_t e;
    if (strcmp(c->arg, "start") == 0) {
        if (s_cfg.radio == RY_RADIO_NONE) { ry_out("ERR RX STATE no radio selected, send SET first"); return; }
        ry_rx_reset_counters();
        g_rx_radio = (uint8_t)s_cfg.radio;
        g_rx_enabled = true;
        switch (s_cfg.radio) {
        case RY_RADIO_WIFI: e = ry_wifi_rx(true); break;
        case RY_RADIO_BLE: e = ry_ble_rx(true, s_cfg.scan_ms); break;
        default: e = ry_154_rx(true); break;
        }
        if (e != ESP_OK) {
            g_rx_enabled = false;
            ry_out("ERR RX %s %s", err_code(e), esp_err_to_name(e));
            return;
        }
        ry_out("OK RX start radio=%s ch=%d", ry_radio_name(s_cfg.radio), s_cfg.ch);
    } else if (strcmp(c->arg, "stop") == 0) {
        switch (s_cfg.radio) {
        case RY_RADIO_WIFI: ry_wifi_rx(false); break;
        case RY_RADIO_BLE: ry_ble_rx(false, 0); break;
        case RY_RADIO_154: ry_154_rx(false); break;
        default: break;
        }
        g_rx_enabled = false;
        vTaskDelay(pdMS_TO_TICKS(50)); /* let the printer drain what is queued */
        ry_out("OK RX stop ok=%lu bad=%lu other=%lu ovf=%lu", (unsigned long)g_rx_cnt.ok,
               (unsigned long)g_rx_cnt.bad, (unsigned long)g_rx_cnt.other, (unsigned long)g_rx_cnt.ovf);
    } else {
        ry_out("ERR RX SYNTAX expected 'RX start' or 'RX stop'");
    }
#else
    (void)c;
    ry_out("ERR RX ROLE this board is a DUT");
#endif
}

static void dispatch(const char *line)
{
    ry_cmd_t c;
    int rc = ry_cmd_parse(line, &c);
    if (rc != RY_OK) {
        ry_out("ERR ? %s", ry_status_str(rc));
        return;
    }
    if (strcmp(c.verb, "INFO") == 0) cmd_info();
    else if (strcmp(c.verb, "PING") == 0) ry_out("OK PING t_us=%lld", (long long)esp_timer_get_time());
    else if (strcmp(c.verb, "SET") == 0) cmd_set(&c);
    else if (strcmp(c.verb, "TX") == 0) cmd_tx(&c);
    else if (strcmp(c.verb, "STOP") == 0) cmd_stop();
    else if (strcmp(c.verb, "RX") == 0) cmd_rx(&c);
    else ry_out("ERR %s UNKNOWN verb", c.verb);
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init(); /* WiFi/PHY calibration data */
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    esp_log_level_set("*", ESP_LOG_WARN); /* keep the protocol stream clean */
    ESP_ERROR_CHECK(esp_read_mac(g_mac, ESP_MAC_WIFI_STA));
    g_dut_tag = (uint16_t)((g_mac[4] << 8) | g_mac[5]);
    ry_console_init();
    ry_rx_init();
#if CONFIG_RY_ROLE_DUT
    s_tx_q = xQueueCreate(1, sizeof(tx_job_t));
    xTaskCreate(tx_task, "ry_tx", 4096, NULL, 5, NULL);
#endif
    ry_out("BOOT role=%s fw=%s proto=%d", RY_ROLE_NAME, RY_FW_VERSION, RY_PROTO_VERSION);
    static char line[RY_LINE_MAX + 2];
    for (;;) {
        int n = ry_console_readline(line, sizeof(line));
        if (n < 0) {
            ry_out("ERR ? TOOLONG");
            continue;
        }
        /* skip blank lines silently (host may send a bare newline to resync) */
        bool blank = true;
        for (int i = 0; i < n; i++)
            if (line[i] != ' ' && line[i] != '\r' && line[i] != '\n' && line[i] != '\t') blank = false;
        if (!blank) dispatch(line);
    }
}
