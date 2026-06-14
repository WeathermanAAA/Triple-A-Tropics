"""Discoverable wrapper that runs the publish-script integration harness
(tests/test_enscenters_publish_manifest.sh) under `python -m unittest discover
tests`. The shell harness drives the REAL publish script + reconcile guard
against a fake `aws` CLI backed by a temp dir (mock R2)."""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SH = os.path.join(HERE, "test_enscenters_publish_manifest.sh")


class TestPublishManifestScript(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "bash required")
    def test_publish_script_integration(self):
        r = subprocess.run(["bash", SH], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
        self.assertIn("FAIL=0", r.stdout)


if __name__ == "__main__":
    unittest.main()
