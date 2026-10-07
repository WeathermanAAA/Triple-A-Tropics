"""Bake the Situation Room basemap (the Tulsa-Live map look, situation/tiles.py) for the NHC basins and write it as
static XYZ tiles for R2:  <out>/situation/tiles/{base,lines,roads}/{z}/{x}/{y}.{jpg,png}

  python situation/bake_tiles.py --out-dir situation_tiles [--zmin 2 --zmax 6] [--bbox -180,-2,-5,55]

The page uses maxzoom 6 and overzooms past it. Baked once (dispatch); the map is static, nothing here goes stale.
"""
import argparse, math, os, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tiles as TILES

def tx(lon, z): return int((lon + 180) / 360 * 2 ** z)
def ty(lat, z): r = math.radians(lat); return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="situation_tiles"); ap.add_argument("--zmin", type=int, default=2); ap.add_argument("--zmax", type=int, default=6)
    ap.add_argument("--bbox", default="-180,-2,-5,55"); ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--kinds", default="base,lines,roads", help="which tile sets to bake (e.g. just lines after a lines-style change)")
    a = ap.parse_args(); w, s, e, n = map(float, a.bbox.split(","))
    jobs = []
    for z in range(a.zmin, a.zmax + 1):
        for x in range(tx(w, z), min(tx(e, z), 2 ** z - 1) + 1):
            for y in range(ty(n, z), min(ty(s, z), 2 ** z - 1) + 1):
                for kind in a.kinds.split(","):
                    if kind == "roads" and z < 5: continue
                    jobs.append((kind, z, x, y))
    print(f"baking {len(jobs)} tiles", flush=True)
    done = [0]
    def one(j):
        kind, z, x, y = j
        p = os.path.join(a.out_dir, "situation", "tiles", kind, str(z), str(x), f"{y}.{'jpg' if kind == 'base' else 'png'}")
        if os.path.exists(p): return
        try: data = TILES.tile(kind, z, x, y)
        except Exception as ex: print("failed", j, ex, flush=True); return
        os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(data)
        done[0] += 1
        if done[0] % 100 == 0: print(f"  {done[0]}/{len(jobs)}", flush=True)
    with ThreadPoolExecutor(a.workers) as ex: list(ex.map(one, jobs))
    print("baked", done[0], flush=True)

if __name__ == "__main__":
    main()
