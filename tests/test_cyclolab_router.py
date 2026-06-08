"""Edge side of the CycloLab cross-repo contract (Stage-0 companion).

Runs the VENDORED Worker's pure resolve() under node and pins its
path->R2-key mapping to the exact strings the poller-side writer
produces (tat-satellite-render: cyclolab_pages.page_key/adv_key +
R2Sink.write_html). The deployed Worker (v8b77a818, Stage-0
gate-verified live) must match workers/cyclolab-router.js byte-for-byte
per the vendoring rule, so pinning the vendored copy pins production.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKER = REPO / "workers" / "cyclolab-router.js"
NODE = shutil.which("node")

CASES = {
    "/cyclolab/NHC_EP012026/": {"kind": "key",
                                "key": "cyclolab/NHC_EP012026/index.html"},
    "/cyclolab/NHC_AL052026/": {"kind": "key",
                                "key": "cyclolab/NHC_AL052026/index.html"},
    "/cyclolab/adv/NHC_EP012026.json": {
        "kind": "key", "key": "cyclolab/adv/NHC_EP012026.json"},
    "/cyclolab/": {"kind": "key", "key": "cyclolab/index.html"},
    "/cyclolab": {"kind": "key", "key": "cyclolab/index.html"},
    "/cyclolab/NHC_EP012026": {"kind": "redirect",
                               "to": "/cyclolab/NHC_EP012026/"},
    "/elsewhere": {"kind": "notfound"},
}


@unittest.skipIf(NODE is None, "node not on PATH")
class TestRouterResolve(unittest.TestCase):

    def test_resolve_matches_writer_contract(self):
        # The worker is an ESM module; import it via a data-URL shim so no
        # package.json type field is needed.
        script = (
            "const cases = JSON.parse(process.argv[1]);"
            f"import('file://{WORKER}').then(m => {{"
            "  const out = {};"
            "  for (const p of Object.keys(cases)) out[p] = m.resolve(p);"
            "  console.log(JSON.stringify(out));"
            "});"
        )
        proc = subprocess.run(
            [NODE, "--input-type=module", "-e", script, json.dumps(CASES)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        got = json.loads(proc.stdout)
        for path, want in CASES.items():
            with self.subTest(path=path):
                actual = got[path]
                self.assertEqual(actual.get("kind"), want["kind"], actual)
                for k in ("key", "to"):
                    if k in want:
                        self.assertEqual(actual.get(k), want[k], actual)


if __name__ == "__main__":
    unittest.main(verbosity=2)
