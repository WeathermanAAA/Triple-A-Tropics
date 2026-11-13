#!/usr/bin/env python3
"""Winter Storm Center automatic run (v3, impact-based).

Usage: python3 wsc_auto.py wsc_grid.json [previous_latest.json] > out.json

For every US county, pulls the NWS gridded forecast (next 72 h), rates the expected
local impact (Level 1 Minor .. 5 Extreme) from snow, ice, wind and cold, groups
neighboring impacted counties into storm footprints, scores each footprint with the
WSC Storm Index (impact level weighted by population and area) to get its category
W1-W5, names lower-48 storms at W1, and issues county signals (= local impact level)
only inside named storms' footprints. Prints the new wsc/latest document as JSON.
"""
import json, sys, re, time, datetime as dt
import concurrent.futures as cf, urllib.request

UA = {'User-Agent': 'WinterStormCenter personal project (coloradoskier2018@gmail.com)',
      'Accept': 'application/geo+json'}
HOURS = 72
PEAK_LINK = 18      # h: neighboring counties belong to the same storm only if their peaks are this close

# ---- county impact scale (must match the page) ----
SNOW_T = [0.15, 0.30, 0.50, 0.75, 1.0]      # share of local 24-h record
SNOW_ABS = [6, 12, 18, 24, 30]              # inches; snow level = higher of the two
ICE_T = [0.05, 0.10, 0.25, 0.50, 0.75]      # inches flat accretion
WIND_T = [25, 35, 45, 55, 65]               # mph gust while snowing
COLD_T = [10, 0, -10, -20, -30]             # F wind chill, only with snow or ice

# ---- storm index (must match the page) ----
LEVEL_W = {1: 1, 2: 3, 3: 9, 4: 27, 5: 81}
POP_W, AREA_W = 0.6, 0.4                    # population counts a bit more than area
POP_REF, AREA_REF = 1_000_000, 50_000       # people, km2
CAT_T = [1, 6, 25, 80, 200]                 # index thresholds for W1..W5
NAME_POP, NAME_AREA = 250_000, 10_000       # naming floors: people and km2 expecting impact Level 1+ (signals come after naming)

LISTS = {
    1: 'Aiden Bella Chris Dennis Everett Frances Gretel Hayden Ira James Kamila Lane Mateo Noah Opal Pearl Quincy Ronan Solomon Thomas Uriel Vince Walden Xander Yardley Zane'.split(),
    2: 'Asher Blanche Carson Diana Eli Fleur Gerald Holly Iver Jake Kendall Lawerence Melanie Nemo Onyx Penelope Quito Raven Simon Theresa Ulan Vera Wilson Xara Yolanda Zenette'.split(),
    3: 'Amber Beau Chloe David Elara Finn Gaia Hadrian Inara Jaxxon Kiara Lysander Mira Nyx Orian Pandora Quillan Rhea Sylvie Thalia Ulric Vanessa Willow Xanthe Yale Zoe'.split(),
}
AUX = 'Andrew Bernice Caroline Diego Eleanor Flynn Gloria Hunter Isabella Jacob Kassan Laura Maria Nicholas Odelia Preston Rachel Steven Tanya Victor Wanda'.split()


def level(v, th, desc=False):
    l = 0
    for i, t in enumerate(th):
        if (v <= t) if desc else (v >= t):
            l = i + 1
    return l


def county_level(snow, s24, ice, gust, chill, thr):
    L = {'snow': max(level(snow / thr, SNOW_T), level(snow, SNOW_ABS)), 'ice': level(ice, ICE_T)}
    # wind needs snow and rates at most one level above it; cold needs snow or ice
    L['wind'] = min(level(gust, WIND_T), L['snow'] + 1) if L['snow'] >= 1 else 0
    L['cold'] = level(chill, COLD_T, True) if (L['snow'] >= 1 or L['ice'] >= 1) else 0
    mx = max(L['snow'], L['ice'], L['wind'])
    strong = sum(1 for k in ('snow', 'ice', 'wind') if L[k] >= 3)
    lvl = mx
    if mx and mx < 5 and (strong >= 2 or (L['cold'] >= 3 and max(L['snow'], L['ice']) >= 2)):
        lvl = mx + 1    # compounding hazards, or dangerous cold on top of a real storm
    sb = s24 >= thr and s24 > 0
    if sb:
        lvl = 5
    return lvl, sb, L


