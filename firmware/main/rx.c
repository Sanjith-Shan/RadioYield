/* REF receive pipeline: radio callbacks -> queue -> one printer task -> "RX,..." lines.
 * Every drop on the REF side (queue full) is counted in g_rx_cnt.ovf and reported to
 * the host, so REF logging loss is never confused with RF packet loss. */
#include <string.h>

#include "esp_attr.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "ry_app.h"

#define RX_QUEUE_LEN 128

typedef enum { ITEM_PKT = 0, ITEM_RAW154 = 1 } item_kind_t;

typedef struct {
    uint8_t kind;
    int8_t rssi;
    uint8_t rx_ch;
    uint8_t lqi_raw;
    int16_t lqi;
    int64_t t_us;
    union {
        ry_pkt_t pkt;
        uint8_t frame[RY_154_MAX_PSDU + 1];
    } u;
} rx_item_t;

ry_rx_counters_t g_rx_cnt;
volatile bool g_rx_enabled;
volatile uint8_t g_rx_radio;
static QueueHandle_t s_q;

void ry_rx_reset_counters(void)
{
    memset((void *)&g_rx_cnt, 0, sizeof(g_rx_cnt));
}

void ry_rx_push_pkt(const ry_pkt_t *p, int8_t rssi, uint8_t rx_ch, int16_t lqi)
{
    if (!g_rx_enabled) return;
    if (p->radio != g_rx_radio) {
        g_rx_cnt.other++;
        return;
    }
    rx_item_t it;
    it.kind = ITEM_PKT;
    it.rssi = rssi;
    it.rx_ch = rx_ch;
    it.lqi = lqi;
    it.t_us = esp_timer_get_time();
    it.u.pkt = *p;
    if (xQueueSend(s_q, &it, 0) != pdTRUE) g_rx_cnt.ovf++;
}

void IRAM_ATTR ry_rx_push_154_from_isr(const uint8_t *frame, int8_t rssi, uint8_t lqi, uint8_t ch)
{
    if (!g_rx_enabled) return;
    static rx_item_t it; /* ISR is not re-entrant for this radio; keep it off the ISR stack */
    uint8_t n = frame[0];
    if (n > RY_154_MAX_PSDU) n = RY_154_MAX_PSDU;
    it.kind = ITEM_RAW154;
    it.rssi = rssi;
    it.rx_ch = ch;
    it.lqi_raw = lqi;
    it.t_us = 0; /* stamped in the task */
    memcpy(it.u.frame, frame, (size_t)n + 1);
    it.u.frame[0] = n;
    BaseType_t woken = pdFALSE;
    if (xQueueSendFromISR(s_q, &it, &woken) != pdTRUE) g_rx_cnt.ovf++;
    if (woken) portYIELD_FROM_ISR();
}

static void rx_task(void *arg)
{
    (void)arg;
    rx_item_t it;
    char line[RY_LINE_MAX];
    for (;;) {
        if (xQueueReceive(s_q, &it, portMAX_DELAY) != pdTRUE) continue;
        ry_rx_rec_t r;
        memset(&r, 0, sizeof(r));
        if (it.kind == ITEM_RAW154) {
            ry_pkt_t p;
            int rc = ry_154_frame_parse(it.u.frame, sizeof(it.u.frame), &p);
            if (rc != RY_OK) {
                if (rc == RY_ERR_CRC || rc == RY_ERR_VERSION || rc == RY_ERR_FIELD) g_rx_cnt.bad++;
                else g_rx_cnt.other++;
                continue;
            }
            if (p.radio != RY_RADIO_154) {
                g_rx_cnt.other++;
                continue;
            }
            it.u.pkt = p;
            it.lqi = it.lqi_raw;
            it.t_us = esp_timer_get_time();
        }
        const ry_pkt_t *p = &it.u.pkt;
        r.radio = p->radio;
        r.rx_ch = it.rx_ch;
        r.tx_ch = p->channel;
        r.run_id = p->run_id;
        r.seq = p->seq;
        r.rssi = it.rssi;
        r.pwr_qdbm = p->pwr_qdbm;
        r.t_us = (uint64_t)it.t_us;
        r.lqi = it.lqi;
        r.dut_tag = p->dut_tag;
        if (ry_rx_line_format(&r, line, sizeof(line)) > 0) {
            g_rx_cnt.ok++;
            ry_out("%s", line);
        }
    }
}

void ry_rx_init(void)
{
    s_q = xQueueCreate(RX_QUEUE_LEN, sizeof(rx_item_t));
    configASSERT(s_q);
    xTaskCreate(rx_task, "ry_rx", 4096, NULL, 6, NULL);
}
