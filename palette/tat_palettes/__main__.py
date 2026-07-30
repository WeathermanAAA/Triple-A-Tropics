"""``python -m tat_palettes`` runs the no-drift self-test."""
from ._selftest import verify_no_drift

if __name__ == "__main__":
    n = verify_no_drift()
    print(f"tat_palettes: no-drift self-test PASSED ({n} golden RGBA checks)")
