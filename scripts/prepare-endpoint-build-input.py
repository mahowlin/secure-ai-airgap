#!/usr/bin/env python3
"""Validate private build inputs and write only a mode0600 Packer variable file."""
import json
import os
from pathlib import Path
import re
import subprocess


def prepare(environ, output):
    password = environ.get('ENDPOINT_BUILD_PASSWORD', '')
    raw_keys = environ.get('ENDPOINT_SSH_AUTHORIZED_KEYS', '')
    if not password or not raw_keys:
        raise ValueError('ENDPOINT_BUILD_PASSWORD and ENDPOINT_SSH_AUTHORIZED_KEYS runtime references are required')
    try:
        keys = json.loads(raw_keys)
    except json.JSONDecodeError:
        raise ValueError('ENDPOINT_SSH_AUTHORIZED_KEYS must be a JSON list') from None
    if not isinstance(keys, list) or not keys or any(not isinstance(key, str) or not re.fullmatch(r'(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/]+=*(?: [^\r\n]+)?', key) for key in keys):
        raise ValueError('explicit site SSH public keys are required')
    if '\n' in password or '\r' in password:
        raise ValueError('temporary build password must be one line')
    for key in keys:
        try:
            subprocess.run(['ssh-keygen', '-l', '-f', '/dev/stdin'], input=key + '\n',
                           text=True, capture_output=True, check=True)
        except subprocess.CalledProcessError:
            raise ValueError('invalid site SSH public key') from None
    hashed = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=password + '\n',
                            text=True, capture_output=True, check=True).stdout.strip()
    # Exclusive private creation; never replace a retained input or expose secrets in argv.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'ssh_password': password, 'ssh_password_hash': hashed, 'ssh_authorized_keys': keys}, stream)
    except BaseException:
        Path(output).unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    try:
        prepare(os.environ, Path(os.environ['ENDPOINT_PRIVATE_INPUT']))
    except ValueError as error:
        raise SystemExit(str(error)) from None
