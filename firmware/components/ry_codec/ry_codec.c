/* ry_codec.c: see include/ry_codec.h and firmware/PROTOCOL.md. */
#include "ry_codec.h"

#include <stdio.h>
#include <string.h>

const char *ry_status_str(int s)
{
    switch (s) {
    case RY_OK: return "OK";
    case RY_ERR_ARG: return "ARG";
    case RY_ERR_SHORT: return "SHORT";
    case RY_ERR_MAGIC: return "MAGIC";
    case RY_ERR_VERSION: return "VERSION";
    case RY_ERR_CRC: return "CRC";
    case RY_ERR_FIELD: return "FIELD";
    case RY_ERR_SYNTAX: return "SYNTAX";
    case RY_ERR_TOOLONG: return "TOOLONG";
    case RY_ERR_MISSING: return "MISSING";
    case RY_ERR_RANGE: return "RANGE";
    case RY_ERR_CHECKSUM: return "CHECKSUM";
    case RY_ERR_NOTFOUND: return "NOTFOUND";
    case RY_ERR_DUP: return "DUP";
    default: return "UNKNOWN";
    }
}

const char *ry_radio_name(ry_radio_t r)
{
    switch (r) {
    case RY_RADIO_WIFI: return "wifi";
    case RY_RADIO_BLE: return "ble";
    case RY_RADIO_154: return "154";
    default: return "none";
    }
}

int ry_radio_parse(const char *s, ry_radio_t *out)
{
    if (!s || !out) return RY_ERR_ARG;
    if (strcmp(s, "wifi") == 0) *out = RY_RADIO_WIFI;
    else if (strcmp(s, "ble") == 0) *out = RY_RADIO_BLE;
    else if (strcmp(s, "154") == 0) *out = RY_RADIO_154;
    else if (strcmp(s, "none") == 0) *out = RY_RADIO_NONE;
    else return RY_ERR_FIELD;
    return RY_OK;
}

/* ---- payload ------------------------------------------------------------- */

uint16_t ry_crc16_ccitt(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
    }
    return crc;
}

