# Copyright (c) 2026 Cisco and/or its affiliates.
# Licensed under the Cisco Sample Code License, Version 1.1. See LICENSE.
"""Offline selected-copy binding and delegated authenticated-client contracts."""
from pathlib import Path
import base64, hashlib, importlib.util, json, subprocess, tempfile, unittest
from unittest import mock
import yaml
ROOT = Path(__file__).resolve().parents[1]

def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m
source = script('inspect-isovalent-release')
mirror = script('verify-isovalent-mirror')

class SelectedSync(unittest.TestCase):

    def selected(self):
        return source.load_selection(Path('mirror/isovalent-selection.yaml'), ROOT)

    def test_fresh_copy_inventory_contains_every_destination_verification_pin(self):
        selected = self.selected()
        recipe = yaml.safe_load((ROOT / selected['imageSet']).read_text())
        copied = {(source.split_image(i['name'])[1], source.split_image(i['name'])[2]) for i in recipe['mirror']['additionalImages']}
        seen = []

        def fresh(registry, repo, ref, *args):
            if (repo, ref) not in copied:
                raise ValueError('manifest absent from fresh destination')
            seen.append((repo, ref))
            return ref
        with mock.patch.object(mirror, 'manifest_digest', side_effect=fresh):
            for item in selected['contracts']:
                mirror.verify_mirror(item['contract'], 'https://registry.example.test', Path('private-auth'))
        self.assertGreater(len(seen), 1)

    def test_mismatched_contract_is_rejected_before_source_or_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'mirror/releases').mkdir(parents=True)
            for file in ['isovalent-images.yaml', 'isovalent-selection.yaml', 'releases/iep-26.05.yaml']:
                target = root / 'mirror' / file
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / 'mirror' / file).read_bytes())
            path = root / 'mirror/isovalent-selection.yaml'
            value = yaml.safe_load(path.read_text())
            value['contracts'][0] = {'path': 'mirror/releases/iep-26.05.yaml', 'mode': 'full'}
            recipe = root / 'mirror/isovalent-images.yaml'
            recipe.write_text('mirror: {additionalImages: []}\n')
            path.write_text(yaml.safe_dump(value))
            with self.assertRaisesRegex(ValueError, 'absent from the selected copy recipe'):
                source.load_selection(Path('mirror/isovalent-selection.yaml'), root)

    def test_receipt_identity_changes_with_selected_source_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'mirror/releases').mkdir(parents=True)
            for file in ['isovalent-images.yaml', 'isovalent-selection.yaml', 'releases/clife-1.19.7.yaml', 'releases/iep-26.05.yaml']:
                target = root / 'mirror' / file
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / 'mirror' / file).read_bytes())
            before = source.load_selection(Path('mirror/isovalent-selection.yaml'), root)['sha256']
            p = root / 'mirror/releases/clife-1.19.7.yaml'
            p.write_text(p.read_text() + '\n# refreshed provenance\n')
            self.assertNotEqual(before, source.load_selection(Path('mirror/isovalent-selection.yaml'), root)['sha256'])

    def test_ca_bytes_and_runtime_selection_change_receipt_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in self.selected()['inputs']:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
            selection_path = root / 'mirror/isovalent-selection.yaml'
            declared = yaml.safe_load(selection_path.read_text())
            declared['destination']['caBundle'] = 'mirror/fixture-ca.pem'
            selection_path.write_text(yaml.safe_dump(declared))
            ca = root / 'mirror/fixture-ca.pem'
            ca.write_bytes(b'synthetic trust input, no TLS use')
            before = source.load_selection(Path('mirror/isovalent-selection.yaml'), root)
            ca.write_bytes(ca.read_bytes() + b'\n')
            after = source.load_selection(Path('mirror/isovalent-selection.yaml'), root)
            self.assertNotEqual(before['sha256'], after['sha256'])
            value = yaml.safe_load((root / 'mirror/isovalent-selection.yaml').read_text())
            value['runtimeImages'] = value['runtimeImages'][:-1]
            (root / 'mirror/isovalent-selection.yaml').write_text(yaml.safe_dump(value))
            self.assertNotEqual(after['sha256'], source.load_selection(Path('mirror/isovalent-selection.yaml'), root)['sha256'])

    def test_cli_rejects_wrong_destination_or_ca_before_client_and_removes_stale_receipt(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auth = root / 'auth'
            auth.write_text('{}')
            auth.chmod(384)
            certs = root / 'certs'
            certs.mkdir()
            (certs / 'ca.crt').write_text('wrong CA')
            receipt = root / 'receipt.json'
            for registry, message in [('https://wrong.example.test', 'destination'), ('https://' + self.selected()['destination']['registry'], 'CA')]:
                receipt.write_text('stale prior success')
                with mock.patch.object(sys, 'argv', ['verify', '--selection', 'mirror/isovalent-selection.yaml', '--registry', registry, '--auth-file', str(auth), '--cert-dir', str(certs), '--output', str(receipt)]), mock.patch.object(mirror.subprocess, 'run') as client:
                    with self.assertRaisesRegex(ValueError, message):
                        mirror.main()
                    client.assert_not_called()
                self.assertFalse(receipt.exists())

    def test_existing_workflow_uses_selection_for_source_copy_verify_and_policy(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/sync-images.yaml').read_text())
        job = workflow['jobs']['sync-isovalent']
        steps = job['steps']
        text = '\n'.join((s.get('run', '') for s in steps))
        self.assertNotIn('--contract mirror/releases/iep-26.05.yaml', text)
        self.assertIn('--expected-registry "$LOCAL_REGISTRY"', text)
        self.assertIn('read-registry-contract.py', text)
        self.assertIn('oc-mirror --v2 --config "$IMAGE_SET"', text)
        self.assertIn('--config "$IMAGE_SET"', text)
        self.assertIn('--auth-file "$REGISTRY_AUTH_FILE"', text)
        self.assertIn('isovalent-mirror-verified.json', text)

class AuthenticatedMirror(unittest.TestCase):

    def setup_auth(self, root, value='valid'):
        p = root / 'auth.json'
        p.write_text(json.dumps({'auths': {'registry.example.test:5000': {'auth': base64.b64encode(('fixture-user:' + value).encode()).decode()}}}))
        p.chmod(384)
        return p

    def protected(self, argv, **kwargs):
        self.assertIn('--tls-verify=true', argv)
        self.assertIn('--raw', argv)
        auth = Path(argv[argv.index('--authfile') + 1])
        valid = json.loads(auth.read_text())['auths']['registry.example.test:5000']['auth'] == base64.b64encode(b'fixture-user:valid').decode()
        return subprocess.CompletedProcess(argv, 0 if valid else 1, stdout=b'{"schemaVersion":2}' if valid else b'', stderr=b'private synthetic credential' if not valid else b'')

    def test_protected_success_delegates_auth_file_and_ca_to_existing_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auth = self.setup_auth(root)
            ca = root / 'certs'
            ca.mkdir()
            with mock.patch.object(mirror.subprocess, 'run', side_effect=self.protected) as client:
                actual = mirror.manifest_digest('https://registry.example.test:5000', 'component', 'sha256:expected', auth, ca)
            self.assertEqual(actual, 'sha256:' + hashlib.sha256(b'{"schemaVersion":2}').hexdigest())
            self.assertIn('--cert-dir', client.call_args.args[0])
            self.assertEqual(client.call_args.kwargs['timeout'], 120)

    def test_missing_auth_fails_without_client_or_anonymous_retry(self):
        with mock.patch.object(mirror.subprocess, 'run') as client:
            with self.assertRaisesRegex(ValueError, 'auth file'):
                mirror.manifest_digest('https://registry.example.test:5000', 'component', 'sha256:expected')
            client.assert_not_called()

    def test_wrong_auth_fails_once_without_exposing_client_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = self.setup_auth(Path(tmp), 'wrong')
            with mock.patch.object(mirror.subprocess, 'run', side_effect=self.protected) as client:
                with self.assertRaisesRegex(ValueError, 'authenticated registry') as caught:
                    mirror.manifest_digest('https://registry.example.test:5000', 'component', 'sha256:expected', auth)
            self.assertEqual(client.call_count, 1)
            self.assertNotIn('credential', str(caught.exception))

    def test_tls_bypass_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'cannot be disabled'):
            mirror.manifest_digest('https://registry.example.test', 'component', 'sha256:expected', insecure=True)
if __name__ == '__main__':
    unittest.main()
