/* Host unit tests for ry_codec. Run via ctest (see CMakeLists.txt in this dir). */
#include "ry_codec.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_fail = 0, g_checks = 0;

#define CHECK(cond)                                                                 \
    do {                                                                            \
        g_checks++;                                                                 \
        if (!(cond)) {                                                              \
            g_fail++;                                                               \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                           \
    } while (0)
#define CHECK_EQ(a, b) CHECK((long long)(a) == (long long)(b))

static ry_pkt_t sample_pkt(void)
{
    ry_pkt_t p;
    memset(&p, 0, sizeof(p));
    p.radio = RY_RADIO_WIFI;
    p.channel = 6;
    p.run_id = 0xBEEF;
    p.seq = 123456789u;
    p.pwr_qdbm = -60;
    p.dut_tag = 0x1a2b;
    return p;
}

static void test_crc(void)
{
    /* CRC-16/CCITT-FALSE check value */
    CHECK_EQ(ry_crc16_ccitt((const uint8_t *)"123456789", 9), 0x29B1);
    CHECK_EQ(ry_crc16_ccitt(NULL, 0), 0xFFFF);
}

static void test_pkt_roundtrip(void)
{
    uint8_t buf[RY_PKT_MAX_LEN];
    for (size_t len = RY_PKT_HDR_LEN; len <= RY_PKT_MAX_LEN; len++) {
        ry_pkt_t p = sample_pkt(), q;
        p.seq = (uint32_t)len * 7919u;
        int n = ry_pkt_encode(&p, len, buf, sizeof(buf));
        CHECK_EQ(n, (long long)len);
        int pad_ok = -1;
        CHECK_EQ(ry_pkt_decode(buf, len, &q, &pad_ok), RY_OK);
        CHECK_EQ(pad_ok, 1);
        CHECK_EQ(q.seq, p.seq);
        CHECK_EQ(q.run_id, p.run_id);
        CHECK_EQ(q.pwr_qdbm, p.pwr_qdbm);
        CHECK_EQ(q.channel, p.channel);
        CHECK_EQ(q.dut_tag, p.dut_tag);
        CHECK_EQ(q.radio, p.radio);
        CHECK_EQ(q.flags & RY_PKT_FLAG_PADDED, len > RY_PKT_HDR_LEN ? 1 : 0);
    }
    /* extremes */
    ry_pkt_t p = sample_pkt(), q;
    p.seq = 0xFFFFFFFFu;
    p.pwr_qdbm = 32767;
    p.run_id = 0;
    p.radio = RY_RADIO_154;
    CHECK_EQ(ry_pkt_encode(&p, RY_PKT_HDR_LEN, buf, sizeof(buf)), RY_PKT_HDR_LEN);
    CHECK_EQ(ry_pkt_decode(buf, RY_PKT_HDR_LEN, &q, NULL), RY_OK);
    CHECK_EQ(q.seq, 0xFFFFFFFFu);
    CHECK_EQ(q.pwr_qdbm, 32767);
    p.pwr_qdbm = -32768;
    ry_pkt_encode(&p, RY_PKT_HDR_LEN, buf, sizeof(buf));
    ry_pkt_decode(buf, RY_PKT_HDR_LEN, &q, NULL);
    CHECK_EQ(q.pwr_qdbm, -32768);
    /* byte layout is little-endian and fixed */
    p = sample_pkt();
    ry_pkt_encode(&p, RY_PKT_HDR_LEN, buf, sizeof(buf));
    CHECK_EQ(buf[0], 'R');
    CHECK_EQ(buf[1], 'Y');
    CHECK_EQ(buf[2], RY_PKT_VERSION);
    CHECK_EQ(buf[3], RY_RADIO_WIFI);
    CHECK_EQ(buf[6], 0xEF);
    CHECK_EQ(buf[7], 0xBE);
    CHECK_EQ(buf[8], 0x15); /* 123456789 = 0x075BCD15 */
    CHECK_EQ(buf[11], 0x07);
    CHECK_EQ(buf[12], 0xC4); /* -60 = 0xFFC4 */
    CHECK_EQ(buf[13], 0xFF);
}

static void test_pkt_errors(void)
{
    uint8_t buf[64], tmp[64];
    ry_pkt_t p = sample_pkt(), q;
    CHECK_EQ(ry_pkt_encode(&p, RY_PKT_HDR_LEN - 1, buf, sizeof(buf)), RY_ERR_RANGE);
    CHECK_EQ(ry_pkt_encode(&p, RY_PKT_MAX_LEN + 1, buf, sizeof(buf)), RY_ERR_RANGE);
    CHECK_EQ(ry_pkt_encode(&p, 40, buf, 39), RY_ERR_ARG);
    CHECK_EQ(ry_pkt_encode(NULL, 20, buf, sizeof(buf)), RY_ERR_ARG);
    p.radio = 0;
    CHECK_EQ(ry_pkt_encode(&p, 20, buf, sizeof(buf)), RY_ERR_FIELD);
    p.radio = 9;
    CHECK_EQ(ry_pkt_encode(&p, 20, buf, sizeof(buf)), RY_ERR_FIELD);

    p = sample_pkt();
    int n = ry_pkt_encode(&p, 32, buf, sizeof(buf));
    CHECK_EQ(n, 32);
    for (size_t len = 0; len < RY_PKT_HDR_LEN; len++) CHECK_EQ(ry_pkt_decode(buf, len, &q, NULL), RY_ERR_SHORT);
    CHECK_EQ(ry_pkt_decode(NULL, 32, &q, NULL), RY_ERR_ARG);

    /* every single-bit flip in the 18-byte header must be rejected */
    for (int byte = 0; byte < RY_PKT_HDR_LEN; byte++) {
        for (int bit = 0; bit < 8; bit++) {
            memcpy(tmp, buf, 32);
            tmp[byte] ^= (uint8_t)(1u << bit);
            int rc = ry_pkt_decode(tmp, 32, &q, NULL);
            CHECK(rc != RY_OK);
            if (byte < 2) CHECK_EQ(rc, RY_ERR_MAGIC);
            else if (byte == 2) CHECK_EQ(rc, RY_ERR_VERSION);
            else CHECK_EQ(rc, RY_ERR_CRC);
        }
    }
    /* padding corruption is soft: decode ok, pad_ok = 0 */
    memcpy(tmp, buf, 32);
    tmp[25] ^= 0x40;
    int pad_ok = -1;
    CHECK_EQ(ry_pkt_decode(tmp, 32, &q, &pad_ok), RY_OK);
    CHECK_EQ(pad_ok, 0);

    /* valid CRC but invalid radio id -> FIELD */
    memcpy(tmp, buf, RY_PKT_HDR_LEN);
    tmp[3] = 7;
    uint16_t crc = ry_crc16_ccitt(tmp, 16);
    tmp[16] = (uint8_t)crc;
    tmp[17] = (uint8_t)(crc >> 8);
    CHECK_EQ(ry_pkt_decode(tmp, RY_PKT_HDR_LEN, &q, NULL), RY_ERR_FIELD);

    /* all-zero and all-0xFF buffers */
    memset(tmp, 0, sizeof(tmp));
    CHECK_EQ(ry_pkt_decode(tmp, sizeof(tmp), &q, NULL), RY_ERR_MAGIC);
    memset(tmp, 0xFF, sizeof(tmp));
    CHECK_EQ(ry_pkt_decode(tmp, sizeof(tmp), &q, NULL), RY_ERR_MAGIC);
}

static void test_ble(void)
{
    uint8_t adv[31];
    ry_pkt_t p = sample_pkt(), q;
    p.radio = RY_RADIO_BLE;
    p.channel = 38;
    CHECK_EQ(ry_ble_adv_build(&p, adv, sizeof(adv)), RY_BLE_ADV_LEN);
    CHECK(RY_BLE_ADV_LEN <= 31);
    CHECK_EQ(adv[0], RY_BLE_ADV_LEN - 1);
    CHECK_EQ(adv[1], 0xFF);
    CHECK_EQ(ry_ble_adv_find(adv, RY_BLE_ADV_LEN, &q), RY_OK);
    CHECK_EQ(q.seq, p.seq);
    CHECK_EQ(q.channel, 38);
    CHECK_EQ(ry_ble_adv_build(&p, adv, RY_BLE_ADV_LEN - 1), RY_ERR_ARG);

    /* preceded by flags + a short name, followed by zero padding */
    uint8_t big[64];
    size_t k = 0;
    big[k++] = 2; big[k++] = 0x01; big[k++] = 0x06;
    big[k++] = 3; big[k++] = 0x09; big[k++] = 'h'; big[k++] = 'i';
    memcpy(big + k, adv, RY_BLE_ADV_LEN);
    k += RY_BLE_ADV_LEN;
    big[k++] = 0;
    big[k++] = 0;
    CHECK_EQ(ry_ble_adv_find(big, k, &q), RY_OK);

    /* another company's manufacturer data only */
    uint8_t other[] = {5, 0xFF, 0x4C, 0x00, 0x01, 0x02};
    CHECK_EQ(ry_ble_adv_find(other, sizeof(other), &q), RY_ERR_NOTFOUND);
    /* AD length runs past the buffer */
    uint8_t trunc[] = {30, 0xFF, 0xFF, 0xFF, 'R', 'Y'};
    CHECK_EQ(ry_ble_adv_find(trunc, sizeof(trunc), &q), RY_ERR_SYNTAX);
    /* our company id but corrupted payload */
    memcpy(big, adv, RY_BLE_ADV_LEN);
    big[10] ^= 1;
    CHECK_EQ(ry_ble_adv_find(big, RY_BLE_ADV_LEN, &q), RY_ERR_CRC);
    /* our company id with a too-short payload */
    uint8_t shortp[] = {5, 0xFF, 0xFF, 0xFF, 'R', 'Y'};
    CHECK_EQ(ry_ble_adv_find(shortp, sizeof(shortp), &q), RY_ERR_SHORT);
    /* empty and degenerate inputs */
    CHECK_EQ(ry_ble_adv_find(adv, 0, &q), RY_ERR_NOTFOUND);
    uint8_t one[] = {1, 0xFF};
    CHECK_EQ(ry_ble_adv_find(one, sizeof(one), &q), RY_ERR_NOTFOUND);
    CHECK_EQ(ry_ble_adv_find(NULL, 3, &q), RY_ERR_ARG);
}

static void test_154(void)
{
    uint8_t fr[RY_154_MAX_PSDU + 1];
    ry_pkt_t p = sample_pkt(), q;
    p.radio = RY_RADIO_154;
    p.channel = 15;
    int n = ry_154_frame_build(&p, RY_PKT_HDR_LEN, 0xCAFE, 0x0001, fr, sizeof(fr));
    CHECK_EQ(n, 1 + RY_154_MHR_LEN + RY_PKT_HDR_LEN + 2);
    CHECK_EQ(fr[0], RY_154_MHR_LEN + RY_PKT_HDR_LEN + 2);
    CHECK_EQ(fr[1], 0x41);
    CHECK_EQ(fr[2], 0x88);
    CHECK_EQ(fr[4], 0xFE);
    CHECK_EQ(fr[5], 0xCA);
    CHECK_EQ(ry_154_frame_parse(fr, (size_t)n, &q), RY_OK);
    CHECK_EQ(q.seq, p.seq);
    CHECK_EQ(q.channel, 15);

    n = ry_154_frame_build(&p, RY_154_MAX_PAYLOAD, 0xCAFE, 1, fr, sizeof(fr));
    CHECK_EQ(n, RY_154_MAX_PSDU + 1);
    CHECK_EQ(ry_154_frame_parse(fr, (size_t)n, &q), RY_OK);
    CHECK_EQ(ry_154_frame_build(&p, RY_154_MAX_PAYLOAD + 1, 0xCAFE, 1, fr, sizeof(fr)), RY_ERR_RANGE);
    CHECK_EQ(ry_154_frame_build(&p, RY_PKT_HDR_LEN - 1, 0xCAFE, 1, fr, sizeof(fr)), RY_ERR_RANGE);
    CHECK_EQ(ry_154_frame_build(&p, 40, 0xCAFE, 1, fr, 20), RY_ERR_ARG);

    /* parse errors */
    ry_154_frame_build(&p, RY_PKT_HDR_LEN, 0xCAFE, 1, fr, sizeof(fr));
    size_t full = (size_t)fr[0] + 1;
    CHECK_EQ(ry_154_frame_parse(fr, full - 1, &q), RY_ERR_SYNTAX); /* length byte > buffer */
    CHECK_EQ(ry_154_frame_parse(fr, 0, &q), RY_ERR_SHORT);
    uint8_t save = fr[0];
    fr[0] = 128;
    CHECK_EQ(ry_154_frame_parse(fr, sizeof(fr), &q), RY_ERR_SYNTAX);
    fr[0] = 5;
    CHECK_EQ(ry_154_frame_parse(fr, sizeof(fr), &q), RY_ERR_SHORT);
    fr[0] = save;
    fr[1] = 0x03; /* MAC command frame type */
    CHECK_EQ(ry_154_frame_parse(fr, full, &q), RY_ERR_NOTFOUND);
    fr[1] = 0x41;
    fr[15] ^= 0x10; /* inside the RY payload */
    CHECK_EQ(ry_154_frame_parse(fr, full, &q), RY_ERR_CRC);
}

static void test_cmd(void)
{
    ry_cmd_t c;
    long v;
    CHECK_EQ(ry_cmd_parse("INFO", &c), RY_OK);
    CHECK(strcmp(c.verb, "INFO") == 0);
    CHECK_EQ(c.nkv, 0);
    CHECK_EQ(c.arg[0], 0);

    CHECK_EQ(ry_cmd_parse("  SET radio=wifi ch=6\tpwr_qdbm=-40  \r\n", &c), RY_OK);
    CHECK(strcmp(c.verb, "SET") == 0);
    CHECK_EQ(c.nkv, 3);
    CHECK(strcmp(ry_cmd_get(&c, "radio"), "wifi") == 0);
    CHECK_EQ(ry_cmd_get_int(&c, "ch", 1, 13, 0, 0, &v), RY_OK);
    CHECK_EQ(v, 6);
    CHECK_EQ(ry_cmd_get_int(&c, "pwr_qdbm", -100, 100, 0, 0, &v), RY_OK);
    CHECK_EQ(v, -40);
    CHECK(ry_cmd_get(&c, "len") == NULL);
    CHECK_EQ(ry_cmd_get_int(&c, "len", 18, 250, 0, 0, &v), RY_ERR_MISSING);
    CHECK_EQ(ry_cmd_get_int(&c, "len", 18, 250, 1, 32, &v), RY_OK);
    CHECK_EQ(v, 32);
    CHECK_EQ(ry_cmd_get_int(&c, "ch", 7, 13, 0, 0, &v), RY_ERR_RANGE);
    CHECK_EQ(ry_cmd_get_int(&c, "radio", 0, 10, 0, 0, &v), RY_ERR_SYNTAX);

    CHECK_EQ(ry_cmd_parse("RX start", &c), RY_OK);
    CHECK(strcmp(c.arg, "start") == 0);
    CHECK_EQ(ry_cmd_parse("TX n=500 interval_ms=5 run=65535", &c), RY_OK);
    CHECK_EQ(ry_cmd_get_int(&c, "run", 0, 65535, 0, 0, &v), RY_OK);
    CHECK_EQ(v, 65535);
    CHECK_EQ(ry_cmd_parse("SET ch=all radio=ble", &c), RY_OK);
    CHECK(strcmp(ry_cmd_get(&c, "ch"), "all") == 0);

    /* malformed */
    CHECK_EQ(ry_cmd_parse("", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("   \r\n", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("info", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("IN-FO", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("ABCDEFGHIJKL", &c), RY_ERR_TOOLONG);
    CHECK_EQ(ry_cmd_parse("SET ch=6 ch=7", &c), RY_ERR_DUP);
    CHECK_EQ(ry_cmd_parse("SET =6", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("SET ch=", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("SET Ch=6", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("SET ch=6;rm", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("SET ch=\"6\"", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("SET ch=6=7", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("RX start stop", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("RX ch=1 start", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("RX St", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse("SET abcdefghijklmnop=1", &c), RY_ERR_TOOLONG);
    CHECK_EQ(ry_cmd_parse("SET a=0123456789012345678901234567890123", &c), RY_ERR_TOOLONG);
    CHECK_EQ(ry_cmd_parse("RX abcdefghijklmnopq", &c), RY_ERR_TOOLONG);
    CHECK_EQ(ry_cmd_parse("SET a=1 b=1 c=1 d=1 e=1 f=1 g=1 h=1 i=1 j=1 k=1 l=1 m=1", &c), RY_ERR_TOOLONG);
    char longline[RY_LINE_MAX + 10];
    memset(longline, 'A', sizeof(longline) - 1);
    longline[sizeof(longline) - 1] = 0;
    CHECK_EQ(ry_cmd_parse(longline, &c), RY_ERR_TOOLONG);
    CHECK_EQ(ry_cmd_parse("SET\x01 a=1", &c), RY_ERR_SYNTAX);
    CHECK_EQ(ry_cmd_parse(NULL, &c), RY_ERR_ARG);

    /* integer parsing edge cases */
    const char *bad_ints[] = {"4x", "--1", "+", "-", "1.5", "0x10", " 1"};
    for (size_t i = 0; i < sizeof(bad_ints) / sizeof(bad_ints[0]); i++) {
        ry_cmd_t cc;
        memset(&cc, 0, sizeof(cc));
        cc.nkv = 1;
        strcpy(cc.kv[0].key, "n");
        strcpy(cc.kv[0].val, bad_ints[i]);
        CHECK_EQ(ry_cmd_get_int(&cc, "n", -1000, 1000, 0, 0, &v), RY_ERR_SYNTAX);
    }
    CHECK_EQ(ry_cmd_parse("TX n=99999999999", &c), RY_OK);
    CHECK_EQ(ry_cmd_get_int(&c, "n", 0, 100000, 0, 0, &v), RY_ERR_RANGE);
    CHECK_EQ(ry_cmd_parse("TX n=2147483648", &c), RY_OK);
    CHECK_EQ(ry_cmd_get_int(&c, "n", 0, 2147483647L, 0, 0, &v), RY_ERR_RANGE);
    CHECK_EQ(ry_cmd_parse("TX n=+12", &c), RY_OK);
    CHECK_EQ(ry_cmd_get_int(&c, "n", 0, 100, 0, 0, &v), RY_OK);
    CHECK_EQ(v, 12);
}

static void test_rx_line(void)
{
    ry_rx_rec_t r, q;
    memset(&r, 0, sizeof(r));
    r.radio = RY_RADIO_WIFI;
    r.rx_ch = 6;
    r.tx_ch = 6;
    r.run_id = 7;
    r.seq = 123;
    r.rssi = -45;
    r.pwr_qdbm = 40;
    r.t_us = 1234567;
    r.lqi = -1;
    r.dut_tag = 0xbeef;
    char line[RY_LINE_MAX];
    int n = ry_rx_line_format(&r, line, sizeof(line));
    CHECK(n > 0);
    CHECK(strcmp(line, "RX,wifi,6,6,7,123,-45,40,1234567,-1,beef*1C") == 0);
    CHECK_EQ(ry_rx_line_parse(line, &q), RY_OK);
    CHECK(q.radio == r.radio && q.rx_ch == r.rx_ch && q.tx_ch == r.tx_ch && q.run_id == r.run_id);
    CHECK(q.seq == r.seq && q.rssi == r.rssi && q.pwr_qdbm == r.pwr_qdbm && q.t_us == r.t_us);
    CHECK_EQ(q.dut_tag, 0xbeef);
    CHECK_EQ(q.lqi, -1);

    char tmp[RY_LINE_MAX + 8];
    snprintf(tmp, sizeof(tmp), "%s\r\n", line);
    CHECK_EQ(ry_rx_line_parse(tmp, &q), RY_OK);

    /* lowercase checksum hex is accepted */
    strcpy(tmp, line);
    for (char *p = strchr(tmp, '*'); p && *p; p++)
        if (*p >= 'A' && *p <= 'F') *p = (char)(*p - 'A' + 'a');
    CHECK_EQ(ry_rx_line_parse(tmp, &q), RY_OK);

    /* checksum problems */
    strcpy(tmp, line);
    tmp[strlen(tmp) - 3] = 0; /* strip *hh */
    CHECK_EQ(ry_rx_line_parse(tmp, &q), RY_ERR_CHECKSUM);
    strcpy(tmp, line);
    tmp[strlen(tmp) - 1] = 'Z';
    CHECK_EQ(ry_rx_line_parse(tmp, &q), RY_ERR_CHECKSUM);
    strcpy(tmp, line);
    tmp[8] = tmp[8] == '6' ? '5' : '6'; /* body corruption */
    CHECK_EQ(ry_rx_line_parse(tmp, &q), RY_ERR_CHECKSUM);

    /* structurally bad lines with correct checksums */
    const char *bodies[] = {
        "RX,wifi,6,6,7,123,-45,40,1234567,-1",            /* 10 fields */
        "RX,wifi,6,6,7,123,-45,40,1234567,-1,beef,extra", /* 12 fields */
        "RX,zigbee,6,6,7,123,-45,40,1234567,-1,beef",     /* bad radio */
        "RX,none,6,6,7,123,-45,40,1234567,-1,beef",       /* radio none */
        "RX,wifi,6,6,7,123,-200,40,1234567,-1,beef",      /* rssi range */
        "RX,wifi,6,6,70000,123,-45,40,1234567,-1,beef",   /* run_id range */
        "RX,wifi,6,6,7,4294967296,-45,40,1234567,-1,beef",/* seq range */
        "RX,wifi,6,6,7,123,-45,40,1234567,-1,beeg",       /* tag hex */
        "RX,wifi,6,6,7,123,-45,40,1234567,-1,bee",        /* tag width */
        "RX,wifi,,6,7,123,-45,40,1234567,-1,beef",        /* empty field */
        "RX,wifi,6,6,7,12a,-45,40,1234567,-1,beef",       /* junk digit */
        "RX,wifi,6,6,7,123,-45,40,1234567,-2,beef",       /* lqi range */
        "RX,wifi,6,6,7,123,--45,40,1234567,-1,beef",      /* double sign */
        "RX,wifi,6,6,7,123,-45,40,99999999999999999999999,-1,beef", /* t_us overflow */
        "RX,,6,6,7,123,-45,40,1234567,-1,beef",           /* empty radio */
    };
    for (size_t i = 0; i < sizeof(bodies) / sizeof(bodies[0]); i++) {
        snprintf(tmp, sizeof(tmp), "%s*%02X", bodies[i], ry_line_xor(bodies[i], strlen(bodies[i])));
        int rc = ry_rx_line_parse(tmp, &q);
        if (rc == RY_OK) fprintf(stderr, "unexpectedly parsed: %s\n", tmp);
        CHECK(rc != RY_OK);
    }
    CHECK_EQ(ry_rx_line_parse("OK INFO role=REF", &q), RY_ERR_SYNTAX);
    CHECK_EQ(ry_rx_line_parse("RX", &q), RY_ERR_SYNTAX);
    CHECK_EQ(ry_rx_line_parse("RX,", &q), RY_ERR_CHECKSUM);
    CHECK_EQ(ry_rx_line_parse("", &q), RY_ERR_SYNTAX);

    /* format rejects an invalid radio and a too-small buffer */
    r.radio = 0;
    CHECK_EQ(ry_rx_line_format(&r, line, sizeof(line)), RY_ERR_FIELD);
    r.radio = RY_RADIO_BLE;
    CHECK_EQ(ry_rx_line_format(&r, line, 10), RY_ERR_TOOLONG);

    /* max-width record fits RY_LINE_MAX */
    r.radio = RY_RADIO_WIFI;
    r.rx_ch = 255; r.tx_ch = 255; r.run_id = 65535; r.seq = 0xFFFFFFFFu; r.rssi = -128;
    r.pwr_qdbm = -32768; r.t_us = ~0ULL; r.lqi = 255; r.dut_tag = 0xFFFF;
    n = ry_rx_line_format(&r, line, sizeof(line));
    CHECK(n > 0 && n < RY_LINE_MAX);
    CHECK_EQ(ry_rx_line_parse(line, &q), RY_OK);
    CHECK(q.t_us == ~0ULL);
    CHECK_EQ(q.pwr_qdbm, -32768);
}

/* Deterministic mutation loop (200k random 1-3 byte mutations of a valid line): no
 * crash (run under ASan/UBSan in CI), anything accepted re-formats to a line that
 * parses to the same record, and the XOR checksum rejects nearly all mutations. */
static void test_rx_line_mutations(void)
{
    ry_rx_rec_t r, q;
    memset(&r, 0, sizeof(r));
    r.radio = RY_RADIO_BLE;
    r.rx_ch = 0; r.tx_ch = 37; r.run_id = 9; r.seq = 4242; r.rssi = -77; r.pwr_qdbm = 36;
    r.t_us = 987654321; r.lqi = -1; r.dut_tag = 0x00ff;
    char base[RY_LINE_MAX], mut[RY_LINE_MAX + 4], again[RY_LINE_MAX];
    int n = ry_rx_line_format(&r, base, sizeof(base));
    CHECK(n > 0);
    unsigned long long s = 0x9E3779B97F4A7C15ULL;
    int accepted = 0;
    for (int it = 0; it < 200000; it++) {
        strcpy(mut, base);
        int nm = 1 + (int)(s % 3);
        for (int k = 0; k < nm; k++) {
            s ^= s << 13; s ^= s >> 7; s ^= s << 17;
            size_t pos = (size_t)(s % (unsigned long long)n);
            mut[pos] = (char)(32 + (s >> 32) % 95);
        }
        if (ry_rx_line_parse(mut, &q) == RY_OK) {
            accepted++;
            int m = ry_rx_line_format(&q, again, sizeof(again));
            CHECK(m > 0);
            ry_rx_rec_t q2;
            CHECK_EQ(ry_rx_line_parse(again, &q2), RY_OK); /* re-format is stable */
            CHECK(q2.seq == q.seq && q2.t_us == q.t_us && q2.rssi == q.rssi && q2.dut_tag == q.dut_tag);
        }
    }
    /* with a correct body XOR, a 1-3 byte mutation can still slip through only if
       the XOR collides; just make sure those (if any) are rare */
    CHECK(accepted < 2000);
}

static int test_golden(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, "cannot open golden file %s\n", path);
        return 1;
    }
    char buf[512];
    int n = 0;
    while (fgets(buf, sizeof(buf), f)) {
        if (buf[0] == '#' || buf[0] == '\n') continue;
        buf[strcspn(buf, "\r\n")] = 0;
        char *bar = strchr(buf, '|');
        CHECK(bar != NULL);
        if (!bar) continue;
        *bar = 0;
        const char *line = buf;
        char radio[8];
        unsigned rx_ch, tx_ch, run, tag;
        unsigned long seq;
        int rssi, pwr, lqi;
        unsigned long long t;
        int got = sscanf(bar + 1, "%7[^|]|%u|%u|%u|%lu|%d|%d|%llu|%d|%x", radio, &rx_ch, &tx_ch, &run, &seq,
                         &rssi, &pwr, &t, &lqi, &tag);
        CHECK_EQ(got, 10);
        ry_rx_rec_t r, q;
        memset(&r, 0, sizeof(r));
        ry_radio_t rr;
        CHECK_EQ(ry_radio_parse(radio, &rr), RY_OK);
        r.radio = (uint8_t)rr; r.rx_ch = (uint8_t)rx_ch; r.tx_ch = (uint8_t)tx_ch; r.run_id = (uint16_t)run;
        r.seq = (uint32_t)seq; r.rssi = (int16_t)rssi; r.pwr_qdbm = (int16_t)pwr; r.t_us = t;
        r.lqi = (int16_t)lqi; r.dut_tag = (uint16_t)tag;
        char out[RY_LINE_MAX];
        CHECK(ry_rx_line_format(&r, out, sizeof(out)) > 0);
        if (strcmp(out, line) != 0) fprintf(stderr, "golden mismatch:\n  want %s\n  got  %s\n", line, out);
        CHECK(strcmp(out, line) == 0);
        CHECK_EQ(ry_rx_line_parse(line, &q), RY_OK);
        CHECK_EQ(q.seq, r.seq);
        CHECK_EQ(q.rssi, r.rssi);
        CHECK(q.t_us == r.t_us);
        n++;
    }
    fclose(f);
    CHECK(n >= 3);
    return 0;
}

int main(int argc, char **argv)
{
    test_crc();
    test_pkt_roundtrip();
    test_pkt_errors();
    test_ble();
    test_154();
    test_cmd();
    test_rx_line();
    test_rx_line_mutations();
    if (argc > 1) test_golden(argv[1]);
    else {
        fprintf(stderr, "usage: %s <golden_rx_lines.txt>\n", argv[0]);
        g_fail++;
    }
    printf("%d checks, %d failures\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
