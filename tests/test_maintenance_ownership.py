# Copyright (c) 2026 Cisco and/or its affiliates.
# Licensed under the Cisco Sample Code License, Version 1.1. See LICENSE.
"""Offline negative-path checks; never call real oc, Docker, SSH, VM or user tools."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from jinja2 import Environment, StrictUndefined
import yaml
ROOT = Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
runner = load('validate-runner-configuration')
users = load('remove-managed-user-access')
endpoint = load('prepare-endpoint-build-input')

class RunnerOwnershipTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.play = yaml.safe_load((ROOT / 'ansible/playbooks/configure-runner.yaml').read_text())[0]
        cls.tasks = {task['name']: task for task in cls.play['pre_tasks']}
        cls.jinja = Environment(undefined=StrictUndefined)

    def allowed(self, name, **values):
        return all((bool(self.jinja.compile_expression(expression)(**values)) for expression in self.tasks[name]['ansible.builtin.assert']['that']))

    def test_missing_selected_private_refs_fail_without_echoing_values(self):
        for tags in ('all', 'tagged', 'runner,webserver,gitea'):
            with self.assertRaisesRegex(ValueError, 'HTTP_SERVER_PASSWORD.*GITEA_ADMIN_PASSWORD.*RUNNER_NAME'):
                runner.validate(tags, {'JUMP_HOST_SSH_KEY': 'synthetic', 'JUMP_HOST_KNOWN_HOSTS': 'synthetic'})
        with self.assertRaisesRegex(ValueError, 'at least one'):
            runner.validate('', {})
        runner.validate('tools', {'JUMP_HOST_SSH_KEY': 'synthetic', 'JUMP_HOST_KNOWN_HOSTS': 'synthetic'})
        with self.assertRaisesRegex(ValueError, 'JUMP_HOST_KNOWN_HOSTS'):
            runner.validate('tools', {'JUMP_HOST_SSH_KEY': 'synthetic'})

    def test_assertions_reject_missing_credentials(self):
        self.assertFalse(self.allowed('Require selected webserver private runtime reference', http_auth_password=''))
        self.assertFalse(self.allowed('Require selected Gitea private runtime reference', gitea_admin_password=''))
        self.assertFalse(self.allowed('Require runner token and observed executor identity', runner_token='', runner_executor_name='independent', runner_managed_by='fixture-owner'))

    def test_missing_docker_never_implies_empty_ownership(self):
        self.assertFalse(self.allowed('Require readable Docker before runner replacement', runner_docker_socket={'stat': {'exists': False}}))
        self.assertTrue(self.allowed('Require readable Docker before runner replacement', runner_docker_socket={'stat': {'exists': True}}))

    def test_self_executor_is_rejected_from_declared_names(self):
        name = 'Refuse replacement from a selected managed runner'
        self.assertFalse(self.allowed(name, runner_executor_name='fixture-runner-3', runner_name_prefix='fixture-runner-', item=3))
        self.assertTrue(self.allowed(name, runner_executor_name='external-maintenance', runner_name_prefix='fixture-runner-', item=3))

    def test_unowned_wrong_org_or_actual_executor_container_is_rejected(self):
        name = 'Refuse unrelated or executing existing runner containers'
        values = {'runner_executor_name': 'maintenance', 'runner_name_prefix': 'fixture-', 'runner_managed_by': 'fixture-owner', 'github_org': 'fixture-org'}

        def item(labels, identity):
            return {'item': 2, 'container': {'Config': {'Labels': labels, 'Env': ['RUNNER_NAME=' + identity]}}}
        good = {'managed-by': 'fixture-owner', 'runner-org': 'fixture-org'}
        self.assertTrue(self.allowed(name, item=item(good, 'fixture-2'), **values))
        self.assertFalse(self.allowed(name, item=item({}, 'fixture-2'), **values))
        self.assertFalse(self.allowed(name, item=item(dict(good, **{'runner-org': 'unrelated-org'}), 'fixture-2'), **values))
        self.assertFalse(self.allowed(name, item=item(good, 'maintenance'), **values))
        self.assertFalse(self.allowed(name, item=item(good, 'unrelated-runner'), **values))
        self.assertTrue(all(('always' in task['tags'] for task in self.play['pre_tasks'])))
        self.assertTrue(all((task.get('no_log') for task in self.play['tasks'] if 'gitea_admin_password' in str(task))))

class ManagedUserTests(unittest.TestCase):

    def binding(self, name, subjects, owned=True):
        return {'metadata': {'name': name, 'resourceVersion': '77', 'labels': {'managed-by': users.MANAGER if owned else 'someone-else'}}, 'subjects': subjects}

    def test_exact_owned_user_removed_preserving_groups_serviceaccounts_and_shared_users(self):
        target = {'kind': 'User', 'name': 'alice'}
        retained = [{'kind': 'Group', 'name': 'alice'}, {'kind': 'ServiceAccount', 'name': 'alice', 'namespace': 'other'}, {'kind': 'User', 'name': 'bob'}]
        document = {'items': [self.binding('owned-shared', [target] + retained), self.binding('unowned', [target], False)]}
        plans = users.binding_patches(document, 'alice')
        self.assertEqual(len(plans), 1)
        name, patch = plans[0]
        self.assertEqual(name, 'owned-shared')
        self.assertEqual(patch[0], {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '77'})
        self.assertEqual(patch[1]['value'], [target] + retained)
        self.assertEqual(patch[2]['value'], retained)
        self.assertEqual(document['items'][0]['subjects'], [target] + retained)

    def test_unowned_and_same_name_non_user_subjects_are_untouched(self):
        target = {'kind': 'User', 'name': 'alice'}
        document = {'items': [self.binding('unowned', [target], False), self.binding('group', [{'kind': 'Group', 'name': 'alice'}])]}
        self.assertEqual(users.binding_patches(document, 'alice'), [])
        malformed = self.binding('missing-revision', [target])
        del malformed['metadata']['resourceVersion']
        with self.assertRaisesRegex(ValueError, 'resourceVersion'):
            users.binding_patches({'items': [malformed]}, 'alice')

    def test_failed_api_patch_does_not_change_credential_file_or_delete_any_object(self):
        document = {'items': [self.binding('owned', [{'kind': 'User', 'name': 'alice'}])]}

        def client(argv, **kwargs):
            if argv[1] == 'get':
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(document))
            self.assertEqual(argv[1:3], ['patch', 'clusterrolebinding'])
            raise subprocess.CalledProcessError(1, argv)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'htpasswd'
            path.write_text('alice:synthetic\nbob:synthetic\n')
            before = path.read_bytes()
            with self.assertRaises(subprocess.CalledProcessError):
                users.remove_access('alice', path, client)
            self.assertEqual(path.read_bytes(), before)

    def test_exact_htpasswd_identity_and_no_global_identity_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'htpasswd'
            path.write_text('a.b:synthetic\naxb:synthetic\n')
            client = mock.Mock(return_value=subprocess.CompletedProcess([], 0, stdout='{"items":[]}'))
            users.remove_access('a.b', path, client)
            self.assertEqual(path.read_text(), 'axb:synthetic\n')
            self.assertEqual(client.call_count, 1)
        for name in ('bad\nname', "bad'name", '$(command)', ''):
            with self.assertRaises(ValueError):
                users.validate_users(name)

    def test_jump_account_guards_execute_remote_ownership_check_before_mutation(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/manage-cluster-users.yaml').read_text())
        for step in workflow['jobs']['manage-users']['steps']:
            if step['name'] not in ('Create Jump Host Users', 'Delete Jump Host Users'):
                continue
            command = step['run']
            self.assertIn("grep -Fxq 'managed-by=manage-cluster-users-workflow' || exit 1", command)
            self.assertNotIn('StrictHostKeyChecking=no', command)
            if step['name'] == 'Delete Jump Host Users':
                self.assertLess(command.index('grep -Fxq'), command.index('sudo -n userdel'))
            else:
                self.assertIn('printf', command)
                self.assertNotIn("echo '$pubkey'", command)

    def test_unowned_jump_account_is_preserved_by_actual_step(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/manage-cluster-users.yaml').read_text())
        step = next((step for step in workflow['jobs']['manage-users']['steps'] if step['name'] == 'Delete Jump Host Users'))
        shell = step['run'].replace('${{ inputs.users }}', '${REQUESTED_USERS}')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flag = root / 'mutation'
            fake = {'ssh': '#!/bin/bash\nexec bash -c "${@: -1}"\n', 'getent': '#!/bin/bash\necho "fixture:x:1000:1000:unowned:/home/fixture:/bin/bash"\n', 'sudo': '#!/bin/bash\ntouch "' + str(flag) + '"\nexit 0\n', 'userdel': '#!/bin/bash\ntouch "' + str(flag) + '"\nexit 0\n'}
            for name, text in fake.items():
                path = root / name
                path.write_text(text)
                path.chmod(448)
            environ = dict(os.environ, PATH=str(root) + ':/usr/bin:/bin', REQUESTED_USERS='fixture', JUMP_HOST_SSH_KEY='synthetic-only', JUMP_SSH_DIR=str(root), JUMP_HOST_USER='fixture', JUMP_HOST='example.test', GITHUB_STEP_SUMMARY=str(root / 'summary'))
            result = subprocess.run(['bash', '-c', shell], env=environ, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(flag.exists())

class EndpointAndFailureTests(unittest.TestCase):

    def test_missing_or_malformed_private_endpoint_refs_create_no_file_or_command(self):
        cases = [{}, {'ENDPOINT_BUILD_PASSWORD': 'synthetic', 'ENDPOINT_SSH_AUTHORIZED_KEYS': '[]'}, {'ENDPOINT_BUILD_PASSWORD': 'synthetic', 'ENDPOINT_SSH_AUTHORIZED_KEYS': 'not-json'}, {'ENDPOINT_BUILD_PASSWORD': 'synthetic', 'ENDPOINT_SSH_AUTHORIZED_KEYS': '["malformed"]'}]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(endpoint.subprocess, 'run') as client:
            path = Path(tmp) / 'input.json'
            for environ in cases:
                with self.assertRaises(ValueError):
                    endpoint.prepare(environ, path)
                self.assertFalse(path.exists())
            client.assert_not_called()

    def test_native_invalid_public_key_fails_before_hashing_or_private_file(self):
        environ = {'ENDPOINT_BUILD_PASSWORD': 'synthetic', 'ENDPOINT_SSH_AUTHORIZED_KEYS': '["ssh-ed25519 AAAA"]'}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(endpoint.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['ssh-keygen'])) as client:
            path = Path(tmp) / 'input.json'
            with self.assertRaisesRegex(ValueError, 'invalid site SSH'):
                endpoint.prepare(environ, path)
            self.assertEqual(client.call_count, 1)
            self.assertFalse(path.exists())

    def test_finalizer_rejects_invalid_or_unprivileged_context_before_user_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flag = root / 'unexpected'
            (root / 'id').write_text('#!/bin/sh\necho 1000\n')
            (root / 'id').chmod(448)
            (root / 'usermod').write_text('#!/bin/sh\ntouch "' + str(flag) + '"\n')
            (root / 'usermod').chmod(448)
            env = dict(os.environ, PATH=str(root) + ':/usr/bin:/bin')
            for account in ('invalid;command', 'fixture'):
                result = subprocess.run(['bash', str(ROOT / 'scripts/finalize-endpoint-template.sh'), account], env=env, capture_output=True)
                self.assertEqual(result.returncode, 2)
            self.assertFalse(flag.exists())

    def test_provisioning_failure_has_no_destroy_or_credentials_and_retains_explicit_destroy(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/deploy-vsphere-vms.yaml').read_text())
        self.assertNotIn('cleanup-on-failure', workflow['jobs'])
        retained = workflow['jobs']['retain-on-failure']
        self.assertIn("needs.test.result == 'failure'", retained['if'])
        self.assertNotIn('env', retained)
        commands = '\n'.join((step.get('run', '') for step in retained['steps']))
        self.assertNotRegex(commands, '(?m)^\\s*terraform\\s')
        self.assertNotIn('destroy -', commands)
        deploy = workflow['jobs']['deploy']
        destroys = [step for step in deploy['steps'] if 'terraform destroy' in step.get('run', '')]
        self.assertEqual(len(destroys), 1)
        self.assertIn("inputs.action == 'destroy'", destroys[0]['if'])

    def test_endpoint_recipe_has_no_retained_credential_assignments_and_private_cleanup(self):
        variables = (ROOT / 'environments/example/packer/endpoint-template.pkrvars.hcl').read_text()
        for key in ('ssh_password', 'ssh_password_hash', 'ssh_authorized_keys'):
            self.assertNotRegex(variables, '(?m)^' + key + '\\s*=')
        workflow = yaml.safe_load((ROOT / '.github/workflows/build-endpoint-template.yaml').read_text())
        for name in ('validate', 'deploy'):
            steps = workflow['jobs'][name]['steps']
            cleanup = steps[-1]
            self.assertEqual(cleanup['name'], 'Remove private build input')
            self.assertEqual(cleanup['if'], '${{ always() }}')
            runs = '\n'.join((step.get('run', '') for step in steps))
            self.assertNotIn('packer-endpoint-template.log', runs)
            self.assertIn('-var-file="$ENDPOINT_PRIVATE_INPUT"', runs)
        hcl = (ROOT / 'packer/ubuntu-2404/ubuntu-2404.pkr.hcl').read_text()
        self.assertGreater(hcl.rindex('provisioner "file"'), hcl.index('# Final validation'))
        self.assertIn('sudo -n bash /tmp/finalize-endpoint-template.sh', hcl)
if __name__ == '__main__':
    unittest.main()
