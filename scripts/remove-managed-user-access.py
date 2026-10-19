#!/usr/bin/env python3
"""Remove exact User subjects only from workflow-owned bindings; retain identities."""
import argparse
import base64
import binascii
import json
from pathlib import Path
import re
import subprocess

MANAGER = 'manage-cluster-users-workflow'


def validate_users(value):
    users = [user.strip() for user in value.split(',') if user.strip()]
    if not users or any(not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]{0,252}', user) for user in users):
        raise ValueError('users must be nonempty safe account identities')
    return users


def binding_patches(document, user):
    plans = []
    for binding in document['items']:
        metadata = binding['metadata']
        if metadata.get('labels', {}).get('managed-by') != MANAGER:
            continue
        subjects = binding.get('subjects', [])
        retained = [subject for subject in subjects if not (subject.get('kind') == 'User' and subject.get('name') == user)]
        if len(retained) == len(subjects):
            continue
        # A concurrent change must fail, never overwrite another user's new grant.
        revision = metadata.get('resourceVersion')
        if not revision:
            raise ValueError('owned binding lacks resourceVersion; refusing unguarded patch')
        plans.append((metadata['name'], [
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': revision},
            {'op': 'test', 'path': '/subjects', 'value': subjects},
            {'op': 'replace', 'path': '/subjects', 'value': retained}]))
    return plans


def remove_access(user, htpasswd_file, client=subprocess.run):
    if validate_users(user) != [user]:
        raise ValueError("one exact account identity is required")
    document = json.loads(client(['oc', 'get', 'clusterrolebindings', '-o', 'json'], check=True, capture_output=True, text=True).stdout)
    plans = binding_patches(document, user)
    for name, patch in plans:
        client(['oc', 'patch', 'clusterrolebinding', name, '--type=json', '-p', json.dumps(patch)],
               check=True, capture_output=True, text=True)
    # Exact comparison: usernames containing dots are not regexes.
    lines = htpasswd_file.read_text().splitlines(keepends=True)
    htpasswd_file.write_text(''.join(line for line in lines if line.partition(':')[0] != user))
    print(f'Removed managed User subjects from {len(plans)} binding(s); unrelated grants and User/Identity objects retained')


def decode_secret_observation(observation, htpasswd_file, name, namespace):
    raw = observation.read_text()
    if not raw.strip():
        return False  # oc --ignore-not-found exited successfully with no object.
    try:
        document = json.loads(raw)
        metadata = document['metadata']
        encoded = document['data']['htpasswd']
        if (document.get('apiVersion') != 'v1' or document.get('kind') != 'Secret'
                or metadata.get('name') != name or metadata.get('namespace') != namespace
                or not isinstance(metadata.get('resourceVersion'), str) or not metadata['resourceVersion']
                or not isinstance(metadata.get('uid'), str) or not metadata['uid']
                or not isinstance(encoded, str)):
            raise ValueError
        decoded = base64.b64decode(encoded, validate=True).decode('utf-8')
    except (KeyError, TypeError, AttributeError, ValueError, binascii.Error):
        raise ValueError('invalid HTPasswd Secret observation; refusing access changes') from None
    htpasswd_file.write_text(decoded)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user')
    parser.add_argument('--htpasswd-file', type=Path)
    parser.add_argument('--validate-users')
    parser.add_argument('--decode-secret', type=Path)
    parser.add_argument('--secret-name')
    parser.add_argument('--namespace')
    args = parser.parse_args()
    if args.validate_users is not None:
        validate_users(args.validate_users)
    elif args.decode_secret and args.htpasswd_file and args.secret_name and args.namespace:
        present = decode_secret_observation(args.decode_secret, args.htpasswd_file, args.secret_name, args.namespace)
        print('true' if present else 'false')
    elif args.user and args.htpasswd_file:
        remove_access(args.user, args.htpasswd_file)
    else:
        parser.error('use --validate-users or both --user and --htpasswd-file')


if __name__ == '__main__':
    try:
        main()
    except ValueError as error:
        raise SystemExit(str(error)) from None
