from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_portworx_images", ROOT / "scripts" / "validate-portworx-images.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PortworxImageInventoryTests(unittest.TestCase):
    def test_canonical_inventory_is_exact_and_digest_pinned(self) -> None:
        self.assertEqual(MODULE.validate(ROOT / "mirror" / "portworx-images.yaml"), [])

    def test_rejects_obsolete_helper_and_wrong_count(self) -> None:
        document = yaml.safe_load((ROOT / "mirror" / "portworx-images.yaml").read_text())
        images = document["mirror"]["additionalImages"]
        images.pop()
        images[12]["name"] = images[12]["name"].replace("v1.32.10", "v1.32.0")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "images.yaml"
            path.write_text(yaml.safe_dump(document), encoding="utf-8")
            errors = MODULE.validate(path)
        self.assertTrue(any("expected 38" in error for error in errors))
        self.assertTrue(any("obsolete Kubernetes v1.32.0" in error for error in errors))

    def test_rejects_valid_looking_non_helper_substitution(self) -> None:
        document = yaml.safe_load((ROOT / "mirror" / "portworx-images.yaml").read_text())
        document["mirror"]["additionalImages"][0]["name"] = (
            "docker.io/example/replacement:3.7.0@sha256:" + "0" * 64
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "images.yaml"
            path.write_text(yaml.safe_dump(document), encoding="utf-8")
            errors = MODULE.validate(path)
        self.assertTrue(any("approved exact 38-reference contract" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
