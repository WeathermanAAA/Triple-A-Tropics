"""Pytest entry for the palette no-drift guarantee.

Proves the shared tat_palettes module reproduces the pre-refactor RGBA values
byte-for-byte. Also runnable directly: ``python palette/tests/test_no_drift.py``.
"""
from tat_palettes._selftest import (verify_no_drift, GOLDEN, GOLDEN_PWAT,
                                     GOLDEN_VORT, GOLDEN_RH)


def test_no_drift():
    n = verify_no_drift()
    assert n == (sum(len(rows) for rows in GOLDEN.values())
                 + len(GOLDEN_PWAT) + len(GOLDEN_VORT) + len(GOLDEN_RH))


if __name__ == "__main__":
    n = verify_no_drift()
    print(f"PASSED: {n} golden RGBA checks reproduced exactly")
