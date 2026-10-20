"""Per-step statistics from the REF's per-packet records.

PER is computed from sequence numbers: the DUT sends seq 0..n-1 for a run_id; every seq
in that range that never arrived is a lost packet. Duplicates (the same seq twice) are
counted once; reorders (a seq lower than one already seen) are counted but still count as
received; seqs outside [0, sent) are counted as anomalies and ignored.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from radioyield.collector.protocol import RxRecord

RSSI_UNAVAILABLE = 127  # NimBLE reports 127 when RSSI is not available


@dataclass
class StepStats:
    n_cmd: int                 # packets the host asked for
    n_sent: int                # packets the DUT attempted (n_cmd unless aborted)
    n_tx_fail: int             # packets the DUT driver refused / reported failed
    n_onair: int               # n_sent - n_tx_fail: the PER denominator
    n_rx_unique: int           # distinct in-range seqs received
    n_lost: int
    n_dup: int
    n_reorder: int
    n_out_of_range: int
    per_pct: float             # NaN if n_onair == 0
    rssi_mean: float           # over the first copy of each unique seq; NaN if none
    rssi_median: float
    rssi_stdev: float          # sample stdev; NaN if < 2 packets
    rssi_min: float
    rssi_max: float
    ref_ovf: int = 0
    ref_bad: int = 0
    host_bad_lines: int = 0
    per_valid: bool = True
    flags: list[str] = field(default_factory=list)


def compute(records: list[RxRecord], n_cmd: int, n_sent: int | None = None, n_tx_fail: int = 0,
            ref_ovf: int = 0, ref_bad: int = 0, host_bad_lines: int = 0, aborted: bool = False) -> StepStats:
    """records: this step's records (already filtered to run_id / tag / radio), in arrival order."""
    if n_sent is None:
        n_sent = n_cmd
    n_sent = max(0, min(n_sent, n_cmd))
    n_tx_fail = max(0, min(n_tx_fail, n_sent))
    flags: list[str] = []

    seen: dict[int, RxRecord] = {}
    n_dup = n_reorder = n_oor = 0
    max_seq = -1
    for r in records:
        if r.seq >= n_sent:
            n_oor += 1
            continue
        if r.seq in seen:
            n_dup += 1
            continue
        if r.seq < max_seq:
            n_reorder += 1
        max_seq = max(max_seq, r.seq)
        seen[r.seq] = r

    n_onair = n_sent - n_tx_fail
    n_rx = len(seen)
    if n_rx > n_onair:
        # a packet the driver reported failed still reached the air; cannot attribute
        flags.append("rx_exceeds_onair")
        n_onair_eff = n_rx
    else:
        n_onair_eff = n_onair
    n_lost = n_onair_eff - n_rx
    per = 100.0 * n_lost / n_onair_eff if n_onair_eff > 0 else math.nan

    rssi = [r.rssi for r in seen.values() if r.rssi != RSSI_UNAVAILABLE]
    if len(rssi) < n_rx:
        flags.append(f"rssi_unavailable={n_rx - len(rssi)}")
    mean = statistics.fmean(rssi) if rssi else math.nan
    med = float(statistics.median(rssi)) if rssi else math.nan
    sd = statistics.stdev(rssi) if len(rssi) >= 2 else math.nan

    valid = True
    if ref_ovf:
        flags.append(f"ref_overflow={ref_ovf}")
        valid = False
    if host_bad_lines:
        flags.append(f"host_bad_lines={host_bad_lines}")
        valid = False
    if n_tx_fail:
        flags.append(f"tx_fail={n_tx_fail}")
    if aborted:
        flags.append("aborted")
        valid = False
    if n_oor:
        flags.append(f"out_of_range={n_oor}")
    if n_onair <= 0:
        valid = False
        flags.append("nothing_on_air")
    return StepStats(
        n_cmd=n_cmd, n_sent=n_sent, n_tx_fail=n_tx_fail, n_onair=n_onair, n_rx_unique=n_rx, n_lost=n_lost,
        n_dup=n_dup, n_reorder=n_reorder, n_out_of_range=n_oor, per_pct=per, rssi_mean=mean, rssi_median=med,
        rssi_stdev=sd, rssi_min=float(min(rssi)) if rssi else math.nan,
        rssi_max=float(max(rssi)) if rssi else math.nan, ref_ovf=ref_ovf, ref_bad=ref_bad,
        host_bad_lines=host_bad_lines, per_valid=valid, flags=flags,
    )
