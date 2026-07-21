"""Pure-numpy decoder for BYU SCP QuikSCAT HRStorms per-pass wind files
(*.avewr_*_WRave2/3.gz), a big-endian Fortran unformatted sequential file.
Port of sirlib/idl/loadBYUhrwind.pro (D.G. Long, BYU).

Layout (all big-endian; every Fortran record framed by int32 byte-count
markers, leading == trailing):

  rec1 (20 B data): int32 x5   ngrid, wctgrid, watgrid, ctgrid, atgrid
                    ngrid   = refinement factor (10)
                    wctgrid = 76,  watgrid = 1624  (standard 25-km L2B WVC grid)
                    ctgrid  = 760, atgrid  = 16240 (2.5-km high-res grid)
  rec2 (16 B):      int32 x4   natstart, natend, nctstart, nctend
  rec3 (16 B):      int32 x4   iastart, iaend, icstart, icend
                    nat = iaend-iastart+1 (along-track rows)
                    nct = icend-icstart+1 (cross-track cols)
  rec4 (16 B):      float32 ascnode_lon ; int32 irev, iyear, iday(DOY)
  then nat row records, each 22*nct B of data:
      int16[9*nct]  per cross-track cell: nchoice, then
                    (spd1,dir1,spd2,dir2,spd3,dir3,spd4,dir4)
                    speed_k = int/200.0  [m/s]; dir_k = int/180.0 [deg]
      int16[2*nct]  per cell: loc0, loc1
                    land = loc0 < 0
                    lat  = |loc0|/180.0 - 90.0     [deg]   (NOT the IDL reader's
                           (|loc0|-18001)/200.0 -- that formula is wrong for the
                           HRStorms avewr files: verified empirically, /180 gives
                           exactly 2.5-km row spacing and puts the Katrina eye on
                           the best track; /200 misplaces it by ~13 deg)
                    lon  = loc1/180.0              [deg, +E]
  Selected ambiguity (as in loadBYUhrwind.pro): nchoice is 1-based;
      nchoice>0 -> speeds[...,nchoice-1]; nchoice==0 -> speeds[...,1] (IDL default).

Verified semantics (Katrina revs 32244/32251 vs NHC best track + BYU quicklooks):
  * speed unit = m/s (quicklook GIF colorbars are knots; retrieval tops out ~50 m/s).
  * dir = direction wind blows TOWARD, deg clockwise from north (oceanographic),
    range [-180, 180]. NH cyclones circulate counterclockwise in this convention.
  * nchoice==0 means NO retrieval (land or missing); all 4 ambiguity slots are 0
    there, so the IDL default branch just yields 0. Treat nchoice==0 as fill.
  * land flag: loc0 < 0.
  * There is NO rain flag in this format. Rain-contaminated cells show up as
    isolated implausibly high speeds (outer rainbands can hit ~50 m/s).
  * Grid: 2.5-km swath grid (ngrid=10 refinement of the 76x1624 25-km L2B WVC
    grid); georeferencing is per-pixel lat/lon, no projection math needed.
  * Filename QS_S1B<rev>.<yyyydddhhmm>...: the timestamp is the JPL product
    CREATION time, not the observation time. Observation time = header
    (year, doy) + colocation table QS_Time, or ascnode + row index.
  * WRave2 vs WRave3: identical layout/header/ambiguity sets; they differ only
    in ambiguity SELECTION (~4% of cells; different median-filter passes).
"""
import gzip
import numpy as np

BE_I4 = np.dtype('>i4')
BE_I2 = np.dtype('>i2')
BE_F4 = np.dtype('>f4')


def _rec(buf, off, expect_len=None):
    """Return (payload_bytes, new_offset) for one Fortran record at off."""
    n = int(np.frombuffer(buf, BE_I4, 1, off)[0])
    if expect_len is not None and n != expect_len:
        raise ValueError(f"record @ {off}: marker {n}, expected {expect_len}")
    trail = int(np.frombuffer(buf, BE_I4, 1, off + 4 + n)[0])
    if trail != n:
        raise ValueError(f"record @ {off}: trailer {trail} != leader {n}")
    return buf[off + 4: off + 4 + n], off + 8 + n


