#!/usr/bin/env python3
"""Thin shim: HY-2B/2C HSCAT delayed-daily winds tick. See hy2obs.build."""
import argparse

from sarobs.store import make_store
from hy2obs.build import build

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--store", default="local:/tmp/tat-hy2")
    a = p.parse_args()
    build(make_store(a.store))
