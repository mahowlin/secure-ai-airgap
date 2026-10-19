from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROMOTE = ROOT / "scripts/promote-image-policy-bundle.py"


def write_fixture(root: Path) -> None:
    mirror_dir = root / "mirror"
    artifact_dir = root / "idms-download"
    mirror_dir.mkdir(parents=True)
    artifact_dir.mkdir()

    for filename, image in (
        ("redhat-operators.yaml", "example.com/team/operator:v1"),
        ("isovalent-images.yaml", "example.com/team/operand:v1@sha256:" + "1" * 64),
        ("other-images.yaml", "example.com/team/utility:v1"),
    ):
        (mirror_dir / filename).write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "mirror.openshift.io/v2alpha1",
                    "kind": "ImageSetConfiguration",
                    "mirror": {"additionalImages": [{"name": image}]},
                },
                sort_keys=False,
            )
        )

    (mirror_dir / "isovalent-selection.yaml").write_text(yaml.safe_dump({"imageSet": "mirror/isovalent-images.yaml", "contracts": [{"path": "mirror/release.yaml", "mode": "package-only"}]}))
    (mirror_dir / "release.yaml").write_text(yaml.safe_dump({"clifeBundle": {"controllerImage": "example.com/team/operand:v1", "controllerDigest": "sha256:" + "1" * 64}, "runtimeImages": []}))
    (mirror_dir / "mirror-policy-sources.yaml").write_text("configs: {}\n")
    (artifact_dir / "idms-fixture.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "config.openshift.io/v1",
                "kind": "ImageDigestMirrorSet",
                "metadata": {"name": "idms-fixture"},
                "spec": {
                    "imageDigestMirrors": [
                        {
                            "source": "example.com/team",
                            "mirrors": ["registry.example.test:5000/team"],
                        }
                    ]
                },
            },
            sort_keys=False,
        )
    )


def promote(root: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(PROMOTE),
            "--scope",
            "platform-day2",
            "--artifact-dir",
            "idms-download",
            "--output-root",
            "mirror/bundles",
            "--registry",
            "registry.example.test:5000",
            "--sync-target",
            "all",
            "--run-id",
            "123456",
            "--workflow",
            "Synthetic Bundle Test",
            "--profile",
            "ocp-4.19-cilium-1.19",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class ImagePolicyBundleReproducibilityTests(unittest.TestCase):
    def test_selection_release_and_ca_bytes_are_bound_to_bundle_identity(self):
        with tempfile.TemporaryDirectory(prefix="bundle-inputs-", dir=ROOT) as directory:
            root = Path(directory)
            write_fixture(root)
            selection_path = root / "mirror/isovalent-selection.yaml"
            selection = yaml.safe_load(selection_path.read_text())
            selection["destination"] = {"registry": "registry.example.test:5000", "caBundle": "mirror/site-ca.pem"}
            selection_path.write_text(yaml.safe_dump(selection))
            ca = root / "mirror/site-ca.pem"
            ca.write_text("synthetic CA input, never used for TLS\n")
            def identity():
                promote(root)
                return json.loads((root / "mirror/bundles/platform-day2/current.json").read_text())["specHash"]
            first = identity()
            contract = root / "mirror/release.yaml"
            contract.write_text(contract.read_text() + "\n# refreshed source evidence\n")
            second = identity()
            self.assertNotEqual(first, second)
            ca.write_text(ca.read_text() + "changed bytes\n")
            third = identity()
            self.assertNotEqual(second, third)
            selection["runtimeImages"] = ["example.com/team/operand:v1@sha256:" + "1" * 64]
            selection_path.write_text(yaml.safe_dump(selection))
            self.assertNotEqual(third, identity())

    def test_identical_promotions_produce_identical_complete_bundle_trees(self):
        with tempfile.TemporaryDirectory(prefix="bundle-repro-", dir=ROOT) as directory:
            base = Path(directory)
            first = base / "first"
            second = base / "second"
            write_fixture(first)
            write_fixture(second)

            promote(first)
            promote(second)

            first_root = first / "mirror/bundles"
            second_root = second / "mirror/bundles"
            first_tree = tree_bytes(first_root)
            second_tree = tree_bytes(second_root)
            self.assertEqual(first_tree, second_tree)

            current = json.loads(
                first_tree["platform-day2/current.json"].decode()
            )
            version = current["version"]
            bundle_path = f"platform-day2/{version}/bundle.yaml"
            manifest_path = (
                f"platform-day2/{version}/manifests/"
                "01-platform-day2-idms-fixture.yaml"
            )
            self.assertEqual(
                set(first_tree),
                {"platform-day2/current.json", bundle_path, manifest_path},
            )

            bundle = yaml.safe_load(first_tree[bundle_path])
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip()
            commit_time = subprocess.check_output(
                ["git", "show", "-s", "--format=%cI", commit],
                cwd=ROOT,
                text=True,
            ).strip()
            expected_time = (
                dt.datetime.fromisoformat(commit_time)
                .astimezone(dt.UTC)
                .isoformat(timespec="seconds")
            )

            self.assertEqual(bundle["metadata"]["createdAt"], expected_time)
            self.assertEqual(bundle["spec"]["sysAdminCommit"], commit)
            self.assertEqual(bundle["spec"]["workflowRun"], "123456")
            self.assertEqual(bundle["metadata"]["specHash"], current["specHash"])
            self.assertEqual(bundle["metadata"]["contentHash"], current["contentHash"])
            self.assertEqual(bundle["metadata"]["version"], version)


if __name__ == "__main__":
    unittest.main()
