#!/usr/bin/env python3
"""Create a new package-input bundle from exact local Git blobs, without fetching."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import yaml

spec = importlib.util.spec_from_file_location('package_input', Path(__file__).with_name('verify-clife-package-input.py'))
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def git(source, *args):
    return subprocess.run(['git', '-C', str(source), *args], check=True, capture_output=True).stdout


def promote(source, repository, revision, contract_path, output):
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('source revision must be a full Git commit hash')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('repository must be an owner/name identity')
    path = PurePosixPath(contract_path)
    if path.is_absolute() or '..' in path.parts or not re.fullmatch(r'data/package-contracts/[A-Za-z0-9_.-]+\.yaml', contract_path):
        raise ValueError('contract path must identify a canonical package contract')
    origin = git(source, 'remote', 'get-url', 'origin').decode().strip()
    if origin not in (f'https://github.com/{repository}.git', f'https://github.com/{repository}', f'git@github.com:{repository}.git'):
        raise ValueError('source origin does not match requested repository')
    if git(source, 'rev-parse', revision + '^{commit}').decode().strip() != revision:
        raise ValueError('source revision does not identify the requested commit')
    # Caller refreshes origin/main under normal review before choosing a merged revision.
    if subprocess.run(['git', '-C', str(source), 'merge-base', '--is-ancestor', revision,
                       'refs/remotes/origin/main'], capture_output=True).returncode:
        raise ValueError('source revision is not on the locally verified origin/main lineage')
    if output.exists():
        raise ValueError('refusing to replace an existing promoted bundle')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.package-input-', dir=output.parent))
    try:
        artifacts = {}
        for name, (local, fixed_path) in verifier.EXPECTED_ARTIFACTS.items():
            source_path = fixed_path or contract_path
            blob = git(source, 'show', revision + ':' + source_path)
            (stage / local).write_bytes(blob)
            artifacts[name] = {'sourcePath': source_path, 'localPath': local,
                               'sha256': hashlib.sha256(blob).hexdigest()}
        provenance = {'apiVersion': verifier.EXPECTED_API_VERSION, 'kind': verifier.EXPECTED_KIND,
                      'metadata': {'name': path.stem},
                      'spec': {'source': {'repository': repository, 'revision': revision}, 'artifacts': artifacts}}
        (stage / 'provenance.yaml').write_text(yaml.safe_dump(provenance, sort_keys=False))
        verifier.verify(stage, repository, revision, contract_path)
        # Validate original schema and semantic checks before publishing the local directory.
        import sys
        subprocess.run([sys.executable, str(stage / 'validate-contract.py'), str(stage / 'contract.yaml'),
                        '--schema', str(stage / 'contract.schema.json')], check=True, capture_output=True)
        stage.rename(output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--contract-path', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    promote(args.source, args.repository, args.revision, args.contract_path, args.output)


if __name__ == '__main__':
    main()
