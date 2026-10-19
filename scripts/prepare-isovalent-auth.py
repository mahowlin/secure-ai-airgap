#!/usr/bin/env python3
"""Prepare per-job registry auth and normal CA trust; never alter runner HOME."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import shutil
import ssl
import tempfile


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


auth = module('configure-isovalent-auth')
release = module('inspect-isovalent-release')


def prepare(selection_path, output_root, github_env, expected_registry=None):
    selected = release.load_selection(selection_path)
    destination = selected.get('destination')
    if not destination or (expected_registry and expected_registry != destination['registry']):
        raise ValueError('selected destination does not match requested registry')
    config = auth.build_config(auth.required_env('REDHAT_PULL_SECRET'),
                               auth.required_env('ISOVALENT_REGISTRY_USERNAME'),
                               auth.required_env('ISOVALENT_REGISTRY_TOKEN'))
    registry_auth = config['auths'].get(destination['registry'], {})
    if not registry_auth.get('auth') and not registry_auth.get('identitytoken'):
        raise ValueError('pull secret must include authentication for the selected destination')
    private = Path(tempfile.mkdtemp(prefix='isovalent-registry-', dir=output_root))
    try:
        auth_path = private / 'config.json'
        auth.write_private_json(auth_path, config)
        cert_dir = private / 'certs'
        cert_dir.mkdir(mode=0o700)
        ca = Path(destination['caBundle']).read_bytes() if destination['caBundle'] else b''
        # Validate declared CA before exporting the auth directory to any consumer.
        context = ssl.create_default_context()
        roots = b''.join(ssl.DER_cert_to_PEM_cert(cert).encode() for cert in context.get_ca_certs(binary_form=True))
        if ca:
            context.load_verify_locations(cadata=ca.decode('ascii'))
        (cert_dir / 'ca.crt').write_bytes(ca or roots)
        bundle = private / 'ca-bundle.pem'
        bundle.write_bytes(roots + b'\n' + ca)
        values = {'ISOVALENT_AUTH_DIR': private, 'DOCKER_CONFIG': private,
                  'REGISTRY_AUTH_FILE': auth_path, 'HELM_REGISTRY_CONFIG': auth_path,
                  'ISOVALENT_CERT_DIR': cert_dir, 'SSL_CERT_FILE': bundle}
        if any('\n' in str(v) or '\r' in str(v) for v in values.values()):
            raise ValueError('unsafe environment output path')
        with github_env.open('a') as stream:
            stream.write(''.join(f'{key}={value}\n' for key, value in values.items()))
        return values
    except BaseException:
        shutil.rmtree(private)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--github-env', required=True, type=Path)
    parser.add_argument('--expected-registry')
    args = parser.parse_args()
    prepare(args.selection, args.output_root, args.github_env, args.expected_registry)


if __name__ == '__main__':
    main()
