import math

from radioyield.collector import stats as S
from radioyield.collector.protocol import RxRecord


def recs(seqs, rssi=-50):
    return [RxRecord("wifi", 6, 6, 1, s, rssi if not callable(rssi) else rssi(s), 40, 1000 + i, -1, 0xbeef)
            for i, s in enumerate(seqs)]


def test_no_loss():
    st = S.compute(recs(range(100)), n_cmd=100)
    assert st.n_lost == 0 and st.per_pct == 0 and st.per_valid and st.rssi_median == -50


def test_gaps_and_tail_loss_counted():
    got = [s for s in range(100) if s % 10 != 3 and s < 95]   # 10 gaps... minus tail
    st = S.compute(recs(got), n_cmd=100)
    expected_lost = 100 - len(got)
    assert st.n_lost == expected_lost
    assert math.isclose(st.per_pct, expected_lost)


def test_duplicates_counted_once_and_reorders_counted():
    seqs = [0, 1, 2, 2, 5, 3, 4, 4, 6, 7, 8, 9]
    st = S.compute(recs(seqs), n_cmd=10)
    assert st.n_rx_unique == 10 and st.n_dup == 2 and st.n_reorder == 2 and st.n_lost == 0


def test_rssi_uses_first_copy_only():
    r = [RxRecord("wifi", 6, 6, 1, 0, -40, 40, 0, -1, 1), RxRecord("wifi", 6, 6, 1, 0, -90, 40, 1, -1, 1),
         RxRecord("wifi", 6, 6, 1, 1, -60, 40, 2, -1, 1)]
    st = S.compute(r, n_cmd=2)
    assert st.rssi_mean == -50 and st.rssi_stdev > 0


def test_out_of_range_ignored():
    st = S.compute(recs([0, 1, 2, 500]), n_cmd=3)
    assert st.n_out_of_range == 1 and st.n_lost == 0 and "out_of_range=1" in st.flags


def test_tx_fail_reduces_denominator():
    st = S.compute(recs(range(95)), n_cmd=100, n_tx_fail=5)
    assert st.n_onair == 95 and st.n_lost == 0 and st.per_pct == 0 and "tx_fail=5" in st.flags


def test_rx_exceeding_onair_is_flagged_not_negative():
    st = S.compute(recs(range(100)), n_cmd=100, n_tx_fail=3)
    assert st.n_lost == 0 and "rx_exceeds_onair" in st.flags


def test_invalidating_conditions():
    assert not S.compute(recs(range(10)), n_cmd=10, ref_ovf=1).per_valid
    assert not S.compute(recs(range(10)), n_cmd=10, host_bad_lines=1).per_valid
    assert not S.compute(recs(range(5)), n_cmd=10, n_sent=5, aborted=True).per_valid
    st = S.compute([], n_cmd=10, n_tx_fail=10)
    assert not st.per_valid and math.isnan(st.per_pct)


def test_all_lost_and_unavailable_rssi():
    st = S.compute([], n_cmd=50)
    assert st.per_pct == 100 and math.isnan(st.rssi_mean)
    st = S.compute(recs(range(3), rssi=127), n_cmd=3)
    assert math.isnan(st.rssi_median) and any("rssi_unavailable" in f for f in st.flags)