def load_byu_hrwind(path):
    raw = open(path, 'rb').read()
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)

    off = 0
    p, off = _rec(raw, off, 20)
    ngrid, wctgrid, watgrid, ctgrid, atgrid = np.frombuffer(p, BE_I4)
    p, off = _rec(raw, off, 16)
    natstart, natend, nctstart, nctend = np.frombuffer(p, BE_I4)
    p, off = _rec(raw, off, 16)
    iastart, iaend, icstart, icend = np.frombuffer(p, BE_I4)
    p, off = _rec(raw, off, 16)
    ascnode = float(np.frombuffer(p, BE_F4, 1)[0])
    irev, iyear, iday = (int(x) for x in np.frombuffer(p, BE_I4, 3, 4))

    nat = int(iaend - iastart + 1)
    nct = int(icend - icstart + 1)

    hdr = dict(ngrid=int(ngrid), wctgrid=int(wctgrid), watgrid=int(watgrid),
               ctgrid=int(ctgrid), atgrid=int(atgrid),
               natstart=int(natstart), natend=int(natend),
               nctstart=int(nctstart), nctend=int(nctend),
               iastart=int(iastart), iaend=int(iaend),
               icstart=int(icstart), icend=int(icend),
               ascnode_lon=ascnode, rev=irev, year=iyear, doy=iday,
               nat=nat, nct=nct)

    row_words = 2 + 9 * nct + 2 * nct + 2          # int16 words per row record
    body = np.frombuffer(raw, BE_I2, nat * row_words, off).reshape(nat, row_words)

    # validate every row's Fortran markers (int32 = two int16 words)
    mk = body[:, :2].astype('>i2').view(BE_I4) if False else None
    lead = (body[:, 0].astype(np.int64) << 16) | (body[:, 1].astype(np.int64) & 0xFFFF)
    tail = (body[:, -2].astype(np.int64) << 16) | (body[:, -1].astype(np.int64) & 0xFFFF)
    if not (np.all(lead == 22 * nct) and np.all(tail == 22 * nct)):
        raise ValueError("row record markers != 22*nct")

    wind = body[:, 2:2 + 9 * nct].reshape(nat, nct, 9)
    nchoice = wind[:, :, 0].astype(np.int16)
    a = wind[:, :, 1:9].astype(np.float32)
    speeds = a[:, :, 0::2] / 200.0                  # (nat, nct, 4) m/s
    dirs = a[:, :, 1::2] / 180.0                    # (nat, nct, 4) deg

    locw = body[:, 2 + 9 * nct: 2 + 11 * nct].reshape(nat, nct, 2)
    loc0 = locw[:, :, 0].astype(np.int32)
    land = (loc0 < 0)
    lat = np.abs(loc0) / 180.0 - 90.0
    lon = locw[:, :, 1].astype(np.float32) / 180.0

    # selected ambiguity, exactly as loadBYUhrwind.pro
    speed = speeds[:, :, 1].copy()
    wdir = dirs[:, :, 1].copy()
    sel = nchoice > 0
    idx = np.clip(nchoice - 1, 0, 3)
    ii, jj = np.nonzero(sel)
    speed[ii, jj] = speeds[ii, jj, idx[ii, jj]]
    wdir[ii, jj] = dirs[ii, jj, idx[ii, jj]]

    return dict(header=hdr, speed=speed, dir=wdir, lat=lat, lon=lon,
                land=land, nchoice=nchoice, speeds=speeds, dirs=dirs)


if __name__ == '__main__':
    import sys
    d = load_byu_hrwind(sys.argv[1])
    h = d['header']
    print("header:", h)
    sp, nch, land = d['speed'], d['nchoice'], d['land']
    valid = (nch > 0) & ~land
    print(f"dims nat x nct = {h['nat']} x {h['nct']}")
    print(f"nchoice values: {dict(zip(*[list(x) for x in np.unique(nch, return_counts=True)]))}")
    print(f"land frac {land.mean():.3f}  valid(ocean,nchoice>0) frac {valid.mean():.3f}")
    v = sp[valid]
    print(f"speed valid: min {v.min():.2f} max {v.max():.2f} mean {v.mean():.2f} "
          f"p50 {np.percentile(v,50):.2f} p99 {np.percentile(v,99):.2f}")
    print(f"dir valid: min {d['dir'][valid].min():.1f} max {d['dir'][valid].max():.1f}")
    print(f"lat range {d['lat'].min():.3f}..{d['lat'].max():.3f}  "
          f"lon range {d['lon'].min():.3f}..{d['lon'].max():.3f}")
    print(f"raw speed int16 range: {int(d['speeds'].min()*200)}..{int(d['speeds'].max()*200)}")
