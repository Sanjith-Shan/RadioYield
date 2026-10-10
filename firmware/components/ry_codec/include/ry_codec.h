/*
 * ry_codec: portable packet + serial-line codec for the RadioYield rig firmware.
 *
 * Pure C99, no allocation, no ESP-IDF dependency. Compiled into the firmware as an
 * IDF component and on the host by firmware/host_tests (ctest). The Python
 * collector (radioyield/collector/protocol.py) implements the same formats; the
 * golden vectors in firmware/host_tests/golden_rx_lines.txt keep the two in step.
 *
 * Specified in firmware/PROTOCOL.md. Keep that file and this one in sync.
 */
#ifndef RY_CODEC_H
#define RY_CODEC_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- versions ------------------------------------------------------------ */
#define RY_PROTO_VERSION 1 /* serial line protocol version, reported by INFO */
#define RY_PKT_VERSION 1   /* over-the-air payload version */

/* ---- result codes -------------------------------------------------------- */
typedef enum {
    RY_OK = 0,
    RY_ERR_ARG = -1,      /* NULL pointer or impossible buffer size */
    RY_ERR_SHORT = -2,    /* input shorter than the minimum */
    RY_ERR_MAGIC = -3,    /* not a RadioYield packet */
    RY_ERR_VERSION = -4,  /* unknown payload version */
    RY_ERR_CRC = -5,      /* CRC mismatch */
    RY_ERR_FIELD = -6,    /* a field holds an invalid value */
    RY_ERR_SYNTAX = -7,   /* malformed text line */
    RY_ERR_TOOLONG = -8,  /* line / token too long */
    RY_ERR_MISSING = -9,  /* required key absent */
    RY_ERR_RANGE = -10,   /* value out of range */
    RY_ERR_CHECKSUM = -11,/* *hh line checksum mismatch or absent */
    RY_ERR_NOTFOUND = -12,/* no RadioYield AD structure / frame */
    RY_ERR_DUP = -13      /* duplicate key */
} ry_status_t;

const char *ry_status_str(int status);

/* ---- radios ---------------------------------------------------------------- */
typedef enum {
    RY_RADIO_NONE = 0,
    RY_RADIO_WIFI = 1, /* ESP-NOW (802.11 vendor action frames), channels 1..13 */
    RY_RADIO_BLE = 2,  /* BLE legacy non-connectable advertising, 37/38/39 */
    RY_RADIO_154 = 3   /* IEEE 802.15.4 raw data frames, channels 11..26 */
} ry_radio_t;

/* "wifi", "ble", "154", "none" */
const char *ry_radio_name(ry_radio_t r);
/* Parses the names above; returns RY_OK or RY_ERR_FIELD. */
int ry_radio_parse(const char *s, ry_radio_t *out);

/* BLE: channel 0 in a packet / command means "all three advertising channels". */
#define RY_BLE_CH_ALL 0

/* ---- over-the-air payload --------------------------------------------------
 * Little-endian, 18 bytes, followed by optional deterministic padding:
 *   off len field
 *   0   2   magic 'R' 'Y'
 *   2   1   version (RY_PKT_VERSION)
 *   3   1   radio (ry_radio_t)
 *   4   1   channel commanded on the DUT (BLE: 37/38/39, 0 = all three)
 *   5   1   flags (bit0: padded; others reserved, 0)
 *   6   2   run_id  (host-chosen per sweep step; lets REF/host discard stale packets)
 *   8   4   seq     (0..n-1 within a run_id)
 *   12  2   pwr_qdbm (int16, commanded TX power in 0.25 dBm units)
 *   14  2   dut_tag (low 16 bits of the DUT's WiFi STA MAC)
 *   16  2   crc16   CRC-16/CCITT-FALSE over bytes 0..15
 *   18  ..  padding byte i (i >= 18) = (uint8_t)(seq + i); not covered by the CRC
 *           (the radio's own FCS covers it), checked by ry_pkt_decode as a soft flag.
 */
#define RY_PKT_HDR_LEN 18
#define RY_PKT_MAX_LEN 250 /* ESP-NOW v1 limit; 802.15.4 limits further (see below) */
#define RY_PKT_FLAG_PADDED 0x01

typedef struct {
    uint8_t radio;     /* ry_radio_t */
    uint8_t channel;
    uint8_t flags;
    uint16_t run_id;
    uint32_t seq;
    int16_t pwr_qdbm;
    uint16_t dut_tag;
} ry_pkt_t;

uint16_t ry_crc16_ccitt(const uint8_t *data, size_t len);

/* Encodes p into buf, padded to total_len bytes (RY_PKT_HDR_LEN..RY_PKT_MAX_LEN).
 * Returns the number of bytes written (== total_len) or a negative ry_status_t. */
int ry_pkt_encode(const ry_pkt_t *p, size_t total_len, uint8_t *buf, size_t buflen);

/* Decodes buf. Returns RY_OK or a negative ry_status_t. *pad_ok (optional) is set to
 * 1 when any padding bytes match the deterministic pattern, else 0. */
