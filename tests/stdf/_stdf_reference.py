"""Independent reference path for the STDF tests.

Bytes are decoded by pystdf (pure Python, independent of our C++), and the
record -> measurement mapping of radioyield/schema.py is re-implemented here in
plain Python. Our reader must produce exactly the same table.
"""
from __future__ import annotations

import numpy as np
import pyarrow as pa

from radioyield.schema import MEASUREMENT_SCHEMA


def pystdf_records(path) -> list[tuple[str, dict]]:
    from pystdf.IO import Parser

    out: list[tuple[str, dict]] = []

    class Sink:
        def after_send(self, ds, data):
            rt, fields = data
            out.append((type(rt).__name__.upper(), dict(zip(rt.fieldNames, fields))))

    with open(path, "rb") as f:
        p = Parser(inp=f)
        p.addSink(Sink())
        p.parse()
    return out


def semiate_records(path) -> list[tuple[str, dict]]:
    from Semi_ATE.STDF import records_from_file

    out = []
    for rec in records_from_file(str(path)):
        if rec is None:
            continue
        out.append((rec.id, rec.to_dict()))
    return out


def measurements_from_records(recs: list[tuple[str, dict]]) -> pa.Table:
    lot = station = None
    start_t = None
    have_mir = False
    meta: dict[str, str] = {}
    seen_pir = False
    open_parts: dict[tuple[int, int], dict] = {}
    last_opened: dict | None = None
    tests: dict[int, dict] = {}
    rows: list[tuple[dict, dict]] = []

    for name, f in recs:
        if name == "MIR" and not have_mir:
            have_mir = True
            lot, station, start_t = f.get("LOT_ID"), f.get("NODE_NAM"), f.get("START_T")
        elif name == "DTR":
            text = f.get("TEXT_DAT") or ""
            if not (text.startswith("ry.") and "=" in text and len(text) >= 4):
                continue
            key, val = text[3:].split("=", 1)
            if key == "part_ts":
                if last_opened is not None and not last_opened["closed"]:
                    if val == "":
                        last_opened["ts_state"] = 2
                    else:
                        last_opened["ts"], last_opened["ts_state"] = float(val), 1
            elif not seen_pir and key not in ("lot_id", "station", ""):
                meta[key] = val
        elif name == "PIR":
            seen_pir = True
            key = (f.get("HEAD_NUM") if f.get("HEAD_NUM") is not None else 1,
                   f.get("SITE_NUM") if f.get("SITE_NUM") is not None else 1)
            part = {"closed": False, "ts_state": 0, "ts": None}
            open_parts[key] = part
            last_opened = part
        elif name == "PRR":
            key = (f.get("HEAD_NUM") if f.get("HEAD_NUM") is not None else 1,
                   f.get("SITE_NUM") if f.get("SITE_NUM") is not None else 1)
            part = open_parts.pop(key, None)
            if part is None:
                continue
            flg = f.get("PART_FLG")
            hb, sb = f.get("HARD_BIN"), f.get("SOFT_BIN")
            part.update(
                closed=True,
                retest=bool(flg & 0x03) if flg is not None else False,
                pass_=not (flg & 0x08) if flg is not None else True,
                hbin=None if hb is None or hb == 65535 else hb,
                sbin=None if sb is None or sb == 65535 else sb,
                id=f.get("PART_ID") or None,
            )
        elif name == "PTR":
            tn = f.get("TEST_NUM")
            if tn is None:
                continue
            st = tests.setdefault(tn, {"has": False, "name": None, "units": None, "lo": None, "hi": None})
            first = not st["has"]
            txt, units = f.get("TEST_TXT"), f.get("UNITS")
            name_ = txt if txt else st["name"]
            units_ = units if units else st["units"]
            lo, hi = st["lo"], st["hi"]
            opt = f.get("OPT_FLAG")
            if opt is not None:
                if opt & 0x40:
                    lo = None
                elif not (opt & 0x10) and f.get("LO_LIMIT") is not None:
                    lo = f["LO_LIMIT"]
                if opt & 0x80:
                    hi = None
                elif not (opt & 0x20) and f.get("HI_LIMIT") is not None:
                    hi = f["HI_LIMIT"]
            if first:
                st.update(has=True, lo=lo, hi=hi)
            if st["name"] is None and name_:
                st["name"] = name_
            if st["units"] is None and units_:
                st["units"] = units_
            head = f.get("HEAD_NUM") if f.get("HEAD_NUM") is not None else 1
            site = f.get("SITE_NUM") if f.get("SITE_NUM") is not None else 1
            part = open_parts.get((head, site))
            if part is None:
                continue
            flg = f.get("TEST_FLG")
            res = f.get("RESULT")
            if res is not None and flg is not None and flg & 0x02:
                res = None
            rows.append((part, {
                "site_num": site, "test_num": tn, "test_name": name_, "units": units_,
                "lo_limit": lo, "hi_limit": hi, "result": res,
                "test_pass": ((flg or 0) & 0xC0) == 0,
            }))

    def part_ts(p):
        if p["ts_state"] == 1:
            return p["ts"]
        if p["ts_state"] == 0 and start_t is not None:
            return float(start_t)
        return None

    cols = {c: [] for c in MEASUREMENT_SCHEMA.names}
    for part, r in rows:
        closed = part["closed"]
        cols["lot_id"].append(lot)
        cols["station"].append(station)
        cols["build"].append(meta.get("build"))
        cols["data_origin"].append(meta.get("data_origin", "UNKNOWN"))
        cols["part_id"].append(part.get("id") if closed else None)
        cols["part_ts"].append(part_ts(part))
        cols["retest"].append(part.get("retest") if closed else None)
        cols["part_pass"].append(part.get("pass_") if closed else None)
        cols["hard_bin"].append(part.get("hbin") if closed else None)
        cols["soft_bin"].append(part.get("sbin") if closed else None)
        for k, v in r.items():
            cols[k].append(v)
    return pa.table(cols, schema=MEASUREMENT_SCHEMA)


