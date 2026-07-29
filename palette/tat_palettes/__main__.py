"""``python -m tat_palettes`` runs the no-drift self-test."""
from ._selftest import verify_no_drift
from .categories import verify_contrast, verify_thresholds

if __name__ == "__main__":
    n = verify_no_drift()
    print(f"tat_palettes: no-drift self-test PASSED ({n} golden RGBA checks)")
    # The category palette has no golden RGBA to compare against (it is hand-
    # picked data, not an extraction), so its invariants are structural: the
    # thresholds must tile the wind axis, and every swatch must stay legible
    # under its ink. Both fail loudly on a careless recolor.
    print(f"tat_palettes: SSHWS categories PASSED "
          f"({verify_thresholds()} threshold + {verify_contrast()} contrast checks)")