int ry_pkt_decode(const uint8_t *buf, size_t len, ry_pkt_t *out, int *pad_ok);

/* ---- BLE advertising data -------------------------------------------------
 * One AD structure: [len=21][0xFF manufacturer specific][0xFF 0xFF company id
 * (reserved for testing)][18-byte payload]. 22 bytes, fits legacy 31-byte AD. */
#define RY_BLE_COMPANY_ID 0xFFFF
#define RY_BLE_ADV_LEN (2 + 2 + RY_PKT_HDR_LEN)
int ry_ble_adv_build(const ry_pkt_t *p, uint8_t *buf, size_t buflen);
/* Walks the AD structures of a received advertisement; returns RY_OK and fills out
 * when a valid RadioYield payload is found, RY_ERR_NOTFOUND if none, or another
 * negative code if the AD structure list is malformed. */
int ry_ble_adv_find(const uint8_t *adv, size_t len, ry_pkt_t *out);

/* ---- IEEE 802.15.4 frame ---------------------------------------------------
 * PHY length byte + MHR (data frame, no security, no ACK request, PAN ID
 * compression, short dst 0xFFFF, short src) + RadioYield payload + 2-byte FCS slot.
 * Layout passed to esp_ieee802154_transmit(): [len][fcf 2][dsn][pan 2][dst 2][src 2]
 * [payload][fcs 2], len = 9 + payload + 2 <= 127. */
#define RY_154_MHR_LEN 9
#define RY_154_MAX_PSDU 127
#define RY_154_MAX_PAYLOAD (RY_154_MAX_PSDU - RY_154_MHR_LEN - 2) /* 116 */
int ry_154_frame_build(const ry_pkt_t *p, size_t payload_len, uint16_t pan_id, uint16_t src_short,
                       uint8_t *frame, size_t buflen);
/* frame[0] is the PHY length byte, as delivered by esp_ieee802154_receive_done()
 * (the 2 FCS bytes there hold RSSI/LQI, so they are ignored). */
int ry_154_frame_parse(const uint8_t *frame, size_t buflen, ry_pkt_t *out);

/* ---- serial command lines (host -> device) ---------------------------------
 *   VERB [arg] [key=value ...]
 * VERB: 1..11 chars [A-Z]. arg: one bare token [a-z0-9_]. key: [a-z0-9_]{1,15}.
 * value: [A-Za-z0-9_.:+-]{1,31}. Single spaces or tabs between tokens; leading and
 * trailing whitespace and a trailing CR/LF are ignored. Max line length RY_LINE_MAX.
 */
#define RY_LINE_MAX 160
#define RY_VERB_MAX 12
#define RY_KEY_MAX 16
#define RY_VAL_MAX 32
#define RY_ARG_MAX 16
#define RY_MAX_KV 12

typedef struct {
    char key[RY_KEY_MAX];
    char val[RY_VAL_MAX];
} ry_kv_t;

typedef struct {
    char verb[RY_VERB_MAX];
    char arg[RY_ARG_MAX]; /* "" when absent */
    int nkv;
    ry_kv_t kv[RY_MAX_KV];
} ry_cmd_t;

int ry_cmd_parse(const char *line, ry_cmd_t *out);
/* Returns the value for key or NULL. */
const char *ry_cmd_get(const ry_cmd_t *c, const char *key);
/* Strict base-10 integer: RY_OK, RY_ERR_MISSING (absent), RY_ERR_SYNTAX (not an
 * integer), RY_ERR_RANGE (outside [lo, hi]). If absent and def_ok, *out = def. */
int ry_cmd_get_int(const ry_cmd_t *c, const char *key, long lo, long hi, int def_ok, long def,
                   long *out);

/* ---- RX record lines (REF -> host) ------------------------------------------
 *   RX,<radio>,<rx_ch>,<tx_ch>,<run_id>,<seq>,<rssi>,<pwr_qdbm>,<t_us>,<lqi>,<tag>*hh
 * radio: wifi|ble|154; rx_ch: channel the REF radio reported (0 = not reported by the
 * stack, the BLE case); tx_ch: channel from the payload; rssi: dBm; lqi: -1 if n/a;
 * tag: 4 lowercase hex digits; hh: 2 uppercase hex digits, the XOR of every byte
 * before the '*' (the whole "RX,...,<tag>" text), NMEA style.
 */
typedef struct {
    uint8_t radio;
    uint8_t rx_ch;
    uint8_t tx_ch;
    uint16_t run_id;
    uint32_t seq;
    int16_t rssi;
    int16_t pwr_qdbm;
    uint64_t t_us;
    int16_t lqi;
    uint16_t dut_tag;
} ry_rx_rec_t;

uint8_t ry_line_xor(const char *s, size_t n);
/* Returns the line length (excluding NUL) or a negative ry_status_t. No newline. */
int ry_rx_line_format(const ry_rx_rec_t *r, char *buf, size_t buflen);
int ry_rx_line_parse(const char *line, ry_rx_rec_t *out);

#ifdef __cplusplus
}
#endif
#endif /* RY_CODEC_H */
