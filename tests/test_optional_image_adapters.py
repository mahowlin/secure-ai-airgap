# Copyright (c) 2026 Cisco and/or its affiliates.
# Licensed under the Cisco Sample Code License, Version 1.1. See LICENSE.
"""Execute optional workflow steps with command stubs; no Docker/registry effects."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def steps(file, job):
    return {step['name']: step for step in yaml.safe_load((ROOT / '.github/workflows' / file).read_text())['jobs'][job]['steps'] if 'name' in step}


IMAGE = steps('sync-nvidia-llm.yaml', 'sync-nvidia-llm')
MNIST = steps('build-mnist-images.yaml', 'build')


class OptionalImageAdapters(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        RUNNER_TEMP=str(self.root), GITHUB_ENV=str(self.root / 'env'),
                        GITHUB_OUTPUT=str(self.root / 'output'), GITHUB_STEP_SUMMARY=str(self.root / 'summary'),
                        CALLS=str(self.root / 'calls'))

    def command(self, name):
        path = self.bin / name
        path.write_text('#!' + sys.executable + '\nimport sys,os,json\n'
                        'with open(os.environ["CALLS"],"a") as f:f.write(json.dumps(sys.argv)+"\\n")\n')
        path.chmod(0o755)

    def run_source(self, source, **env):
        return subprocess.run(['bash', '-eo', 'pipefail', '-c', source],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=10)

    def test_digest_destination_is_valid_tag_and_copy_preserves_graph(self):
        digest = 'sha256:' + 'a' * 64
        result = self.run_source(IMAGE['Parse Image Reference']['run'],
                                 SOURCE_IMAGE_INPUT='nvcr.io:5443/team/image@' + digest,
                                 LOCAL_REGISTRY='mirror.example.test:5000')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = dict(line.split('=', 1) for line in (self.root / 'output').read_text().splitlines())
        self.assertEqual(output['local_image'], 'mirror.example.test:5000/team/image:sha256-' + 'a' * 64)
        for step in ('Pull Image', 'Tag for Local Registry'):
            self.assertIn("is_digest != 'true'", IMAGE[step]['if'])
        source = IMAGE['Push to Local Registry']['run']
        source = source.replace('${{ steps.parse.outputs.is_digest }}', 'true')
        source = source.replace('${{ steps.parse.outputs.local_image }}', output['local_image'])
        source = source.replace('${{ steps.parse.outputs.source_image }}', output['source_image'])
        self.command('skopeo')
        self.command('docker')
        result = self.run_source(source, REGISTRY_AUTH_FILE=str(self.root / 'private-auth'))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][0].endswith('/skopeo'))
        self.assertIn('--all', calls[0])
        self.assertIn('--preserve-digests', calls[0])
        self.assertIn('--dest-tls-verify=true', calls[0])

    def test_registry_port_and_tag_parse_and_missing_pin_rejected(self):
        for image, success in (('nvcr.io:5443/team/image:v9', True),
                               ('nvcr.io:5443/team/image', False),
                               ('nvcr.io/team/image@sha256:bad', False)):
            with self.subTest(image=image):
                result = self.run_source(IMAGE['Parse Image Reference']['run'], SOURCE_IMAGE_INPUT=image,
                                         LOCAL_REGISTRY='mirror.example.test:5000')
                self.assertEqual(result.returncode == 0, success)

    def test_private_auth_and_cleanup_preserve_home_docker_config(self):
        home = self.root / 'home'
        (home / '.docker').mkdir(parents=True)
        legacy = home / '.docker/config.json'
        legacy.write_text('caller-owned configuration')
        result = self.run_source(IMAGE['Configure Registry Auth']['run'], HOME=str(home),
                                 NVIDIA_NGC_API_KEY='', REDHAT_PULL_SECRET='{"auths":{}}')
        self.assertEqual(result.returncode, 0, result.stderr)
        exported = dict(line.split('=', 1) for line in (self.root / 'env').read_text().splitlines())
        private = Path(exported['REGISTRY_AUTH_FILE'])
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        result = self.run_source(IMAGE['Cleanup']['run'], HOME=str(home), **exported)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(private.exists())
        self.assertEqual(legacy.read_text(), 'caller-owned configuration')

    def test_mnist_base_is_first_in_same_store_and_dry_run_never_pushes(self):
        self.command('docker')
        step = MNIST['Build component images using the same local Docker image store']['run']
        result = self.run_source(step, REGISTRY='mirror.example.test:5000', IMAGE_PREFIX='demo',
                                 IMAGE_TAG='v9', DRY_RUN='true')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in (self.root / 'calls').read_text().splitlines()]
        self.assertEqual(len(calls), 4)
        self.assertEqual([call[-1] for call in calls], ['base', 'training', 'inference', 'webapp'])
        self.assertTrue(all(call[1] == 'build' and '--pull=false' in call for call in calls))
        self.assertIn('mirror.example.test:5000/demo/base:v9', calls[0])


if __name__ == '__main__':
    unittest.main()