static void put16(uint8_t *b, uint16_t v) { b[0] = (uint8_t)v; b[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *b, uint32_t v)
{
    b[0] = (uint8_t)v; b[1] = (uint8_t)(v >> 8); b[2] = (uint8_t)(v >> 16); b[3] = (uint8_t)(v >> 24);
}
static uint16_t get16(const uint8_t *b) { return (uint16_t)(b[0] | (b[1] << 8)); }
static uint32_t get32(const uint8_t *b)
{
    return (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
}

static int radio_valid(uint8_t r) { return r >= RY_RADIO_WIFI && r <= RY_RADIO_154; }

int ry_pkt_encode(const ry_pkt_t *p, size_t total_len, uint8_t *buf, size_t buflen)
{
    if (!p || !buf) return RY_ERR_ARG;
    if (total_len < RY_PKT_HDR_LEN || total_len > RY_PKT_MAX_LEN) return RY_ERR_RANGE;
    if (buflen < total_len) return RY_ERR_ARG;
    if (!radio_valid(p->radio)) return RY_ERR_FIELD;
    buf[0] = 'R';
    buf[1] = 'Y';
    buf[2] = RY_PKT_VERSION;
    buf[3] = p->radio;
    buf[4] = p->channel;
    buf[5] = (uint8_t)((p->flags & (uint8_t)~RY_PKT_FLAG_PADDED) |
                       (total_len > RY_PKT_HDR_LEN ? RY_PKT_FLAG_PADDED : 0));
    put16(buf + 6, p->run_id);
    put32(buf + 8, p->seq);
    put16(buf + 12, (uint16_t)p->pwr_qdbm);
    put16(buf + 14, p->dut_tag);
    put16(buf + 16, ry_crc16_ccitt(buf, 16));
    for (size_t i = RY_PKT_HDR_LEN; i < total_len; i++) buf[i] = (uint8_t)(p->seq + i);
    return (int)total_len;
}

int ry_pkt_decode(const uint8_t *buf, size_t len, ry_pkt_t *out, int *pad_ok)
{
    if (!buf || !out) return RY_ERR_ARG;
    if (len < RY_PKT_HDR_LEN) return RY_ERR_SHORT;
    if (buf[0] != 'R' || buf[1] != 'Y') return RY_ERR_MAGIC;
    if (buf[2] != RY_PKT_VERSION) return RY_ERR_VERSION;
    if (get16(buf + 16) != ry_crc16_ccitt(buf, 16)) return RY_ERR_CRC;
    if (!radio_valid(buf[3])) return RY_ERR_FIELD;
    out->radio = buf[3];
    out->channel = buf[4];
    out->flags = buf[5];
    out->run_id = get16(buf + 6);
    out->seq = get32(buf + 8);
    out->pwr_qdbm = (int16_t)get16(buf + 12);
    out->dut_tag = get16(buf + 14);
    if (pad_ok) {
        int ok = 1;
        size_t n = len > RY_PKT_MAX_LEN ? RY_PKT_MAX_LEN : len;
        for (size_t i = RY_PKT_HDR_LEN; i < n; i++)
            if (buf[i] != (uint8_t)(out->seq + i)) { ok = 0; break; }
        *pad_ok = ok;
    }
    return RY_OK;
}

/* ---- BLE ------------------------------------------------------------------ */

int ry_ble_adv_build(const ry_pkt_t *p, uint8_t *buf, size_t buflen)
{
    if (!p || !buf) return RY_ERR_ARG;
    if (buflen < RY_BLE_ADV_LEN) return RY_ERR_ARG;
    buf[0] = (uint8_t)(RY_BLE_ADV_LEN - 1); /* length covers type + data */
    buf[1] = 0xFF;                          /* manufacturer specific data */
    put16(buf + 2, RY_BLE_COMPANY_ID);
    int n = ry_pkt_encode(p, RY_PKT_HDR_LEN, buf + 4, buflen - 4);
    if (n < 0) return n;
    return RY_BLE_ADV_LEN;
}

int ry_ble_adv_find(const uint8_t *adv, size_t len, ry_pkt_t *out)
{
    if (!adv || !out) return RY_ERR_ARG;
    size_t i = 0;
    int last_err = RY_ERR_NOTFOUND;
    while (i < len) {
        uint8_t l = adv[i];
        if (l == 0) break; /* early terminator / zero padding (Core spec Vol 3 Part C 11) */
        if (i + 1 + l > len) return RY_ERR_SYNTAX;
        const uint8_t *ad = adv + i + 1; /* ad[0] = type */
        if (ad[0] == 0xFF && l >= 3 && get16(ad + 1) == RY_BLE_COMPANY_ID) {
            int rc = ry_pkt_decode(ad + 3, (size_t)l - 3, out, NULL);
            if (rc == RY_OK) return RY_OK;
            last_err = rc;
        }
        i += 1 + (size_t)l;
    }
    return last_err;
}

/* ---- 802.15.4 --------------------------------------------------------------- */

#define FCF_DATA_PANCOMP_SHORT 0x8841u /* type data, PAN ID compression, dst/src short, v2003 */

int ry_154_frame_build(const ry_pkt_t *p, size_t payload_len, uint16_t pan_id, uint16_t src_short,
                       uint8_t *frame, size_t buflen)
{
    if (!p || !frame) return RY_ERR_ARG;
    if (payload_len < RY_PKT_HDR_LEN || payload_len > RY_154_MAX_PAYLOAD) return RY_ERR_RANGE;
    size_t psdu = RY_154_MHR_LEN + payload_len + 2;
    if (buflen < psdu + 1) return RY_ERR_ARG;
    frame[0] = (uint8_t)psdu;
    put16(frame + 1, FCF_DATA_PANCOMP_SHORT);
    frame[3] = (uint8_t)p->seq; /* MAC DSN: low byte of seq, informational only */
    put16(frame + 4, pan_id);
    put16(frame + 6, 0xFFFF);
    put16(frame + 8, src_short);
    int n = ry_pkt_encode(p, payload_len, frame + 1 + RY_154_MHR_LEN, buflen - 1 - RY_154_MHR_LEN);
    if (n < 0) return n;
    frame[1 + RY_154_MHR_LEN + payload_len] = 0; /* FCS placeholder; hardware computes it */
    frame[2 + RY_154_MHR_LEN + payload_len] = 0;
    return (int)(psdu + 1);
}

int ry_154_frame_parse(const uint8_t *frame, size_t buflen, ry_pkt_t *out)
{
    if (!frame || !out) return RY_ERR_ARG;
    if (buflen < 1) return RY_ERR_SHORT;
    size_t psdu = frame[0];
    if (psdu > RY_154_MAX_PSDU || psdu + 1 > buflen) return RY_ERR_SYNTAX;
    if (psdu < RY_154_MHR_LEN + 2 + RY_PKT_HDR_LEN) return RY_ERR_SHORT;
    if (get16(frame + 1) != FCF_DATA_PANCOMP_SHORT) return RY_ERR_NOTFOUND;
    return ry_pkt_decode(frame + 1 + RY_154_MHR_LEN, psdu - RY_154_MHR_LEN - 2, out, NULL);
}

/* ---- command lines ------------------------------------------------------------ */

static int is_upper(char c) { return c >= 'A' && c <= 'Z'; }
static int is_keych(char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_'; }
static int is_valch(char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' ||
           c == '.' || c == ':' || c == '+' || c == '-';
}
static int is_ws(char c) { return c == ' ' || c == '\t'; }

int ry_cmd_parse(const char *line, ry_cmd_t *out)
{
    if (!line || !out) return RY_ERR_ARG;
    memset(out, 0, sizeof(*out));
    size_t n = strlen(line);
    if (n > RY_LINE_MAX) return RY_ERR_TOOLONG;
    while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r' || is_ws(line[n - 1]))) n--;
    size_t i = 0;
    while (i < n && is_ws(line[i])) i++;
    if (i >= n) return RY_ERR_SYNTAX; /* empty */

    size_t v = 0;
    while (i < n && !is_ws(line[i])) {
        if (!is_upper(line[i])) return RY_ERR_SYNTAX;
        if (v + 1 >= RY_VERB_MAX) return RY_ERR_TOOLONG;
        out->verb[v++] = line[i++];
    }
    int seen_kv = 0;
    while (i < n) {
        while (i < n && is_ws(line[i])) i++;
        if (i >= n) break;
        size_t s = i;
        while (i < n && !is_ws(line[i])) i++;
        const char *tok = line + s;
        size_t tl = i - s;
        const char *eq = memchr(tok, '=', tl);
        if (!eq) {
            /* bare argument: only one, and only before any key=value */
            if (out->arg[0] || seen_kv) return RY_ERR_SYNTAX;
            if (tl >= RY_ARG_MAX) return RY_ERR_TOOLONG;
            for (size_t k = 0; k < tl; k++)
                if (!is_keych(tok[k])) return RY_ERR_SYNTAX;
            memcpy(out->arg, tok, tl);
            continue;
        }
        size_t kl = (size_t)(eq - tok), vl = tl - kl - 1;
        if (kl == 0 || vl == 0) return RY_ERR_SYNTAX;
        if (kl >= RY_KEY_MAX || vl >= RY_VAL_MAX) return RY_ERR_TOOLONG;
        for (size_t k = 0; k < kl; k++)
            if (!is_keych(tok[k])) return RY_ERR_SYNTAX;
        for (size_t k = 0; k < vl; k++)
            if (!is_valch(eq[1 + k])) return RY_ERR_SYNTAX;
        if (out->nkv >= RY_MAX_KV) return RY_ERR_TOOLONG;
        ry_kv_t *kv = &out->kv[out->nkv];
        memcpy(kv->key, tok, kl);
        kv->key[kl] = 0;
        if (ry_cmd_get(out, kv->key)) return RY_ERR_DUP;
        memcpy(kv->val, eq + 1, vl);
        kv->val[vl] = 0;
        out->nkv++;
        seen_kv = 1;
    }
    return RY_OK;
}

const char *ry_cmd_get(const ry_cmd_t *c, const char *key)
{
    if (!c || !key) return NULL;
    for (int i = 0; i < c->nkv; i++)
        if (strcmp(c->kv[i].key, key) == 0) return c->kv[i].val;
    return NULL;
}

static int parse_long(const char *s, long *out)
{
    /* strict: optional sign, 1..10 digits, nothing else (no strtol locale/overflow quirks) */
    int neg = 0;
    size_t i = 0;
    if (s[0] == '-' || s[0] == '+') { neg = s[0] == '-'; i = 1; }
    if (s[i] == 0) return RY_ERR_SYNTAX;
    long long v = 0;
    size_t digits = 0;
    for (; s[i]; i++) {
        if (s[i] < '0' || s[i] > '9') return RY_ERR_SYNTAX;
        if (++digits > 10) return RY_ERR_RANGE;
        v = v * 10 + (s[i] - '0');
    }
    if (neg) v = -v;
    if (v > 2147483647LL || v < -2147483648LL) return RY_ERR_RANGE;
    *out = (long)v;
    return RY_OK;
}

int ry_cmd_get_int(const ry_cmd_t *c, const char *key, long lo, long hi, int def_ok, long def, long *out)
{
    if (!c || !key || !out) return RY_ERR_ARG;
    const char *s = ry_cmd_get(c, key);
    if (!s) {
        if (def_ok) { *out = def; return RY_OK; }
        return RY_ERR_MISSING;
    }
    long v;
    int rc = parse_long(s, &v);
    if (rc != RY_OK) return rc;
    if (v < lo || v > hi) return RY_ERR_RANGE;
    *out = v;
    return RY_OK;
}

/* ---- RX lines ------------------------------------------------------------------ */

uint8_t ry_line_xor(const char *s, size_t n)
{
    uint8_t x = 0;
    for (size_t i = 0; i < n; i++) x ^= (uint8_t)s[i];
    return x;
}

int ry_rx_line_format(const ry_rx_rec_t *r, char *buf, size_t buflen)
{
    if (!r || !buf || buflen == 0) return RY_ERR_ARG;
    if (!radio_valid(r->radio)) return RY_ERR_FIELD;
    int n = snprintf(buf, buflen, "RX,%s,%u,%u,%u,%lu,%d,%d,%llu,%d,%04x", ry_radio_name((ry_radio_t)r->radio),
                     (unsigned)r->rx_ch, (unsigned)r->tx_ch, (unsigned)r->run_id, (unsigned long)r->seq,
                     (int)r->rssi, (int)r->pwr_qdbm, (unsigned long long)r->t_us, (int)r->lqi,
                     (unsigned)r->dut_tag);
    if (n < 0 || (size_t)n + 3 >= buflen) return RY_ERR_TOOLONG;
    uint8_t x = ry_line_xor(buf, (size_t)n);
    int m = snprintf(buf + n, buflen - (size_t)n, "*%02X", x);
    if (m != 3) return RY_ERR_TOOLONG;
    return n + 3;
}

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

/* Parses an unsigned decimal field into *v, max 20 digits; rejects empty / junk. */
static int field_u64(const char *s, size_t n, unsigned long long max, unsigned long long *v)
{
    if (n == 0 || n > 20) return RY_ERR_SYNTAX;
    unsigned long long acc = 0;
    for (size_t i = 0; i < n; i++) {
        if (s[i] < '0' || s[i] > '9') return RY_ERR_SYNTAX;
        unsigned long long d = (unsigned long long)(s[i] - '0');
        if (acc > (max - d) / 10) return RY_ERR_RANGE;
        acc = acc * 10 + d;
    }
    *v = acc;
    return RY_OK;
}

static int field_i32(const char *s, size_t n, long lo, long hi, long *v)
{
    int neg = 0;
    if (n > 0 && s[0] == '-') { neg = 1; s++; n--; }
    unsigned long long u;
    int rc = field_u64(s, n, 2147483648ULL, &u);
    if (rc) return rc;
    long long x = neg ? -(long long)u : (long long)u;
    if (x < lo || x > hi) return RY_ERR_RANGE;
    *v = (long)x;
    return RY_OK;
}

int ry_rx_line_parse(const char *line, ry_rx_rec_t *out)
{
    if (!line || !out) return RY_ERR_ARG;
    size_t n = strlen(line);
    if (n > RY_LINE_MAX) return RY_ERR_TOOLONG;
    while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r')) n--;
    if (n < 3 || memcmp(line, "RX,", 3) != 0) return RY_ERR_SYNTAX;
    if (n < 4 || line[n - 3] != '*') return RY_ERR_CHECKSUM;
    int h1 = hexval(line[n - 2]), h0 = hexval(line[n - 1]);
    if (h1 < 0 || h0 < 0) return RY_ERR_CHECKSUM;
    size_t body = n - 3;
    if (ry_line_xor(line, body) != (uint8_t)(h1 * 16 + h0)) return RY_ERR_CHECKSUM;

    /* split body on ',' into exactly 11 fields */
    const char *f[11];
    size_t fl[11];
    int nf = 0;
    size_t s = 0;
    for (size_t i = 0; i <= body; i++) {
        if (i == body || line[i] == ',') {
            if (nf >= 11) return RY_ERR_SYNTAX;
            f[nf] = line + s;
            fl[nf] = i - s;
            nf++;
            s = i + 1;
        }
    }
    if (nf != 11) return RY_ERR_SYNTAX;

    ry_rx_rec_t r;
    memset(&r, 0, sizeof(r));
    char rname[8];
    if (fl[1] == 0 || fl[1] >= sizeof(rname)) return RY_ERR_FIELD;
    memcpy(rname, f[1], fl[1]);
    rname[fl[1]] = 0;
    ry_radio_t radio;
    if (ry_radio_parse(rname, &radio) != RY_OK || radio == RY_RADIO_NONE) return RY_ERR_FIELD;
    r.radio = (uint8_t)radio;

    unsigned long long u;
    long l;
    int rc;
    if ((rc = field_u64(f[2], fl[2], 255, &u))) return rc;
    r.rx_ch = (uint8_t)u;
    if ((rc = field_u64(f[3], fl[3], 255, &u))) return rc;
    r.tx_ch = (uint8_t)u;
    if ((rc = field_u64(f[4], fl[4], 65535, &u))) return rc;
    r.run_id = (uint16_t)u;
    if ((rc = field_u64(f[5], fl[5], 4294967295ULL, &u))) return rc;
    r.seq = (uint32_t)u;
    if ((rc = field_i32(f[6], fl[6], -128, 127, &l))) return rc;
    r.rssi = (int16_t)l;
    if ((rc = field_i32(f[7], fl[7], -32768, 32767, &l))) return rc;
    r.pwr_qdbm = (int16_t)l;
    if ((rc = field_u64(f[8], fl[8], ~0ULL, &u))) return rc;
    r.t_us = u;
    if ((rc = field_i32(f[9], fl[9], -1, 255, &l))) return rc;
    r.lqi = (int16_t)l;
    if (fl[10] != 4) return RY_ERR_FIELD;
    unsigned tag = 0;
    for (int k = 0; k < 4; k++) {
        int h = hexval(f[10][k]);
        if (h < 0) return RY_ERR_FIELD;
        tag = tag * 16 + (unsigned)h;
    }
    r.dut_tag = (uint16_t)tag;
    *out = r;
    return RY_OK;
}
