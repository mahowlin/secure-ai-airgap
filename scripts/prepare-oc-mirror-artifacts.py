#!/usr/bin/env python3
"""Prepare only declared oc-mirror operator inspection inputs; never install/mirror."""
import argparse
import io
import subprocess
import tarfile
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import urllib.request


def prepare(contract_path, output, source_root=None, fetch=False):
    contract_path, output = Path(contract_path), Path(output)
    contract = json.loads(contract_path.read_text())
    if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', contract['repository'])
            or not re.fullmatch(r'[0-9a-f]{40}', contract['commit'])
            or not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', contract['bundleImage'])
            or not contract['license'] or not contract['files']):
        raise ValueError('immutable operator provenance required')
    if (source_root is None) == (not fetch):
        raise ValueError('choose supplied source root or explicit --fetch')
    source_root = Path(source_root).resolve() if source_root is not None else None
    payload = {}
    bundle_members = {}
    if fetch and any(item['sourceKind'] == 'bundle' for item in contract['files']):
        # Existing pinned Crane reads only the selected immutable source image.
        archive = subprocess.run(['crane', 'export', contract['bundleImage'], '-'],
                                 check=True, capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            for member in bundle.getmembers():
                name = member.name.removeprefix('./').lstrip('/')
                if not member.isfile():
                    continue
                if name in bundle_members:
                    raise ValueError('duplicate bundle artifact path')
                bundle_members[name] = bundle.extractfile(member).read()
    for item in contract['files']:
        relative = PurePosixPath(item['path'])
        if (relative.is_absolute() or '..' in relative.parts or '\\' in item['path']
                or str(relative) != item['path'] or item['path'] in payload
                or not re.fullmatch(r'[0-9a-f]{64}', item['sha256'])):
            raise ValueError('unsafe or duplicate operator artifact path/hash')
        target = output / item['path']
        if any((output / Path(*relative.parts[:i])).is_symlink()
               for i in range(len(relative.parts) + 1)):
            raise ValueError('symlink operator destination')
        if source_root is not None:
            source = source_root / item['path']
            if source.is_symlink() or not source.resolve().is_relative_to(source_root):
                raise ValueError('operator source escapes supplied root')
            raw = source.read_bytes()
        elif item['sourceKind'] == 'bundle':
            raw = bundle_members[item['sourcePath']]
        elif item['sourceKind'] == 'git':
            source_path = PurePosixPath(item['sourcePath'])
            if source_path.is_absolute() or '..' in source_path.parts:
                raise ValueError('unsafe upstream source path')
            url = f"https://raw.githubusercontent.com/{contract['repository']}/{contract['commit']}/{source_path}"
            with urllib.request.urlopen(url, timeout=60) as response:
                raw = response.read()
        else:
            raise ValueError('unknown operator artifact source kind')
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('operator artifact differs from selected checksum: '+item['path'])
        if target.exists() and (not target.is_file() or target.read_bytes() != raw):
            raise ValueError('preserve conflicting prepared artifact: '+item['path'])
        payload[item['path']] = raw
    if 'LICENSE' not in payload:
        raise ValueError('license artifact required')
    # Validate every supplied byte/collision before writing any selected artifact.
    for relative, raw in payload.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    receipt = dict(contract, contractSha256=hashlib.sha256(contract_path.read_bytes()).hexdigest(),
                   verification='exact selected file hashes; not installation or signature qualification')
    (output / 'artifact-receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--source-root', type=Path, help='offline upstream-layout cache/import')
    group.add_argument('--fetch', action='store_true', help='explicit immutable upstream acquisition with normal TLS')
    args = parser.parse_args()
    prepare(args.contract, args.output, args.source_root, args.fetch)
    print('Prepared checksum-verified operator inputs; no installation or registry write')


if __name__ == '__main__':
    main()
