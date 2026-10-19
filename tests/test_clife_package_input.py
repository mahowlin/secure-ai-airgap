from __future__ import annotations

import importlib.util
import shutil
import hashlib
import yaml
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "mirror" / "package-inputs" / "clife-install" / "1.19.5"
SCRIPT = ROOT / "scripts" / "verify-clife-package-input.py"
spec = importlib.util.spec_from_file_location("verify_clife_package_input", SCRIPT)
verifier = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(verifier)


REPOSITORY = "mahowlin/secure-ai-datacenter"
REVISION = "b" * 40  # synthetic unit-test revision, not a public promotion
CONTRACT_PATH = "data/package-contracts/clife-install-1.19.5.yaml"


class PromotedClifePackageInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.bundle = Path(cls.directory.name) / "synthetic-promotion"
        shutil.copytree(BUNDLE, cls.bundle)
        artifacts = {}
        for name, (filename, source_path) in verifier.EXPECTED_ARTIFACTS.items():
            artifacts[name] = {"localPath": filename, "sourcePath": source_path or CONTRACT_PATH,
                               "sha256": hashlib.sha256((cls.bundle / filename).read_bytes()).hexdigest()}
        # This receipt exists only in a temporary test fixture. No rewritten lab
        # receipt is shipped or represented as an actual successful promotion.
        document = {"apiVersion": verifier.EXPECTED_API_VERSION, "kind": verifier.EXPECTED_KIND,
                    "metadata": {"name": Path(CONTRACT_PATH).stem},
                    "spec": {"source": {"repository": REPOSITORY, "revision": REVISION}, "artifacts": artifacts}}
        (cls.bundle / "provenance.yaml").write_text(yaml.safe_dump(document, sort_keys=False))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()
    def verify(self, bundle: Path | None = None, revision: str = REVISION):
        return verifier.verify(bundle or self.bundle, REPOSITORY, revision, CONTRACT_PATH)

    def test_synthetic_promoted_fixture_is_complete_and_verified(self):
        verified = self.verify()
        self.assertEqual(set(verified), {"contract", "schema", "validator"})

    def test_requested_revision_must_match_promoted_revision(self):
        with self.assertRaisesRegex(ValueError, "source revision does not match"):
            self.verify(revision="a" * 40)

    def test_changed_promoted_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            shutil.copytree(self.bundle, bundle)
            with (bundle / "contract.yaml").open("a") as stream:
                stream.write("\n# changed after promotion\n")
            with self.assertRaisesRegex(ValueError, "artifact hash differs: contract"):
                self.verify(bundle=bundle)

    def test_unexpected_promoted_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle"
            shutil.copytree(self.bundle, bundle)
            (bundle / "untracked-input").write_text("unexpected")
            with self.assertRaisesRegex(ValueError, "unexpected or missing files"):
                self.verify(bundle=bundle)


if __name__ == "__main__":
    unittest.main()
