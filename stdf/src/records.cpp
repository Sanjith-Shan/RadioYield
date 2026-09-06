// STDF V4 record field layouts and the Arrow-layout Column builder.
#include "ry_stdf/stdf.hpp"

namespace ry::stdf {

namespace {
#define F(n, t) FieldSpec{n, FT::t, -1}
#define K(n, t, k) FieldSpec{n, FT::t, k}

std::vector<RecordSpec> make_specs() {
    std::vector<RecordSpec> s;
    s.push_back({0, 10, "FAR", {F("CPU_TYPE", U1), F("STDF_VER", U1)}});
    s.push_back({0, 20, "ATR", {F("MOD_TIM", U4), F("CMD_LINE", Cn)}});
    s.push_back({1, 10, "MIR",
                 {F("SETUP_T", U4), F("START_T", U4), F("STAT_NUM", U1), F("MODE_COD", C1),
                  F("RTST_COD", C1), F("PROT_COD", C1), F("BURN_TIM", U2), F("CMOD_COD", C1),
                  F("LOT_ID", Cn), F("PART_TYP", Cn), F("NODE_NAM", Cn), F("TSTR_TYP", Cn),
                  F("JOB_NAM", Cn), F("JOB_REV", Cn), F("SBLOT_ID", Cn), F("OPER_NAM", Cn),
                  F("EXEC_TYP", Cn), F("EXEC_VER", Cn), F("TEST_COD", Cn), F("TST_TEMP", Cn),
                  F("USER_TXT", Cn), F("AUX_FILE", Cn), F("PKG_TYP", Cn), F("FAMLY_ID", Cn),
                  F("DATE_COD", Cn), F("FACIL_ID", Cn), F("FLOOR_ID", Cn), F("PROC_ID", Cn),
                  F("OPER_FRQ", Cn), F("SPEC_NAM", Cn), F("SPEC_VER", Cn), F("FLOW_ID", Cn),
                  F("SETUP_ID", Cn), F("DSGN_REV", Cn), F("ENG_ID", Cn), F("ROM_COD", Cn),
                  F("SERL_NUM", Cn), F("SUPR_NAM", Cn)}});
    s.push_back({1, 20, "MRR",
                 {F("FINISH_T", U4), F("DISP_COD", C1), F("USR_DESC", Cn), F("EXC_DESC", Cn)}});
    s.push_back({1, 30, "PCR",
                 {F("HEAD_NUM", U1), F("SITE_NUM", U1), F("PART_CNT", U4), F("RTST_CNT", U4),
                  F("ABRT_CNT", U4), F("GOOD_CNT", U4), F("FUNC_CNT", U4)}});
    s.push_back({1, 40, "HBR",
                 {F("HEAD_NUM", U1), F("SITE_NUM", U1), F("HBIN_NUM", U2), F("HBIN_CNT", U4),
                  F("HBIN_PF", C1), F("HBIN_NAM", Cn)}});
    s.push_back({1, 50, "SBR",
                 {F("HEAD_NUM", U1), F("SITE_NUM", U1), F("SBIN_NUM", U2), F("SBIN_CNT", U4),
                  F("SBIN_PF", C1), F("SBIN_NAM", Cn)}});
    s.push_back({1, 60, "PMR",
                 {F("PMR_INDX", U2), F("CHAN_TYP", U2), F("CHAN_NAM", Cn), F("PHY_NAM", Cn),
                  F("LOG_NAM", Cn), F("HEAD_NUM", U1), F("SITE_NUM", U1)}});
    s.push_back({1, 62, "PGR",
                 {F("GRP_INDX", U2), F("GRP_NAM", Cn), F("INDX_CNT", U2), K("PMR_INDX", KU2, 2)}});
    s.push_back({1, 63, "PLR",
                 {F("GRP_CNT", U2), K("GRP_INDX", KU2, 0), K("GRP_MODE", KU2, 0),
                  K("GRP_RADX", KU1, 0), K("PGM_CHAR", KCn, 0), K("RTN_CHAR", KCn, 0),
                  K("PGM_CHAL", KCn, 0), K("RTN_CHAL", KCn, 0)}});
    s.push_back({1, 70, "RDR", {F("NUM_BINS", U2), K("RTST_BIN", KU2, 0)}});
    s.push_back({1, 80, "SDR",
                 {F("HEAD_NUM", U1), F("SITE_GRP", U1), F("SITE_CNT", U1), K("SITE_NUM", KU1, 2),
                  F("HAND_TYP", Cn), F("HAND_ID", Cn), F("CARD_TYP", Cn), F("CARD_ID", Cn),
                  F("LOAD_TYP", Cn), F("LOAD_ID", Cn), F("DIB_TYP", Cn), F("DIB_ID", Cn),
                  F("CABL_TYP", Cn), F("CABL_ID", Cn), F("CONT_TYP", Cn), F("CONT_ID", Cn),
                  F("LASR_TYP", Cn), F("LASR_ID", Cn), F("EXTR_TYP", Cn), F("EXTR_ID", Cn)}});
    s.push_back({2, 10, "WIR",
                 {F("HEAD_NUM", U1), F("SITE_GRP", U1), F("START_T", U4), F("WAFER_ID", Cn)}});
    s.push_back({2, 20, "WRR",
                 {F("HEAD_NUM", U1), F("SITE_GRP", U1), F("FINISH_T", U4), F("PART_CNT", U4),
                  F("RTST_CNT", U4), F("ABRT_CNT", U4), F("GOOD_CNT", U4), F("FUNC_CNT", U4),
                  F("WAFER_ID", Cn), F("FABWF_ID", Cn), F("FRAME_ID", Cn), F("MASK_ID", Cn),
                  F("USR_DESC", Cn), F("EXC_DESC", Cn)}});
    s.push_back({2, 30, "WCR",
                 {F("WAFR_SIZ", R4), F("DIE_HT", R4), F("DIE_WID", R4), F("WF_UNITS", U1),
                  F("WF_FLAT", C1), F("CENTER_X", I2), F("CENTER_Y", I2), F("POS_X", C1),
                  F("POS_Y", C1)}});
    s.push_back({5, 10, "PIR", {F("HEAD_NUM", U1), F("SITE_NUM", U1)}});
    s.push_back({5, 20, "PRR",
                 {F("HEAD_NUM", U1), F("SITE_NUM", U1), F("PART_FLG", B1), F("NUM_TEST", U2),
                  F("HARD_BIN", U2), F("SOFT_BIN", U2), F("X_COORD", I2), F("Y_COORD", I2),
                  F("TEST_T", U4), F("PART_ID", Cn), F("PART_TXT", Cn), F("PART_FIX", Bn)}});
    s.push_back({10, 30, "TSR",
                 {F("HEAD_NUM", U1), F("SITE_NUM", U1), F("TEST_TYP", C1), F("TEST_NUM", U4),
                  F("EXEC_CNT", U4), F("FAIL_CNT", U4), F("ALRM_CNT", U4), F("TEST_NAM", Cn),
                  F("SEQ_NAME", Cn), F("TEST_LBL", Cn), F("OPT_FLAG", B1), F("TEST_TIM", R4),
                  F("TEST_MIN", R4), F("TEST_MAX", R4), F("TST_SUMS", R4), F("TST_SQRS", R4)}});
    s.push_back({15, 10, "PTR",
                 {F("TEST_NUM", U4), F("HEAD_NUM", U1), F("SITE_NUM", U1), F("TEST_FLG", B1),
                  F("PARM_FLG", B1), F("RESULT", R4), F("TEST_TXT", Cn), F("ALARM_ID", Cn),
                  F("OPT_FLAG", B1), F("RES_SCAL", I1), F("LLM_SCAL", I1), F("HLM_SCAL", I1),
                  F("LO_LIMIT", R4), F("HI_LIMIT", R4), F("UNITS", Cn), F("C_RESFMT", Cn),
                  F("C_LLMFMT", Cn), F("C_HLMFMT", Cn), F("LO_SPEC", R4), F("HI_SPEC", R4)}});
    s.push_back({15, 15, "MPR",
                 {F("TEST_NUM", U4), F("HEAD_NUM", U1), F("SITE_NUM", U1), F("TEST_FLG", B1),
                  F("PARM_FLG", B1), F("RTN_ICNT", U2), F("RSLT_CNT", U2), K("RTN_STAT", KN1, 5),
                  K("RTN_RSLT", KR4, 6), F("TEST_TXT", Cn), F("ALARM_ID", Cn), F("OPT_FLAG", B1),
                  F("RES_SCAL", I1), F("LLM_SCAL", I1), F("HLM_SCAL", I1), F("LO_LIMIT", R4),
                  F("HI_LIMIT", R4), F("START_IN", R4), F("INCR_IN", R4), K("RTN_INDX", KU2, 5),
                  F("UNITS", Cn), F("UNITS_IN", Cn), F("C_RESFMT", Cn), F("C_LLMFMT", Cn),
                  F("C_HLMFMT", Cn), F("LO_SPEC", R4), F("HI_SPEC", R4)}});
    s.push_back({15, 20, "FTR",
                 {F("TEST_NUM", U4), F("HEAD_NUM", U1), F("SITE_NUM", U1), F("TEST_FLG", B1),
                  F("OPT_FLAG", B1), F("CYCL_CNT", U4), F("REL_VADR", U4), F("REPT_CNT", U4),
                  F("NUM_FAIL", U4), F("XFAIL_AD", I4), F("YFAIL_AD", I4), F("VECT_OFF", I2),
                  F("RTN_ICNT", U2), F("PGM_ICNT", U2), K("RTN_INDX", KU2, 12),
                  K("RTN_STAT", KN1, 12), K("PGM_INDX", KU2, 13), K("PGM_STAT", KN1, 13),
                  F("FAIL_PIN", Dn), F("VECT_NAM", Cn), F("TIME_SET", Cn), F("OP_CODE", Cn),
                  F("TEST_TXT", Cn), F("ALARM_ID", Cn), F("PROG_TXT", Cn), F("RSLT_TXT", Cn),
                  F("PATG_NUM", U1), F("SPIN_MAP", Dn)}});
    s.push_back({20, 10, "BPS", {F("SEQ_NAME", Cn)}});
    s.push_back({20, 20, "EPS", {}});
    s.push_back({50, 10, "GDR", {F("FLD_CNT", U2), K("GEN_DATA", Vn, 0)}});
    s.push_back({50, 30, "DTR", {F("TEXT_DAT", Cn)}});
    return s;
}
#undef F
#undef K
}  // namespace

const std::vector<RecordSpec>& record_specs() {
    static const std::vector<RecordSpec> specs = make_specs();
    return specs;
}

const RecordSpec* find_spec(uint8_t typ, uint8_t sub) {
    static const std::vector<const RecordSpec*> lut = [] {
        std::vector<const RecordSpec*> t(65536, nullptr);
        for (const auto& r : record_specs()) t[(size_t(r.typ) << 8) | r.sub] = &r;
        return t;
    }();
    return lut[(size_t(typ) << 8) | sub];
}

// ---------------------------------------------------------------- Column
Column Column::fixed(const char* type, int width) {
    Column c;
    c.kind = FIXED;
    c.type = type;
    c.width = width;
    return c;
}
Column Column::boolean() {
    Column c;
    c.kind = BOOL;
    c.type = "bool";
    return c;
}
Column Column::string() {
    Column c;
    c.kind = STRING;
    c.type = "string";
    c.offsets.push_back(0);
    return c;
}
Column Column::binary() {
    Column c;
    c.kind = BINARY;
    c.type = "binary";
    c.offsets.push_back(0);
    return c;
}
Column Column::list(Column child) {
    Column c;
    c.kind = LIST;
    c.type = "list";
    c.offsets.push_back(0);
    c.child = std::make_unique<Column>(std::move(child));
    return c;
}

void Column::push_bool(bool v) {
    if ((length & 7) == 0) data.push_back(0);
    if (v) data.back() |= uint8_t(1u << (length & 7));
    if (null_count) valid.push_back(1);
    ++length;
}

static inline void check_offset(size_t n) {
    if (n > size_t(INT32_MAX)) throw Error("column exceeds 2 GiB of string/list data");
}

static inline bool all_ascii(const uint8_t* s, size_t n) {
    uint64_t acc = 0;
    size_t i = 0;
    for (; i + 8 <= n; i += 8) {
        uint64_t w;
        std::memcpy(&w, s + i, 8);
        acc |= w;
    }
    for (; i < n; ++i) acc |= s[i];
    return (acc & 0x8080808080808080ull) == 0;
}

void Column::push_str(const uint8_t* s, size_t n) {
    const size_t o = data.size();
    if (all_ascii(s, n)) {
        data.resize(o + n);
        if (n) std::memcpy(data.data() + o, s, n);
    } else {  // STDF strings are ASCII; map stray high bytes as Latin-1 -> UTF-8
        for (size_t i = 0; i < n; ++i) {
            uint8_t b = s[i];
            if (b < 0x80) {
                data.push_back(b);
            } else {
                data.push_back(uint8_t(0xC0 | (b >> 6)));
                data.push_back(uint8_t(0x80 | (b & 0x3F)));
            }
        }
    }
    check_offset(data.size());
    offsets.push_back(int32_t(data.size()));
    if (null_count) valid.push_back(1);
    ++length;
}

void Column::push_bytes(const uint8_t* s, size_t n) {
    data.insert(data.end(), s, s + n);
    check_offset(data.size());
    offsets.push_back(int32_t(data.size()));
    if (null_count) valid.push_back(1);
    ++length;
}

void Column::push_null() {
    switch (kind) {
        case FIXED: data.resize(data.size() + size_t(width), 0); break;
        case BOOL:
            if ((length & 7) == 0) data.push_back(0);
            break;
        case STRING:
        case BINARY: offsets.push_back(int32_t(data.size())); break;
        case LIST: offsets.push_back(int32_t(child->length)); break;
    }
    if (null_count == 0) valid.assign(size_t(length), 1);  // validity is materialized lazily
    valid.push_back(0);
    ++null_count;
    ++length;
}

void Column::end_list() {
    check_offset(size_t(child->length));
    offsets.push_back(int32_t(child->length));
    if (null_count) valid.push_back(1);
    ++length;
}

void Column::finish() {
    if (finished) return;
    finished = true;
    if (child) child->finish();
    if (null_count == 0) {
        std::vector<uint8_t>().swap(valid);
        return;
    }
    std::vector<uint8_t> bits(size_t((length + 7) / 8), 0);
    for (int64_t i = 0; i < length; ++i)
        if (valid[size_t(i)]) bits[size_t(i >> 3)] |= uint8_t(1u << (i & 7));
    valid.swap(bits);
}

}  // namespace ry::stdf
