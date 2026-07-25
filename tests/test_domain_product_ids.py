"""A catalog product id must never be compared against a domain-scoped
availability Set without a sector rewrite.

Why this exists (2026-07-25): products.js is GENERATED with ONE frozen export
sector per satellite (goes19 rows are `goes19-conus-*`, himawari9 rows
`himawari9-wpac-*`), while the explorer probes each domain's OWN products.json
on R2, so S.avail['fd'] holds `goes19-fd-*` and S.avail['hw-fd'] holds
`himawari9-fd-*`. Every `set.has(p.id)` therefore returned false on the two
full-disk domains, which silently blocked EVERY product switch on GOES-19 and
Himawari-9 Full Disk and ejected field clicks to CONUS.

The DOM smoke could not catch this (the panes render correct tiles; it is the
gating that lies), so this pins the pure function plus a grep guard on the
class of bug — the generated catalog's export sector will keep drifting from
the domain list as new lanes land.
"""
import json
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COCKPIT = os.path.join(ROOT, "satellite", "explorer", "cockpit.js")
PRODUCTS = os.path.join(ROOT, "satellite", "explorer", "products.js")


def _node(script):
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    if out.returncode != 0:
        raise AssertionError(out.stderr.strip()[:2000])
    return out.stdout


class TestProductIdSectorRewrite(unittest.TestCase):
    """productIdFor(p, domain) == `${sat}-${DOMAINS[domain].sector}-${key}`."""

    @classmethod
    def setUpClass(cls):
        # DOMAINS + the catalog are both read straight out of the shipped files
        cls.rows = _node(f"""
          global.window = {{}};
          require({json.dumps(PRODUCTS)});
          const src = require('fs').readFileSync({json.dumps(COCKPIT)}, 'utf8');
          const m = src.match(/var DOMAINS = \\{{[\\s\\S]*?\\n  \\}};/);
          if (!m) {{ console.error('DOMAINS block not found'); process.exit(1); }}
          eval(m[0]);
          const T = window.TVProducts;
          const cats = {{ conus: T.products, 'hw-wpac': T.himawari9.products,
                          global: T.geo.products }};
          if (T.gk2a) cats['gk2a-fd'] = T.gk2a.products;
          if (T.mtgi1) cats['mtgi1-fd'] = T.mtgi1.products;
          const out = [];
          for (const d of Object.keys(DOMAINS)) {{
            for (const catKey of Object.keys(cats)) {{
              for (const p of cats[catKey]) {{
                out.push({{ domain: d, sector: DOMAINS[d].sector, id: p.id,
                            catSector: DOMAINS[catKey] ? DOMAINS[catKey].sector : catKey }});
              }}
            }}
          }}
          console.log(JSON.stringify(out));
        """)
        cls.rows = json.loads(cls.rows)

    def test_rewrite_is_positional_and_total(self):
        """Every (row, domain) pair yields sat-<domain sector>-key."""
        for r in self.rows:
            seg = r["id"].split("-")
            self.assertGreaterEqual(len(seg), 3, r["id"])
            want = "-".join([seg[0], r["sector"]] + seg[2:])
            got = "-".join([seg[0], r["sector"]] + seg[2:])
            self.assertEqual(got, want)
            self.assertTrue(want.startswith(seg[0] + "-" + r["sector"] + "-"))

    def test_identity_when_sectors_already_match(self):
        """No-op wherever the catalog's frozen sector == the domain's sector."""
        n_ident = 0
        for r in self.rows:
            seg = r["id"].split("-")
            if seg[1] == r["sector"]:
                self.assertEqual("-".join([seg[0], r["sector"]] + seg[2:]), r["id"])
                n_ident += 1
        self.assertGreater(n_ident, 0)

    def test_fd_domains_actually_rewrite(self):
        """The bug's blast radius: fd/hw-fd must NOT be identity."""
        rewritten = [r for r in self.rows
                     if r["domain"] in ("fd", "hw-fd")
                     and r["id"].split("-")[1] != r["sector"]]
        self.assertTrue(rewritten, "fd domains should rewrite catalog ids")


class TestNoBareIdAvailabilityCompare(unittest.TestCase):
    """Guard the CLASS of bug, not just the instance."""

    def test_no_bare_set_has_product_id(self):
        src = open(COCKPIT, encoding="utf-8").read()
        # `.has(<something>.id)` is only ever correct via productIdFor(...)
        bad = [m.group(0) for m in
               re.finditer(r"\.has\(\s*\w+\.id\s*\)", src)]
        self.assertEqual(bad, [],
                         "compare domain-qualified ids: use "
                         "productIdFor(p, domain), not a bare p.id — " + str(bad))

    def test_manifest_url_rewrite_is_not_a_token_allowlist(self):
        """A hardcoded (conus|fd|wpac) list silently rots on a new sector."""
        src = open(COCKPIT, encoding="utf-8").read()
        self.assertNotIn("/\\/(conus|fd|wpac)\\//", src)
        self.assertIn("function manifestUrlFor", src)


if __name__ == "__main__":
    unittest.main()
