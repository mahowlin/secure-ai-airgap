"""Exercise production subprocess/stream verification against controlled OCI data."""

import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("graph_audit", ROOT / "mirror/package-inputs/hcp-oc-mirror-operator/verify-image.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
OCI = "application/vnd.oci."
SOURCE = "upstream.example/operator"
TARGET = "registry.example:5000/upstream.example/operator:v1"


class Fixture:
    def __init__(self, *, bad_config=False, wrong_subject=False, bad_subject_size=False,
                 unknown=False, wrong_annotation=False):
        self.manifests, self.blobs, self.calls = {}, {}, []
        self.present, self.fallback, self.semantic_failure = True, {}, False
        children = []
        for arch in ("amd64", "arm64"):
            config = {"os": "linux", "architecture": arch, "rootfs": {"type": "layers"}}
            if bad_config:
                config.pop("rootfs")
            image = {"schemaVersion": 2, "mediaType": OCI + "image.manifest.v1+json",
                     "config": self.blob(config, OCI + "image.config.v1+json"),
                     "layers": [self.blob(b"compressed-layer", OCI + "image.layer.v1.tar+gzip")]}
            child = self.manifest(image)
            child["platform"] = {"os": "linux", "architecture": arch}
            children.append(child)
        subject = copy.deepcopy(children[0])
        subject.pop("platform")
        if bad_subject_size:
            subject["size"] += 1
        statement = {"_type": "https://in-toto.io/Statement/v1", "predicateType": "https://slsa.dev/provenance/v1",
                     "subject": [{"name": "image", "digest": {"sha256": ("0" * 64 if wrong_subject else subject["digest"].split(":")[1])}}]}
        layer = self.blob(statement, M.IN_TOTO)
        layer["annotations"] = {"in-toto.io/predicate-type": statement["predicateType"]}
        self.attestation_blob = layer["digest"]
        attestation = {"schemaVersion": 2, "mediaType": OCI + "image.manifest.v1+json",
                       "artifactType": "unknown/artifact" if unknown else M.ATTESTATION,
                       "config": self.blob({}, M.EMPTY), "layers": [layer], "subject": subject}
        child = self.manifest(attestation)
        child["platform"] = {"os": "unknown", "architecture": "unknown"}
        child["annotations"] = {"vnd.docker.reference.type": "attestation-manifest",
                                "vnd.docker.reference.digest": "sha256:" + "0" * 64 if wrong_annotation else subject["digest"]}
        children.append(child)
        self.fallback_tag = subject["digest"].replace(":", "-", 1)
        self.attestation_descriptor = copy.deepcopy(child)
        self.root = self.manifest({"schemaVersion": 2, "mediaType": OCI + "image.index.v1+json", "manifests": children})["digest"]

    def blob(self, value, media):
        raw = value if isinstance(value, bytes) else json.dumps(value, separators=(",", ":")).encode()
        sha = M.digest(raw)
        self.blobs[sha] = raw
        return {"digest": sha, "size": len(raw), "mediaType": media}

    def manifest(self, value):
        raw = json.dumps(value, separators=(",", ":")).encode()
        sha = M.digest(raw)
        self.manifests[sha] = raw
        return {"digest": sha, "size": len(raw), "mediaType": value["mediaType"]}

    def run(self, command, **kwargs):
        self.calls.append(command)
        if command[1] == "manifest":
            ref = command[2]
            if "@" in ref:
                raw = self.manifests.get(ref.rsplit("@", 1)[1])
            elif ref == TARGET:
                raw = self.manifests[self.root] if self.present else None
            else:
                raw = self.fallback.get(ref.rsplit(":", 1)[1])
            return subprocess.CompletedProcess(command, 0 if raw is not None else 1, raw or b"", b"MANIFEST_UNKNOWN" if raw is None else b"")
        if command[1] == "copy":
            self.present = True
            self.fallback[self.fallback_tag] = json.dumps({"schemaVersion": 2, "mediaType": OCI + "image.index.v1+json", "manifests": [self.attestation_descriptor]}).encode()
        if command[1] == "validate" and self.semantic_failure:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    def popen(self, command, **kwargs):
        self.calls.append(command)
        raw = self.blobs.get(command[2].rsplit("@", 1)[1])

        class Process:
            stdout = io.BytesIO(raw or b"")
            def __enter__(self): return self
            def __exit__(self, *args): self.stdout.close()
            def wait(self): return 0 if raw is not None else 1
            def kill(self): pass
        return Process()

    def audit(self):
        with patch.object(M.subprocess, "run", side_effect=self.run), patch.object(M.subprocess, "Popen", side_effect=self.popen):
            return M.ensure_image(M.Crane(), SOURCE + "@" + self.root, TARGET, self.root)


class GraphTest(unittest.TestCase):
    def test_multiarch_and_attestations_all_bytes_existing_root_no_copy(self):
        f = Fixture()
        result = f.audit()
        self.assertTrue(result["fullBytesVerified"])
        self.assertEqual(len(result["runnable"]), 2)
        self.assertEqual(len(result["attestations"]), 1)
        self.assertFalse(result["copyPerformed"])
        self.assertIn(f.attestation_blob, {b["digest"] for b in result["blobs"]})
        validated = [c[3] for c in f.calls if c[1] == "validate"]
        self.assertEqual(set(validated), {TARGET.rsplit(":", 1)[0] + "@" + sha for sha in result["runnable"]})
        self.assertFalse(any(c[1] == "copy" for c in f.calls))

    def test_corrupt_or_missing_attestation_blob_rejected_even_matching_root(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                f = Fixture()
                if missing:
                    del f.blobs[f.attestation_blob]
                else:
                    raw = f.blobs[f.attestation_blob]
                    f.blobs[f.attestation_blob] = b"x" + raw[1:]
                with self.assertRaisesRegex(ValueError, "blob size/digest mismatch"):
                    f.audit()
                self.assertFalse(any(c[1] == "copy" for c in f.calls))

    def test_wrong_subject_size_annotation_and_unknown_shape_fail_closed(self):
        for key in ("wrong_subject", "bad_subject_size", "wrong_annotation", "unknown"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                Fixture(**{key: True}).audit()

    def test_malformed_runnable_image_and_semantic_failure_rejected(self):
        with self.assertRaisesRegex(ValueError, "runnable config"):
            Fixture(bad_config=True).audit()
        f = Fixture()
        f.semantic_failure = True
        with self.assertRaises(subprocess.CalledProcessError):
            f.audit()

    def test_fresh_copy_checks_and_bounds_referrer_fallback_tags(self):
        f = Fixture()
        f.present = False
        result = f.audit()
        self.assertTrue(result["copyPerformed"])
        self.assertEqual(result["permittedNewFallbackTags"], [f.fallback_tag])
        self.assertEqual(len([c for c in f.calls if c[1] == "copy"]), 1)

    def test_preexisting_fallback_tag_preserved_before_copy(self):
        f = Fixture()
        f.present = False
        f.fallback[f.fallback_tag] = b"unknown existing content"
        before = copy.deepcopy(f.fallback)
        with self.assertRaisesRegex(ValueError, "preexisting referrer fallback"):
            f.audit()
        self.assertEqual(f.fallback, before)
        self.assertFalse(any(c[1] == "copy" for c in f.calls))

    def test_preexisting_conflicting_root_is_not_overwritten(self):
        f = Fixture()
        f.manifests[f.root] += b" "
        with self.assertRaisesRegex(ValueError, "existing destination tag conflicts"):
            f.audit()
        self.assertFalse(any(c[1] == "copy" for c in f.calls))

    def test_tls_and_auth_errors_never_treated_as_absence(self):
        for error in (b"x509: certificate rejected", b"DENIED: access forbidden"):
            with self.subTest(error=error), patch.object(M.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", error)):
                with self.assertRaisesRegex(RuntimeError, "manifest read failed"):
                    M.Crane().manifest(TARGET, missing_ok=True)

    def test_bad_child_manifest_bytes_and_blob_size_are_rejected(self):
        f = Fixture()
        child = json.loads(f.manifests[f.root])["manifests"][0]["digest"]
        f.manifests[child] += b" "
        with self.assertRaisesRegex(ValueError, "manifest size/digest mismatch"):
            f.audit()
        f = Fixture()
        f.blobs[f.attestation_blob] += b" "
        with self.assertRaisesRegex(ValueError, "blob size/digest mismatch"):
            f.audit()

    def test_unknown_index_subject_rejected_before_copy(self):
        f = Fixture()
        root = json.loads(f.manifests[f.root])
        root["subject"] = root["manifests"][0]
        f.root = f.manifest(root)["digest"]
        f.present = False
        with self.assertRaisesRegex(ValueError, "unknown index artifact"):
            f.audit()
        self.assertFalse(any(c[1] == "copy" for c in f.calls))

    def test_existing_matching_root_leaves_unknown_fallback_untouched(self):
        f = Fixture()
        f.fallback[f.fallback_tag] = b"unknown retained tag"
        before = copy.deepcopy(f.fallback)
        result = f.audit()
        self.assertFalse(result["copyPerformed"])
        self.assertEqual(f.fallback, before)


if __name__ == "__main__":
    unittest.main()
