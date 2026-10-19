#!/usr/bin/env python3
"""Resolve the image pipeline destination from explicit non-secret input data."""
import argparse
import os
from pathlib import Path
import re
import yaml


def resolve(profile, project=None, override=''):
    if project is None:
        registry = f"{profile['registry_dns_name']}:{profile['registry_port']}"
    else:
        service = project['platformServices']
        registry = service['registry']
        if project['imageDelivery']['registry'] != registry or project['registryTrustPolicy'] != service['registryTrustPolicy']:
            raise ValueError('generated registry/trust contract conflicts internally')
        if project['imageDelivery']['mode'] != 'mirrored':
            raise ValueError('image mirroring requires explicitly selected mirrored delivery')
    if not re.fullmatch(r'[A-Za-z0-9.-]+:[0-9]+', registry) or not 1 <= int(registry.rsplit(':', 1)[1]) <= 65535:
        raise ValueError('registry must be an explicit DNS host:port')
    if override and override != registry:
        raise ValueError('OCP_REGISTRY_URL conflicts with selected input; edit canonical input')
    return registry


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--selection', type=Path, help='Require this selected Isovalent destination to match the canonical registry')
    args = p.parse_args()
    name = os.environ['PROFILE_NAME']
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('invalid explicit execution profile')
    root = Path(__file__).resolve().parents[1]
    profile = yaml.safe_load((root/'environments'/name/'group_vars.yml').read_text())
    contract = os.environ.get('PROJECT_CONTRACT', '')
    project = yaml.safe_load(Path(contract).read_text()) if contract else None
    registry = resolve(profile, project, os.environ.get('LEGACY_REGISTRY_OVERRIDE', ''))
    if args.selection:
        selected = yaml.safe_load(args.selection.read_text())
        if selected['destination']['registry'] != registry:
            raise ValueError('Isovalent destination conflicts with canonical registry; refusing writes')
    with args.output.open('a') as out:
        out.write(f'registry={registry}\n')


if __name__ == '__main__':
    main()
