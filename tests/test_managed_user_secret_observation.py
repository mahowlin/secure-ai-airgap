# Copyright (c) 2026 Cisco and/or its affiliates.
# Licensed under the Cisco Sample Code License, Version 1.1. See LICENSE.
"""Run the actual deletion shell against synthetic oc responses; no real API/tools."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
FAKE_OC = "\nimport base64,json,os,sys\nfrom pathlib import Path\nargs=sys.argv[1:];case=os.environ['OBSERVATION_CASE'];log=Path(os.environ['OBSERVATION_LOG'])\ndef record(value):\n with log.open('a') as stream:stream.write(json.dumps(value)+'\\n')\nrecord({'args':args})\nif args[:2]==['get','secret']:\n if '--ignore-not-found' not in args or args[-2:]!=['-o','json']:raise SystemExit(93)\n if case in ('forbidden','transport'):\n  print('Forbidden' if case=='forbidden' else 'connection timed out',file=sys.stderr);raise SystemExit(1)\n if case=='absent':raise SystemExit(0)\n value={'apiVersion':'v1','kind':'Secret','metadata':{'name':'htpasswd-secret','namespace':'openshift-config','resourceVersion':'77','uid':'fixture-only-uid'},'data':{'htpasswd':base64.b64encode(b'alice:synthetic-only\\nbob:synthetic-only\\n').decode()}}\n if case=='wrong-identity':value['metadata']['name']='other-secret'\n if case=='bad-data':value['data']['htpasswd']='BODY_MUST_NOT_BE_PRINTED'\n if case=='malformed':print('BODY_MUST_NOT_BE_PRINTED');raise SystemExit(0)\n print(json.dumps(value));raise SystemExit(0)\nif args[:2]==['get','clusterrolebindings']:\n user={'kind':'User','name':'alice'}\n binding={'metadata':{'name':'owned-shared','resourceVersion':'12','labels':{'managed-by':'manage-cluster-users-workflow'}},'subjects':[user,{'kind':'Group','name':'alice'},{'kind':'User','name':'bob'}]}\n other={'metadata':{'name':'unowned','resourceVersion':'13','labels':{}},'subjects':[user]}\n print(json.dumps({'items':[binding,other]}));raise SystemExit(0)\nif args[:2]==['patch','clusterrolebinding']:\n if args[2]!='owned-shared':raise SystemExit(94)\n record({'patched':json.loads(args[args.index('-p')+1])});raise SystemExit(0)\nif args[:3]==['create','secret','generic']:\n path=next(arg.split('=',2)[2] for arg in args if arg.startswith('--from-file=htpasswd='))\n record({'updatedHtpasswd':Path(path).read_text()})\n print('{}');raise SystemExit(0)\nif args[:1]==['apply']:\n sys.stdin.read()\n raise SystemExit(1 if case=='update-failed' else 0)\nraise SystemExit(95)\n"

class SecretObservationWorkflowTests(unittest.TestCase):

    def run_step(self, case):
        workflow = yaml.safe_load((ROOT / '.github/workflows/manage-cluster-users.yaml').read_text())
        step = next((step for step in workflow['jobs']['manage-users']['steps'] if step['name'] == 'Delete Users'))
        shell = step['run'].replace('${{ inputs.users }}', '${REQUESTED_USERS}')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'oc'
            binary.write_text('#!' + sys.executable + '\n' + FAKE_OC)
            binary.chmod(448)
            python = root / 'python3'
            python.symlink_to(sys.executable)
            log = root / 'calls.jsonl'
            environ = dict(os.environ, PATH=str(root) + ':/usr/bin:/bin', OBSERVATION_CASE=case, OBSERVATION_LOG=str(log), REQUESTED_USERS='alice', HTPASSWD_SECRET='htpasswd-secret', CLUSTER_NAME='fixture-cluster', RUNNER_TEMP=str(root), GITHUB_STEP_SUMMARY=str(root / 'summary'))
            result = subprocess.run(['bash', '-c', shell], cwd=ROOT, env=environ, capture_output=True, text=True)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(list(root.glob('managed-*')), [])
            self.assertNotIn('BODY_MUST_NOT_BE_PRINTED', result.stdout + result.stderr)
            self.assertNotIn('synthetic-only', result.stdout + result.stderr)
            reads = [call for call in calls if call.get('args', [])[:2] == ['get', 'secret']]
            self.assertEqual(len(reads), 1)
            summary = (root / 'summary').read_text()
            self.assertNotIn('BODY_MUST_NOT_BE_PRINTED', summary)
            self.assertNotIn('synthetic-only', summary)
            return (result, calls, summary)

    def test_forbidden_or_transport_error_stops_before_any_mutation_or_success(self):
        for case in ('forbidden', 'transport'):
            with self.subTest(case=case):
                result, calls, summary = self.run_step(case)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls), 1)
                self.assertNotIn('Users removed successfully', result.stdout + summary)
                self.assertNotIn('Managed access revoked', result.stdout + summary)

    def test_successful_not_found_revokes_only_owned_user_subject_and_does_not_create_secret(self):
        result, calls, summary = self.run_step('absent')
        self.assertEqual(result.returncode, 0, result.stderr)
        patches = [call['patched'] for call in calls if 'patched' in call]
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0][-1]['value'], [{'kind': 'Group', 'name': 'alice'}, {'kind': 'User', 'name': 'bob'}])
        self.assertFalse(any((call.get('args', [None])[0] in ('create', 'apply', 'delete') for call in calls)))
        self.assertIn('Users removed successfully', summary)

    def test_successful_secret_is_decoded_from_single_observation_and_updated(self):
        result, calls, summary = self.run_step('present')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call['updatedHtpasswd'] for call in calls if 'updatedHtpasswd' in call], ['bob:synthetic-only\n'])
        self.assertTrue(any((call.get('args', [])[:1] == ['apply'] for call in calls)))
        self.assertIn('Users removed successfully', summary)

    def test_wrong_identity_or_invalid_payload_stops_before_binding_observation(self):
        for case in ('wrong-identity', 'bad-data', 'malformed'):
            with self.subTest(case=case):
                result, calls, summary = self.run_step(case)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(calls), 1)
                self.assertNotIn('Users removed successfully', result.stdout + summary)

    def test_secret_update_failure_is_not_reported_as_overall_success(self):
        result, calls, summary = self.run_step('update-failed')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(('patched' in call for call in calls)))
        self.assertNotIn('Users removed successfully', result.stdout + summary)
if __name__ == '__main__':
    unittest.main()
