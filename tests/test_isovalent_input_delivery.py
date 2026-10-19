"""Offline regressions for declared trust/auth delivery and exact Git promotion."""
from pathlib import Path
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


auth = load('prepare-isovalent-auth')
mirror = load('verify-isovalent-mirror')
promoter = load('promote-clife-package-input')


class DeliveryTests(unittest.TestCase):
    def test_private_auth_retains_destination_credentials_and_declared_ca(self):
        selected = auth.release.load_selection(Path('mirror/isovalent-selection.yaml'))
        host = selected['destination']['registry']
        original = {'auths': {host: {'auth': 'c3ludGhldGljOm9ubHk='}}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
            'REDHAT_PULL_SECRET': json.dumps(original), 'ISOVALENT_REGISTRY_USERNAME': 'fixture',
            'ISOVALENT_REGISTRY_TOKEN': 'not-a-real-secret'}):
            root = Path(tmp)
            values = auth.prepare(Path('mirror/isovalent-selection.yaml'), root, root / 'env', host)
            private = values['ISOVALENT_AUTH_DIR']
            self.assertEqual(private.stat().st_mode & 0o777, 0o700)
            self.assertEqual(values['REGISTRY_AUTH_FILE'].stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(values['REGISTRY_AUTH_FILE'].read_text())['auths'][host], original['auths'][host])
            self.assertNotIn('not-a-real-secret', (root / 'env').read_text())
            if selected['destination']['caBundle']:
                expected = (ROOT / selected['destination']['caBundle']).read_bytes()
                self.assertEqual((values['ISOVALENT_CERT_DIR'] / 'ca.crt').read_bytes(), expected)
            else:
                context = auth.ssl.create_default_context()
                context.load_verify_locations(cafile=str(values['ISOVALENT_CERT_DIR'] / 'ca.crt'))
            self.assertIn(values['HELM_REGISTRY_CONFIG'], [values['REGISTRY_AUTH_FILE']])
            shutil.rmtree(private)
            self.assertFalse(private.exists())

    def test_missing_destination_auth_or_wrong_destination_fails_before_delivery(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
            'REDHAT_PULL_SECRET': '{"auths":{}}', 'ISOVALENT_REGISTRY_USERNAME': 'fixture',
            'ISOVALENT_REGISTRY_TOKEN': 'not-a-real-secret'}):
            root = Path(tmp)
            for destination, message in ((None, 'authentication'), ('wrong.example.test', 'destination')):
                with self.assertRaisesRegex(ValueError, message):
                    auth.prepare(Path('mirror/isovalent-selection.yaml'), root, root / 'env', destination)
            self.assertEqual(list(root.iterdir()), [])

    def test_failure_after_private_auth_write_removes_private_files(self):
        selected = auth.release.load_selection(Path('mirror/isovalent-selection.yaml'))
        host = selected['destination']['registry']
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {
            'REDHAT_PULL_SECRET': json.dumps({'auths': {host: {'auth': 'fixture'}}}),
            'ISOVALENT_REGISTRY_USERNAME': 'fixture', 'ISOVALENT_REGISTRY_TOKEN': 'fixture'}), \
                mock.patch.object(auth.ssl, 'create_default_context', side_effect=ValueError('broken trust')):
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'broken trust'):
                auth.prepare(Path('mirror/isovalent-selection.yaml'), root, root / 'env')
            self.assertEqual(list(root.iterdir()), [])

    def test_tag_receipt_records_actual_raw_manifest_digest_and_rejects_mismatch(self):
        image = 'source.example.test/team/collector:v7'
        digest = 'sha256:' + '1' * 64
        with mock.patch.object(mirror, 'manifest_digest', return_value=digest) as client:
            records = mirror.verify_runtime_images([image], 'https://mirror.example.test:5000', Path('auth'), Path('certs'))
            self.assertEqual(records[0]['digest'], digest)
            self.assertEqual(client.call_args_list[0].args[:3], ('https://source.example.test', 'team/collector', 'v7'))
            self.assertEqual(client.call_args_list[1].args[:3], ('https://mirror.example.test:5000', 'team/collector', 'v7'))
        with mock.patch.object(mirror, 'manifest_digest', side_effect=[digest, 'sha256:' + '2' * 64]):
            with self.assertRaisesRegex(ValueError, 'source/mirror'):
                mirror.verify_runtime_images([image], 'https://mirror.example.test', Path('auth'), Path('certs'))

    def test_touched_jobs_cleanup_even_after_failure_and_never_disable_tls(self):
        workflows = ROOT / '.github/workflows'
        sync = yaml.safe_load((workflows / 'sync-images.yaml').read_text())['jobs']['sync-isovalent']
        publish = yaml.safe_load((workflows / 'publish-clife-package.yaml').read_text())['jobs']['publish']
        for job in (sync, publish):
            runs = '\n'.join(step.get('run', '') for step in job['steps'])
            self.assertNotIn('--insecure', runs)
            self.assertNotIn('--dest-tls-verify=false', runs)
            self.assertNotIn('~/.docker', runs)
            cleanup = job['steps'][-1]
            self.assertEqual(cleanup['if'], '${{ always() }}')
            self.assertIn('rm -rf -- "$ISOVALENT_AUTH_DIR"', cleanup['run'])


