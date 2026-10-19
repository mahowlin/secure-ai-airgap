#!/usr/bin/env python3
"""Fail before runner-host changes when selected runtime references are absent."""
import os
from pathlib import Path


def required_references(tags):
    selected = {tag.strip() for tag in tags.split(',') if tag.strip()}
    if not selected:
        raise ValueError('at least one Ansible tag is required')
    all_tasks = bool(selected & {'all', 'tagged'})
    required = ['JUMP_HOST_SSH_KEY', 'JUMP_HOST_KNOWN_HOSTS']
    if all_tasks or 'webserver' in selected:
        required.append('HTTP_SERVER_PASSWORD')
    if all_tasks or 'gitea' in selected:
        required.append('GITEA_ADMIN_PASSWORD')
    if all_tasks or 'runner' in selected:
        required.append('RUNNER_NAME')
    return required


def validate(tags, environ):
    missing = [name for name in required_references(tags) if not environ.get(name, '').strip()]
    if missing:
        raise ValueError('missing selected runtime references: ' + ', '.join(missing))


if __name__ == '__main__':
    try:
        tags = os.environ.get('SELECTED_TAGS', '')
        validate(tags, os.environ)
        if os.environ.get('GITHUB_OUTPUT'):
            with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
                stream.write('runner_selected=' + str('RUNNER_NAME' in required_references(tags)).lower() + '\n')
    except ValueError as error:
        raise SystemExit(str(error)) from None