def expected_after_write(t: pa.Table) -> pa.Table:
    """What our reader must return for a table written by our writer.

    STDF stores PTR RESULT/LO_LIMIT/HI_LIMIT as 4-byte floats, so those columns
    round to float32; everything else is exact for the tables used in tests.
    """
    out = t
    for c in ("lo_limit", "hi_limit", "result"):
        i = out.schema.get_field_index(c)
        a = out.column(c).combine_chunks()
        vals = a.fill_null(0).to_numpy(zero_copy_only=False).astype(np.float32).astype(np.float64)
        out = out.set_column(i, c, pa.array(vals, mask=a.is_null().to_numpy(zero_copy_only=False),
                                            type=pa.float64()))
    return out.cast(MEASUREMENT_SCHEMA)


def synth_table(parts: int = 20, tests: int = 8, seed: int = 0, lot: str = "LOT-A", station: str = "ST1",
                build: str = "EVT", origin: str = "SIMULATED", nulls: bool = True) -> pa.Table:
    """A measurement table exercising nulls, retests, multi-site and fails."""
    rng = np.random.default_rng(seed)
    n = parts * tests
    p = np.repeat(np.arange(parts), tests)
    t = np.tile(np.arange(tests), parts)
    res = rng.normal(0.0, 1.0, n) * 10 ** (t % 4)
    lo = -2.0 * 10 ** (t % 4)
    hi = 2.0 * 10 ** (t % 4)
    test_pass = (res >= lo) & (res <= hi)
    part_pass = np.array([test_pass[p == i].all() for i in range(parts)])[p]
    hb = np.where(part_pass, 1, 2 + (p % 3)).astype(np.uint16)
    lo_mask = (t == 1) if nulls else np.zeros(n, bool)
    hi_mask = (t == 2) if nulls else np.zeros(n, bool)
    res_mask = ((p == 3) & (t == 4)) if nulls else np.zeros(n, bool)
    sb_mask = (p == 5) if nulls else np.zeros(n, bool)
    ts_mask = (p == 6) if nulls else np.zeros(n, bool)
    return pa.table(
        {
            "lot_id": pa.array([lot] * n),
            "station": pa.array([station] * n),
            "build": pa.array([build] * n),
            "data_origin": pa.array([origin] * n),
            "site_num": pa.array((p % 4).astype(np.uint8)),
            "part_id": pa.array([f"SN{i:05d}" if i % 7 else f"SN{i // 7:05d}" for i in p]),
            "part_ts": pa.array(1.7e9 + p * 3.25 + 0.001, mask=ts_mask),
            "retest": pa.array((p % 7 == 0) & (p > 0)),
            "test_num": pa.array((1000 + 10 * t).astype(np.uint32)),
            "test_name": pa.array([f"rf_test_{i}" for i in t]),
            "units": pa.array(["dBm" if i % 2 else "V" for i in t]),
            "lo_limit": pa.array(lo, mask=lo_mask),
            "hi_limit": pa.array(hi, mask=hi_mask),
            "result": pa.array(res, mask=res_mask),
            "test_pass": pa.array(test_pass),
            "part_pass": pa.array(part_pass),
            "hard_bin": pa.array(hb),
            "soft_bin": pa.array((hb * 10).astype(np.uint16), mask=sb_mask),
        }
    ).cast(MEASUREMENT_SCHEMA)