class PromotionTests(unittest.TestCase):
    def test_public_examples_validate_but_cannot_claim_unpromoted_provenance(self):
        for version in ('1.19.5', '1.19.7'):
            bundle = ROOT / 'mirror/package-inputs/clife-install' / version
            # No source-main publication has been released for these sanitized examples.
            with self.assertRaisesRegex(ValueError, 'missing package input provenance'):
                promoter.verifier.verify(bundle, 'mahowlin/secure-ai-datacenter', '0' * 40,
                                         'data/package-contracts/clife-install-' + version + '.yaml')
            result = subprocess.run([sys.executable, str(bundle / 'validate-contract.py'), str(bundle / 'contract.yaml'),
                                     '--schema', str(bundle / 'contract.schema.json')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_promotion_uses_committed_bytes_not_dirty_worktree_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            source.mkdir()
            def git(*args):
                return subprocess.run(['git', '-C', str(source), *args], check=True, capture_output=True).stdout.decode().strip()
            git('init', '-q')
            git('config', 'user.email', 'fixture@example.test')
            git('config', 'user.name', 'Offline fixture')
            git('remote', 'add', 'origin', 'https://github.com/fixture/platform.git')
            original = ROOT / 'mirror/package-inputs/clife-install/1.19.7'
            paths = {'contract.yaml': 'data/package-contracts/clife-install-1.19.7.yaml',
                     'contract.schema.json': 'schemas/clife-package-contract-v1.schema.json',
                     'validate-contract.py': 'scripts/validate-clife-package-contract.py'}
            for local, relative in paths.items():
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((original / local).read_bytes())
            git('add', '.')
            git('commit', '-qm', 'Synthetic local source fixture')
            revision = git('rev-parse', 'HEAD')
            git('update-ref', 'refs/remotes/origin/main', revision)
            (source / paths['contract.yaml']).write_text('deliberately dirty invalid bytes')
            out = root / 'promoted'
            promoter.promote(source, 'fixture/platform', revision, paths['contract.yaml'], out)
            self.assertEqual((out / 'contract.yaml').read_bytes(), (original / 'contract.yaml').read_bytes())
            promoter.verifier.verify(out, 'fixture/platform', revision, paths['contract.yaml'])
            with self.assertRaisesRegex(ValueError, 'existing'):
                promoter.promote(source, 'fixture/platform', revision, paths['contract.yaml'], out)
            with self.assertRaisesRegex(ValueError, 'origin'):
                promoter.promote(source, 'wrong/platform', revision, paths['contract.yaml'], root / 'wrong')
            self.assertFalse((root / 'wrong').exists())


if __name__ == '__main__':
    unittest.main()
