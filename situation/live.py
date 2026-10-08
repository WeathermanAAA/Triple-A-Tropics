"""Situation Room live writer: rebuild every storm bundle about every 20 s and publish only what changed.

  python situation/live.py --minutes 175 [--every 20] [--dry-run]

Runs as one long GitHub Actions job (update-situation.yml) that the next scheduled run replaces, so the feed is
continuous instead of riding GitHub's load-shed 10-minute cron (which in practice fired every 15-35 min).
Each pass: NHC CurrentStorms + advisory KMZs + text, decks, recon, microwave (storm_bundle's per-URL cache sets each
source's re-read interval), then compares each document to what was last published, ignoring the "generated" stamp,
and uploads only changed files. situation/live.json is a ~200-byte heartbeat the page polls every few seconds: it holds
a content hash per document, so the browser re-downloads a 190 KB bundle only when something in it actually changed.
"""
import argparse, hashlib, json, os, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import build_situation as BS
import vis as VIS

BUCKET = "s3://triple-a-tropics-media/situation/"
HEARTBEAT_S = 120


def digest(path):
    d = json.load(open(path))
    if isinstance(d, dict): d.pop("generated", None)
    return hashlib.sha1(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def put(path, key, cache, dry, ctype="application/json"):
    if dry: print(f"  would upload {key}"); return True
    r = subprocess.run(["aws", "s3", "cp", path, BUCKET + key, "--endpoint-url", os.environ["R2_ENDPOINT"], "--content-type", ctype,
                        "--cache-control", cache, "--only-show-errors"], capture_output=True, text=True, timeout=60)
    if r.returncode: print(f"  upload {key} failed: {r.stderr.strip()[:200]}", flush=True)
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--minutes", type=float, default=175); ap.add_argument("--every", type=float, default=20)
    ap.add_argument("--out-dir", default="situation_live"); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args()
    end = time.time() + a.minutes * 60; od = os.path.join(a.out_dir, "situation"); sent, beat, n = {}, 0, 0
    # visible imagery for open rooms runs beside the feed (a 60 MB file per frame must not stall the 20 s loop)
    vw = VIS.VisWriter(lambda p, k, c, t: put(p, k, c, a.dry_run, t), a.out_dir, a.dry_run)
    fw = VIS.FDWriter(lambda p, k, c, t: put(p, k, c, a.dry_run, t), a.out_dir, a.dry_run)   # IR/WV BT for storms outside the 5-min sectors
    def vis_loop():
        while time.time() < end:
            try:
                st = []
                for f in os.listdir(od) if os.path.isdir(od) else []:
                    if f.endswith(".json") and f not in ("index.json", "rooms.json", "live.json"):
                        d = json.load(open(os.path.join(od, f)))
                        if d.get("room_open") and d.get("nhc"): st.append({"sid": d["sid"], "lon": float(d["nhc"]["longitudeNumeric"]), "lat": float(d["nhc"]["latitudeNumeric"])})
                vw.tick(st); fw.tick(st)
            except Exception as ex: print(f"vis loop: {ex}", flush=True)
            time.sleep(60)
    threading.Thread(target=vis_loop, daemon=True).start()
    while time.time() < end:
        t0 = time.time(); n += 1
        try:
            BS.build(a.out_dir, BS.load_rooms(live=True))
        except Exception as e:
            print(f"pass {n}: build failed: {e}", flush=True); time.sleep(a.every); continue
        changed = []
        for f in sorted(os.listdir(od)):
            if not f.endswith(".json") or f == "live.json": continue
            h = digest(os.path.join(od, f))
            if sent.get(f) != h and put(os.path.join(od, f), f, "public, max-age=15", a.dry_run):
                sent[f] = h; changed.append(f)
        if changed or time.time() - beat > HEARTBEAT_S:
            live = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "every_s": a.every, "h": {f[:-5]: h for f, h in sent.items()}}
            json.dump(live, open(os.path.join(od, "live.json"), "w"), separators=(",", ":"))
            if put(os.path.join(od, "live.json"), "live.json", "no-store, max-age=0", a.dry_run): beat = time.time()
        print(f"pass {n}: {time.time() - t0:.1f}s, {len(changed)} changed{(': ' + ', '.join(changed)) if changed else ''}", flush=True)
        time.sleep(max(1.0, a.every - (time.time() - t0)))


if __name__ == "__main__":
    main()