def storm_index(counties, lvl_of, grid):
    tot = 0.0
    for f in counties:
        l = lvl_of(f)
        if l:
            c = grid[f]
            tot += LEVEL_W[l] * (POP_W * c.get('pop', 0) / POP_REF + AREA_W * (c.get('area') or 1500) / AREA_REF)
    return tot


def category(index):
    return level(index, CAT_T)


def parse_dur(s):
    m = re.match(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?', s)
    d, h, mi = (int(x) if x else 0 for x in m.groups())
    return d * 24 + h + (1 if mi else 0)


def hourly(layer, now, kind):
    out = [None] * HOURS
    for v in (layer or {}).get('values', []):
        if v['value'] is None:
            continue
        start_s, dur_s = v['validTime'].split('/')
        start = dt.datetime.fromisoformat(start_s)
        n = max(1, parse_dur(dur_s))
        per = v['value'] / n if kind == 'sum' else v['value']
        for k in range(n):
            i = int((start + dt.timedelta(hours=k) - now).total_seconds() // 3600)
            if 0 <= i < HOURS:
                out[i] = per
    return out


def fetch(grid):
    wfo, x, y = grid
    url = f'https://api.weather.gov/gridpoints/{wfo}/{x},{y}'
    last = None
    for a in range(4):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30)
            return json.load(r)['properties']
        except Exception as e:
            last = e
            time.sleep(2 * (a + 1))
    raise last


def rate(props, now, thr):
    snow = [(v or 0) / 25.4 for v in hourly(props.get('snowfallAmount'), now, 'sum')]
    ice = [(v or 0) / 25.4 for v in hourly(props.get('iceAccumulation'), now, 'sum')]
    gust = [(v or 0) * 0.621371 for v in hourly(props.get('windGust'), now, 'val')]
    app = [None if v is None else v * 9 / 5 + 32 for v in hourly(props.get('apparentTemperature'), now, 'val')]
    total = sum(snow)
    s24 = max(sum(snow[i:i + 24]) for i in range(HOURS - 23))
    ice_t = sum(ice)
    wet = [i for i in range(HOURS) if snow[i] >= 0.01 or ice[i] > 0]
    g = max((gust[i] for i in wet), default=0)
    # wind chill during and up to 12 h after the precipitation
    if wet:
        win = range(wet[0], min(HOURS, wet[-1] + 13))
        chill = min((app[i] for i in win if app[i] is not None), default=99)
    else:
        chill = 99
    lvl, sb, L = county_level(total, s24, ice_t, g, chill, thr)
    onset = wet[0] if wet else None
    end = wet[-1] + 1 if wet else None
    peak = max(wet, key=lambda i: snow[i] + 10 * ice[i]) if wet else None
    return {'lvl': lvl, 'sb': sb, 'L': [L['snow'], L['ice'], L['wind'], L['cold']],
            'onset': onset, 'peak': peak, 'end': end,
            'snow': round(total, 1), 's24': round(s24, 1), 'ice': round(ice_t, 2),
            'gust': round(g), 'chill': None if chill == 99 else round(chill)}


def season_list(now):
    y = now.year if now.month >= 7 else now.year - 1
    return f'{y}-{str(y + 1)[2:]}', {0: 3, 1: 1, 2: 2}[(y - 2024) % 3]


def build(results, grid, prev, now, failed=()):
    # ---- footprints ----
    # A storm is built around its core: touching counties at Level 2 or higher whose peaks fall within
    # PEAK_LINK hours of each other. Two cores join only
    # when a single Level 1 county bridges them. Level 1 fringe counties then attach to the nearest
    # core through other Level 1 counties. Light snow alone never chains separate systems together.
    nb = lambda f: grid[f].get('nb', [])
    active = {f for f, r in results.items() if r['lvl'] >= 1}
    core = {f for f in active if results[f]['lvl'] >= 2}
    parent = {f: f for f in core}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(x, y):
        parent[find(x)] = find(y)
    pk = lambda f: results[f]['peak'] if results[f]['peak'] is not None else -99
    same_time = lambda x, y: abs(pk(x) - pk(y)) <= PEAK_LINK
    for f in core:
        for n in nb(f):
            if n in core and same_time(f, n):
                union(f, n)
    for f in active - core:                     # one-county Level 1 bridge between cores
        cs = [n for n in nb(f) if n in core and same_time(f, n)]
        for n in cs[1:]:
            union(cs[0], n)
    owner = {f: find(f) for f in core}
    frontier = sorted(core)
    while frontier:                             # grow cores outward through Level 1 counties
        nxt = []
        for f in frontier:
            for n in nb(f):
                if n in active and n not in owner:
                    owner[n] = owner[f]
                    nxt.append(n)
        frontier = sorted(set(nxt))
    # second pass: two touching pieces (cores plus their fringe) are one storm when the counties along
    # their shared border peak at about the same time (median of each side). This stops noisy mountain
    # timing and one-county gaps from splitting one system into fragments, while a storm sweeping across the
    # country stays separate from a different system it brushes against.
    med = lambda v: sorted(v)[len(v) // 2]
    near2 = {f: set(nb(f)).union(*[set(nb(n)) for n in nb(f)]) - {f} for f in owner}   # allow a one-county gap
    while True:
        border = {}
        for f in owner:
            for n in near2[f]:
                if n in owner and find(owner[f]) != find(owner[n]):
                    ga, gb = find(owner[f]), find(owner[n])
                    key = (min(ga, gb), max(ga, gb))
                    side = border.setdefault(key, ({}, {}))
                    side[0 if ga == key[0] else 1][f] = pk(f)
        merged = False
        for (ga, gb), (sa, sb_) in border.items():
            if abs(med(list(sa.values())) - med(list(sb_.values()))) <= PEAK_LINK:
                union(ga, gb)
                merged = True
                break
        if not merged:
            break
    owner = {f: find(o) for f, o in owner.items()}
    groups = {}
    for f, o in owner.items():
        groups.setdefault(o, set()).add(f)
    clusters = list(groups.values())
    seen = set(owner)                           # Level 1 patches touching no core stay separate areas
    for f in sorted(active - seen):
        if f in seen:
            continue
        comp, stack = set(), [f]
        seen.add(f)
        while stack:
            x = stack.pop()
            comp.add(x)
            for n in nb(x):
                if n in active and n not in seen:
                    seen.add(n)
                    stack.append(n)
        clusters.append(comp)

    # ---- naming ----
    season, lst = season_list(now)
    st = dict(prev.get('state') or {})
    if st.get('season') != season:
        st = {'season': season, 'list': lst, 'idx': 0, 'used': []}
    names = LISTS[st['list']] + AUX
    prev_storms = [p for p in (prev.get('storms') or []) if p.get('name')]
    claimed, storms = set(), []
    lvl_of = lambda f: results[f]['lvl']
    for comp in sorted(clusters, key=lambda c: storm_index(c, lvl_of, grid), reverse=True):
        idx = storm_index(comp, lvl_of, grid)
        cat = category(idx)
        lower48 = any(f[:2] not in ('02', '15') for f in comp)
        sig2 = [f for f in comp if results[f]['lvl'] >= 1]   # expected impact Level 1+, not signals
        pop2 = sum(grid[f].get('pop', 0) for f in sig2)
        area2 = sum(grid[f].get('area') or 1500 for f in sig2)
        eligible = lower48 and cat >= 1 and pop2 >= NAME_POP and area2 >= NAME_AREA
        best, ov = None, 0
        for p in prev_storms:
            o = len(comp & set(p['counties']))
            if o > ov and p['name'] not in claimed:
                best, ov = p, o
        if not lower48:
            best = None
        if best:
            claimed.add(best['name'])
            s = {'name': best['name'], 'adv': best['adv'] + 1, 'first': best['first'], 'peakCat': max(best.get('peakCat', 0), cat)}
        elif eligible:
            nm = names[st['idx']] if st['idx'] < len(names) else f"Storm {st['idx'] + 1}"
            st['idx'] += 1
            st['used'] = (st.get('used') or []) + [nm]
            s = {'name': nm, 'adv': 1, 'first': now.isoformat(), 'peakCat': cat}
        else:
            s = {'name': None, 'adv': 0, 'first': now.isoformat(), 'peakCat': cat}
        comp_l = sorted(comp)
        on = [results[f]['onset'] for f in comp_l if results[f]['onset'] is not None]
        en = [results[f]['end'] for f in comp_l if results[f]['end'] is not None]
        lv = {}
        for f in comp_l:
            lv[str(results[f]['lvl'])] = lv.get(str(results[f]['lvl']), 0) + 1
        s.update({'index': round(idx, 2), 'cat': cat, 'pop2': pop2, 'area2': round(area2), 'maxLvl': max(results[f]['lvl'] for f in comp_l),
                  'sb': any(results[f]['sb'] for f in comp_l), 'n': len(comp_l), 'levels': lv,
                  'pop': sum(grid[f].get('pop', 0) for f in comp_l),
                  'states': sorted({grid[f]['s'] for f in comp_l}),
                  'onset': min(on) if on else None, 'end': max(en) if en else None,
                  'counties': comp_l})
        storms.append(s)
    storms.sort(key=lambda x: (x['name'] is None, -x['index']))

    counts = {}
    for s in storms:
        if s['name']:
            for k, v in s['levels'].items():
                counts[k] = counts.get(k, 0) + v
    # ---- season archive: one record per named storm, kept all season for the timeline ----
    if 'archive' not in st:   # first run with an archive: rebuild what the run history still remembers
        arc = {}
        for h in (prev.get('history') or []):
            if h.get('v') != 3 or season_list(dt.datetime.fromisoformat(h['run']))[0] != season:
                continue
            for nm in h.get('storms') or []:
                a = arc.setdefault(nm, {'name': nm, 'first': h['run'], 'last': h['run'], 'peakCat': 0, 'peakSig': 0, 'states': []})
                a['last'] = h['run']
                if len(h['storms']) == 1:
                    a['peakCat'] = max(a['peakCat'], h.get('maxCat', 0))
                    a['peakSig'] = max(a['peakSig'], max((int(k) for k in (h.get('counts') or {})), default=0))
        st['archive'] = sorted(arc.values(), key=lambda a: a['first'])
    arch = {a['name']: a for a in st['archive']}
    for s in storms:
        if not s['name']:
            continue
        a = arch.get(s['name'])
        if not a:
            a = {'name': s['name'], 'first': s['first'], 'last': now.isoformat(), 'peakCat': 0, 'peakSig': 0, 'states': []}
            st['archive'].append(a); arch[s['name']] = a
        a['last'] = now.isoformat()
        a['peakCat'] = max(a['peakCat'], s['cat'])
        a['peakSig'] = max(a['peakSig'], s['maxLvl'])
        a['states'] = sorted(set(a['states']) | set(s['states']))
        a['onsetEnd'] = [s['onset'], s['end']]   # latest forecast window, hours after this run

    hist = [h for h in (prev.get('history') or []) if h.get('v') == 3][-59:]
    hist.append({'v': 3, 'run': now.isoformat(), 'counts': counts,
                 'storms': [s['name'] for s in storms if s['name']],
                 'maxCat': max((s['cat'] for s in storms if s['name']), default=0)})
    return {
        'v': 3,
        'run': now.isoformat(),
        'source': 'NWS gridded forecast, next 72 h',
        'failed': list(failed),
        'state': st,
        'storms': storms,
        'counts': counts,
        'history': hist,
        # footprint counties only: [lvl, onset, peak, end, snow, s24, ice, gust, chill, sb, Lsnow, Lice, Lwind, Lcold]
        'c': {f: [r['lvl'], r['onset'], r['peak'], r['end'], r['snow'], r['s24'], r['ice'], r['gust'], r['chill'],
                  1 if r['sb'] else 0] + r['L'] for f, r in results.items() if r['lvl'] >= 1},
    }


# ================= National Blend of Models (lower 48) =================
NBM = 'https://noaa-nbm-grib2-pds.s3.amazonaws.com'
STEPS = list(range(6, HOURS + 1, 6))


def _get(url, rng=None):
    h = {'User-Agent': UA['User-Agent']}
    if rng:
        h['Range'] = f'bytes={rng[0]}-{rng[1]}'
    last = None
    for a in range(4):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60).read()
        except Exception as e:
            last = e
            time.sleep(2 * (a + 1))
    raise last


def nbm_cycle(now):
    """Latest NBM cycle whose 72-h file is published."""
    t = now.replace(hour=now.hour - now.hour % 6)
    for k in range(6):
        c = t - dt.timedelta(hours=6 * k)
        url = f"{NBM}/blend.{c:%Y%m%d}/{c:%H}/core/blend.t{c:%H}z.core.f{HOURS:03d}.co.grib2.idx"
        try:
            _get(url)
            return c
        except Exception:
            continue
    raise RuntimeError('no complete NBM cycle found')


def nbm_fields(cycle, step):
    import pygrib, numpy as np
    base = f"{NBM}/blend.{cycle:%Y%m%d}/{cycle:%H}/core/blend.t{cycle:%H}z.core.f{step:03d}.co.grib2"
    lines = _get(base + '.idx').decode().splitlines()
    want = {'snow': f'ASNOW:surface:{step-6}-{step} hour acc fcst:', 'ice': f'FICEAC:surface:{step-6}-{step} hour acc fcst:',
            'gust': f'GUST:10 m above ground:{step} hour fcst:', 'app': f'APTMP:2 m above ground:{step} hour fcst:'}
    out = {}
    for i, ln in enumerate(lines):
        for k, pat in want.items():
            # NBM v4.0 (2021) idx files list ice accretion only by its code, discipline 0 / cat 1 / parm 228
            alt = f'parmcat=1 parm=228:surface:{step-6}-{step} hour acc fcst:' if k == 'ice' else None
            if k not in out and (ln.endswith(pat) or (alt and ln.endswith(alt))):
                a = int(ln.split(':')[1]); b = int(lines[i + 1].split(':')[1]) - 1 if i + 1 < len(lines) else ''
                g = pygrib.fromstring(_get(base, (a, b)))
                v = g.values
                out[k] = np.ma.filled(v.astype('float32'), np.nan) if hasattr(v, 'mask') else v.astype('float32')
    return out


def nbm_results(grid, index_path, cycle):
    import numpy as np, base64, io
    ix = json.load(open(index_path))
    lab = np.load(io.BytesIO(base64.b64decode(ix['npz_b64'])))['lab'].astype(np.int32)
    order = ix['order']
    for f, (r, c) in ix['extra'].items():          # tiny counties: give them their centroid cell
        if 0 <= r < lab.shape[0] and 0 <= c < lab.shape[1] and lab[r, c] == 0:
            lab[r, c] = order.index(f) + 1
    L = lab.ravel(); n = len(order) + 1
    sel = np.nonzero(L)[0]; Ls = L[sel]
    srt = np.argsort(Ls, kind='stable'); Lsorted = Ls[srt]
    bounds = np.searchsorted(Lsorted, np.arange(1, n + 1))
    def county_q(arr, q):
        v = arr.ravel()[sel][srt]
        out = np.full(n, np.nan, dtype='float32')
        for k in range(1, n):
            seg = v[bounds[k - 1]:bounds[k]]
            seg = seg[~np.isnan(seg)]
            if seg.size:
                out[k] = np.percentile(seg, q)
        return out
    cnt = np.bincount(L, minlength=n).astype('float32'); cnt[cnt == 0] = 1
    def county_mean(arr):
        a = np.nan_to_num(arr.ravel())
        return np.bincount(L, weights=a, minlength=n) / cnt

    snow_tot = ice_tot = s24 = gwet = chill = None
    win = []; wet_recent = None; series = []
    for step in STEPS:
        F = nbm_fields(cycle, step)
        sn = np.nan_to_num(F['snow']) * 39.3701                   # m -> in
        ic = np.nan_to_num(F['ice']) / 25.4                       # kg m-2 (mm) -> in
        gu = F['gust'] * 2.23694                                  # m/s -> mph
        ap = (F['app'] - 273.15) * 9 / 5 + 32                     # K -> F
        if snow_tot is None:
            snow_tot = np.zeros_like(sn); ice_tot = np.zeros_like(sn); s24 = np.zeros_like(sn)
            gwet = np.zeros_like(sn); chill = np.full_like(sn, 99.0); wet_recent = np.zeros(sn.shape, dtype=np.int8)
        snow_tot += sn; ice_tot += ic
        win.append(sn)
        if len(win) > 4:
            win.pop(0)
        s24 = np.maximum(s24, sum(win))
        wet = (sn >= 0.01) | (ic > 0.001)
        gwet = np.where(wet, np.fmax(gwet, np.nan_to_num(gu)), gwet)
        wet_recent = np.where(wet, 3, np.maximum(wet_recent - 1, 0)).astype(np.int8)   # wet steps and the 12 h after
        chill = np.where(wet_recent > 0, np.fmin(chill, np.nan_to_num(ap, nan=99.0)), chill)
        series.append(county_mean(sn + 10 * ic))
    tot = county_q(snow_tot, 75); t24 = county_q(s24, 75); ict = county_q(ice_tot, 75)
    gst = county_q(gwet, 75); chl = county_q(chill, 25)
    series = np.array(series)                                     # steps x counties
    results = {}
    for k, f in enumerate(order, start=1):
        if f not in grid:
            continue
        sn, s2, ic, g, ch = [float(np.nan_to_num(x[k])) for x in (tot, t24, ict, gst, chl)]
        lvl, sb, Lv = county_level(sn, s2, ic, g, ch if ch < 98 else 99, grid[f]['thr'])
        col = series[:, k]
        wet_steps = [i for i, v in enumerate(col) if v >= 0.05]
        onset = STEPS[wet_steps[0]] - 3 if wet_steps else None
        end = STEPS[wet_steps[-1]] if wet_steps else None
        peak = STEPS[int(np.argmax(col))] - 3 if wet_steps else None
        results[f] = {'lvl': lvl, 'sb': sb, 'L': [Lv['snow'], Lv['ice'], Lv['wind'], Lv['cold']],
                      'onset': onset, 'peak': peak, 'end': end, 'snow': round(sn, 1), 's24': round(s2, 1),
                      'ice': round(ic, 2), 'gust': round(g), 'chill': None if ch >= 98 else round(ch)}
    return results


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('grid'); ap.add_argument('nbm_index'); ap.add_argument('prev', nargs='?')
    ap.add_argument('--cycle', help='NBM cycle YYYYMMDDHH (default: latest complete)')
    a = ap.parse_args()
    grid = json.load(open(a.grid))['counties']
    prev = {}
    if a.prev:
        try:
            prev = json.load(open(a.prev))
        except Exception:
            prev = {}
    if prev.get('v') != 3:      # older format: keep only the season naming state
        prev = {'state': prev.get('state')} if prev.get('state') else {}
    if a.cycle:
        cycle = dt.datetime.strptime(a.cycle, '%Y%m%d%H').replace(tzinfo=dt.timezone.utc)
    else:
        cycle = nbm_cycle(dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0))
    results = nbm_results(grid, a.nbm_index, cycle)
    failed = []
    # Alaska and Hawaii (monitored only, never named) still come from the NWS gridded forecast
    if not a.cycle:
        akhi = {f: c for f, c in grid.items() if f[:2] in ('02', '15') and c.get('grid')}
        with cf.ThreadPoolExecutor(8) as ex:
            futs = {ex.submit(fetch, c['grid']): f for f, c in akhi.items()}
            for fut in cf.as_completed(futs):
                f = futs[fut]
                try:
                    results[f] = rate(fut.result(), cycle, grid[f]['thr'])
                except Exception:
                    failed.append(f)
    doc = build(results, grid, prev, cycle, failed)
    doc['source'] = f'National Blend of Models {cycle:%Y-%m-%d %H}Z, next 72 h (Alaska and Hawaii: NWS gridded forecast)'
    json.dump(doc, sys.stdout, separators=(',', ':'))
    storms = doc['storms']
    named = ', '.join(f"{s['name']} (W{s['cat']}, {'/'.join(s['states'])})" for s in storms if s['name']) or 'none'
    mon = sum(1 for s in storms if not s['name'])
    print(f"\nNBM {cycle:%Y%m%d %H}Z: rated {len(results)} counties, {len(failed)} failed, signals {doc['counts'] or 'none'}, named storms: {named}, monitored areas: {mon}", file=sys.stderr)


if __name__ == '__main__':
    main()
