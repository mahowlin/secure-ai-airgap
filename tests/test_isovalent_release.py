from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import stat
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


auth = load_script("configure_isovalent_auth", "scripts/configure-isovalent-auth.py")
release = load_script("inspect_isovalent_release", "scripts/inspect-isovalent-release.py")
mirror = load_script("verify_isovalent_mirror", "scripts/verify-isovalent-mirror.py")
policy = load_script("generate_mirror_policy", "scripts/generate-mirror-policy.py")


def synthetic_bundle(image: str) -> bytes:
    manifest = yaml.safe_dump(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "clife-controller-manager"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "manager", "image": image}],
                    }
                }
            },
        }
    ).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo("manifests/controller.yaml")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
    return output.getvalue()


class RegistryAuthTests(unittest.TestCase):
    def test_auth_is_merged_for_all_isovalent_hosts(self):
        config = auth.build_config('{"auths":{"registry.redhat.io":{"auth":"redhat"}}}', "user", "token")
        self.assertEqual(config["auths"]["registry.redhat.io"]["auth"], "redhat")
        for host in auth.ISOVALENT_HOSTS:
            self.assertIn(host, config["auths"])
            self.assertNotIn("token", json.dumps(config["auths"][host]))

    def test_auth_file_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            auth.write_private_json(path, {"auths": {}})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_provisioning_dry_run_never_prints_token(self):
        token = "synthetic-reference-token"
        result = subprocess.run(
            [
                str(ROOT / "tools/provision-isovalent-registry.sh"),
                "--username",
                "user@example.com",
                "--github-only",
                "--dry-run",
            ],
            input=f"{token}\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(token, result.stdout)
        self.assertNotIn(token, result.stderr)
        self.assertIn("ISOVALENT_REGISTRY_TOKEN", result.stdout)

    def test_vault_dry_run_never_prints_either_token(self):
        registry_token = "synthetic-reference-token"
        vault_token = "synthetic-vault-token"
        environment = {
            **os.environ,
            "VAULT_URL": "https://vault.example.com",
            "VAULT_NAMESPACE": "example",
            "VAULT_TOKEN": vault_token,
            "VAULT_ARTIFACTORY_SECRET_PATH": "platform/artifactory",
            "VAULT_ARTIFACTORY_SECRET_KEY": "reference_token",
        }
        result = subprocess.run(
            [
                str(ROOT / "tools/provision-isovalent-registry.sh"),
                "--username",
                "user@example.com",
                "--dry-run",
            ],
            input=f"{registry_token}\n",
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        combined_output = result.stdout + result.stderr
        self.assertNotIn(registry_token, combined_output)
        self.assertNotIn(vault_token, combined_output)

    def test_auth_mode_is_non_mutating_and_never_prints_token(self):
        token = "synthetic-reference-token"
        with tempfile.TemporaryDirectory() as directory:
            fake_python = Path(directory) / "python3"
            evidence = Path(directory) / "evidence.json"
            fake_python.write_text(
                "#!/bin/sh\n"
                "test \"$ISOVALENT_REGISTRY_USERNAME\" = user@example.com || exit 21\n"
                f"test \"$ISOVALENT_REGISTRY_TOKEN\" = {token} || exit 22\n"
                "test \"$2\" = --contract || exit 23\n"
                "test \"$4\" = --output || exit 24\n"
                "test \"$6\" = --discovery-only || exit 25\n"
                "printf '{}\\n' >\"$5\"\n"
            )
            fake_python.chmod(0o700)
            environment = {
                **os.environ,
                "PATH": f"{directory}:{os.environ['PATH']}",
            }
            result = subprocess.run(
                [
                    str(ROOT / "tools/provision-isovalent-registry.sh"),
                    "--username",
                    "user@example.com",
                    "--test-auth",
                    "--output",
                    str(evidence),
                ],
                input=f"{token}\n",
                capture_output=True,
                text=True,
                env=environment,
                check=False,
            )
            evidence_contents = evidence.read_text()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(evidence_contents, "{}\n")
        combined_output = result.stdout + result.stderr
        self.assertNotIn(token, combined_output)
        self.assertIn("Authenticated access verified", result.stdout)


class ReleaseInspectionTests(unittest.TestCase):
    def test_package_only_verifies_archive_controller_and_runtime_without_standalone_chart(self):
        image = "containers.isovalent.com/clife:v1.19.7-cee.3"
        bundle = synthetic_bundle(image)
        digest = "sha256:" + "a" * 64
        import hashlib
        contract = {
            "release": "26.05", "version": "1.19.7",
            "clifeBundle": {
                "url": "https://artifactory.isovalent.com/artifactory/archive/clife-manifests/clife-v1.19.7.tar.gz",
                "sha256": hashlib.sha256(bundle).hexdigest(),
                "controllerImage": image, "controllerDigest": digest,
            },
            "runtimeImages": [{"name": "cilium", "image": "containers.isovalent.com/cilium-ubi:v1.19.7-cee.3", "digest": digest, "mirrorRepository": "cilium-ubi"}],
        }
        response = io.BytesIO(bundle)
        with mock.patch.object(release, "authenticated_get", return_value=response), mock.patch.object(release, "registry_manifest_digest", return_value=digest):
            evidence, actual = release.discover(contract, "user", "token", package_only=True)
        self.assertEqual(actual, bundle)
        release.verify(contract, evidence)
        evidence["runtimeImages"][0]["digest"] = "sha256:" + "b" * 64
        with self.assertRaises(SystemExit):
            release.verify(contract, evidence)

    def test_curl_download_keeps_token_out_of_argv_and_removes_netrc(self):
        token = "synthetic-reference-token"
        captured = {}

        def fake_run(arguments, **kwargs):
            netrc_path = Path(arguments[arguments.index("--netrc-file") + 1])
            captured["arguments"] = arguments
            captured["netrc_path"] = netrc_path
            captured["netrc"] = netrc_path.read_text()
            captured["mode"] = stat.S_IMODE(netrc_path.stat().st_mode)
            return subprocess.CompletedProcess(arguments, 0, stdout=b"entries: {}\n", stderr=b"")

        with mock.patch.object(release.subprocess, "run", side_effect=fake_run):
            output = release.curl_authenticated_download(
                "https://charts.isovalent.com/index.yaml",
                "user@example.com",
                token,
            )

        self.assertEqual(output, b"entries: {}\n")
        self.assertNotIn(token, " ".join(captured["arguments"]))
        self.assertIn(token, captured["netrc"])
        self.assertEqual(captured["mode"], 0o600)
        self.assertFalse(captured["netrc_path"].exists())

    def test_cross_host_https_redirect_strips_authorization(self):
        request = urllib.request.Request(
            "https://charts.isovalent.com/index.yaml",
            headers={"Authorization": "Basic synthetic-secret", "Accept": "application/yaml"},
        )
        redirected = release.CredentialSafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://example-bucket.s3.amazonaws.com/signed-index.yaml?signature=synthetic",
        )
        self.assertEqual(redirected.full_url, "https://example-bucket.s3.amazonaws.com/signed-index.yaml?signature=synthetic")
        self.assertNotIn("Authorization", dict(redirected.header_items()))
        self.assertEqual(dict(redirected.header_items())["Accept"], "application/yaml")

    def test_same_origin_https_redirect_retains_authorization(self):
        request = urllib.request.Request(
            "https://charts.isovalent.com/index.yaml",
            headers={"Authorization": "Basic synthetic-secret", "Accept": "application/yaml"},
        )
        redirected = release.CredentialSafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://charts.isovalent.com/releases/index.yaml",
        )
        self.assertEqual(
            dict(redirected.header_items())["Authorization"],
            "Basic synthetic-secret",
        )

    def test_same_origin_normalizes_hostname_and_default_https_port(self):
        request = urllib.request.Request(
            "https://CHARTS.ISOVALENT.COM:443/index.yaml",
            headers={"Authorization": "Basic synthetic-secret"},
        )
        redirected = release.CredentialSafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://charts.isovalent.com/releases/index.yaml",
        )
        self.assertIn("Authorization", dict(redirected.header_items()))

    def test_same_host_port_change_strips_authorization(self):
        for port in (8443, 0):
            request = urllib.request.Request(
                "https://charts.isovalent.com/index.yaml",
                headers={"Authorization": "Basic synthetic-secret", "Accept": "application/yaml"},
            )
            with self.subTest(port=port):
                redirected = release.CredentialSafeRedirectHandler().redirect_request(
                    request,
                    None,
                    302,
                    "Found",
                    {},
                    f"https://charts.isovalent.com:{port}/index.yaml",
                )
                self.assertNotIn("Authorization", dict(redirected.header_items()))
                self.assertEqual(dict(redirected.header_items())["Accept"], "application/yaml")

    def test_relative_same_origin_redirect_is_safe(self):
        request = urllib.request.Request(
            "https://charts.isovalent.com/releases/index.yaml",
            headers={"Authorization": "Basic synthetic-secret"},
        )
        redirected = release.CredentialSafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "../index.yaml",
        )
        self.assertEqual(redirected.full_url, "https://charts.isovalent.com/index.yaml")
        self.assertIn("Authorization", dict(redirected.header_items()))

    def test_unsafe_redirect_targets_fail_without_echoing_credentials(self):
        unsafe_targets = (
            "http://charts.isovalent.com/index.yaml",
            "ftp://charts.isovalent.com/index.yaml",
            "https://attacker@charts.isovalent.com/index.yaml",
            "https://attacker:password@charts.isovalent.com/index.yaml",
            "https://charts.isovalent.com:invalid/index.yaml",
        )
        for target in unsafe_targets:
            request = urllib.request.Request(
                "https://charts.isovalent.com/index.yaml",
                headers={"Authorization": "Basic synthetic-secret"},
            )
            with self.subTest(target=target):
                with self.assertRaises(SystemExit) as caught:
                    release.CredentialSafeRedirectHandler().redirect_request(
                        request,
                        None,
                        302,
                        "Found",
                        {},
                        target,
                    )
                message = str(caught.exception)
                self.assertNotIn("synthetic-secret", message)
                self.assertNotIn(target, message)

    def test_redirect_repeat_limit_remains_bounded(self):
        request = urllib.request.Request(
            "https://charts.isovalent.com/index.yaml",
            headers={"Authorization": "Basic synthetic-secret"},
        )
        loop_url = "https://charts.isovalent.com/loop"
        handler = release.CredentialSafeRedirectHandler()
        handler.parent = mock.Mock()
        request.redirect_dict = {loop_url: handler.max_repeats}
        response = mock.Mock()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            handler.http_error_302(
                request,
                response,
                302,
                "Found",
                {"location": "/loop"},
            )
        handler.parent.open.assert_not_called()
        self.assertNotIn("synthetic-secret", str(caught.exception))

    def test_tag_plus_digest_image_uses_repository_without_tag(self):
        image = "containers.isovalent.com/clife:v1.19.5-cee.1@sha256:f389"
        self.assertEqual(
            release.split_image(image),
            ("containers.isovalent.com", "clife", "sha256:f389"),
        )

    def test_controller_image_is_read_from_bundle_without_extracting(self):
        expected = "containers.isovalent.com/clife:v1.19.5"
        self.assertEqual(release.find_clife_controller_image(synthetic_bundle(expected)), expected)

    def test_credentials_are_not_sent_to_unapproved_hosts(self):
        with self.assertRaises(SystemExit):
            release.authenticated_get("https://example.com/artifact", "user", "token")

    def test_strict_verification_rejects_unpinned_bundle(self):
        contract = {
            "release": "26.05",
            "version": "1.19.5",
            "ociChart": {"digest": "sha256:chart"},
            "clifeBundle": {"sha256": "", "controllerImage": "image", "controllerDigest": "sha256:controller"},
            "operands": [],
        }
        evidence = {
            "release": "26.05",
            "version": "1.19.5",
            "ociChart": {"digest": "sha256:chart"},
            "clifeBundle": {"sha256": "abc", "controllerImage": "image", "controllerDigest": "sha256:controller"},
            "operands": [],
        }
        with self.assertRaises(SystemExit):
            release.verify(contract, evidence)

    def test_mirror_verification_rejects_missing_controller_pin(self):
        with self.assertRaises(SystemExit):
            mirror.verify_mirror({"operands": [], "clifeBundle": {}}, "http://registry.invalid")

    def test_mirror_verification_rejects_tls_bypass(self):
        with self.assertRaisesRegex(ValueError, "cannot be disabled"):
            mirror.manifest_digest("https://registry.example.com:5000", "cilium", "sha256:expected", insecure=True)

    def test_ephemeral_bundle_must_stay_below_runner_temp_and_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            runner_temp = Path(directory) / "runner-temp"
            runner_temp.mkdir()
            target = runner_temp / "clife.tar.gz"
            with mock.patch.dict(os.environ, {"RUNNER_TEMP": str(runner_temp)}):
                release.write_ephemeral_bundle(target, b"verified")
                self.assertEqual(target.read_bytes(), b"verified")
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
                with self.assertRaises(SystemExit):
                    release.write_ephemeral_bundle(target, b"overwrite")
                with self.assertRaises(SystemExit):
                    release.write_ephemeral_bundle(Path(directory) / "outside.tar.gz", b"escape")


class CanonicalContractTests(unittest.TestCase):
    def test_selected_release_is_consistent_with_copy_inventory(self):
        selected = release.load_selection(Path("mirror/isovalent-selection.yaml"), ROOT)
        self.assertTrue(selected["contracts"])
        self.assertIn("mirror/isovalent-selection.yaml", selected["inputs"])

    def test_generated_policy_covers_selected_registry_repositories(self):
        selected = release.load_selection(Path("mirror/isovalent-selection.yaml"), ROOT)
        config = yaml.safe_load((ROOT / selected["imageSet"]).read_text())
        sources = policy.collect_sources(config, [])
        for image in config["mirror"]["additionalImages"]:
            registry, repository, _ = release.split_image(image["name"])
            source = registry + "/" + repository
            self.assertTrue(any(source == entry or source.startswith(entry + "/") for entry in sources))
            prefix = max((entry for entry in sources if source == entry or source.startswith(entry + "/")), key=len)
            self.assertEqual(policy.mirror_for_source(prefix, "registry.example.com:5000", {}) + source[len(prefix):], "registry.example.com:5000/" + repository)


if __name__ == "__main__":
    unittest.main()
