# Copyright (c) 2026 Cisco and/or its affiliates.
# Licensed under the Cisco Sample Code License, Version 1.1. See LICENSE.
"""Optional explicit cross-repository selected-consumer check, no registry access."""
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('COMPUTE_ROOT') and os.environ.get('GITOPS_ROOT'),
                     'set exact Compute and GitOps candidate roots for the cross-repository join')
class SelectedConsumerImageJoin(unittest.TestCase):
    def test_selected_compute_pins_and_rendered_timescape_images_are_in_copy_recipe(self):
        compute = Path(os.environ['COMPUTE_ROOT'])
        gitops = Path(os.environ['GITOPS_ROOT'])
        files = list((compute / 'data/openshift').glob('*/metadata.yaml'))
        self.assertTrue(files)
        spec = importlib.util.spec_from_file_location('release', ROOT / 'scripts/inspect-isovalent-release.py')
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        selection = release.load_selection(Path('mirror/isovalent-selection.yaml'), ROOT)
        recipe = yaml.safe_load((ROOT / selection['imageSet']).read_text())
        refs = {image['name'] for image in recipe['mirror']['additionalImages']}
        metadata = [yaml.safe_load(path.read_text()) for path in files]
        for cluster in metadata:
            for name, image in cluster.get('ciliumDay1', {}).get('images', {}).items():
                with self.subTest(cluster=cluster['clusterName'], component=name):
                    self.assertIn(image, refs)
        apps = [d for d in yaml.safe_load_all(subprocess.check_output(
            ['kubectl', 'kustomize', str(gitops / 'clusters/example-management')], text=True)) if d]
        selected = [app for app in apps if app.get('kind') == 'Application'
                    and any(key in app['metadata']['name'] for key in ('timescape', 'hubble-k8s-exporter'))
                    and 'helm' in app['spec'].get('source', {})]
        self.assertTrue(any('hubble-k8s-exporter' in app['metadata']['name'] for app in selected))
        rendered_documents = []
        for app in selected:
            source = app['spec']['source']
            helm = source['helm']
            cmd = ['helm', 'template', app['metadata']['name'], str(gitops / source['path']),
                   '--namespace', app['spec']['destination'].get('namespace', 'default'), '-f', '-']
            for parameter in helm.get('parameters', []):
                cmd += ['--set-string' if parameter.get('forceString') else '--set', parameter['name'] + '=' + parameter['value']]
            rendered = subprocess.run(cmd, input=helm.get('values', '{}'), text=True, capture_output=True, check=True)
            rendered_documents.extend(yaml.safe_load_all(rendered.stdout))
        images = set()
        def walk(value):
            if isinstance(value, dict):
                if isinstance(value.get('image'), str):
                    images.add(value['image'])
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        for document in rendered_documents:
            walk(document)
        self.assertTrue(images)
        repositories = {tuple(release.split_image(image)[1:]) for image in refs}
        selected_runtime = set(selection['runtimeImages'])
        selected_repositories = {tuple(release.split_image(image)[1:]) for image in selected_runtime}
        for image in images:
            with self.subTest(image=image):
                identity = tuple(release.split_image(image)[1:])
                self.assertIn(identity, repositories)
                self.assertIn(identity, selected_repositories)


if __name__ == '__main__':
    unittest.main()
