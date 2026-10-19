"""Selected mirror recipe joins; no registry access or mirroring occurs."""
from pathlib import Path
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]

class SelectedMirrorInputs(unittest.TestCase):
    def test_clife_recipe_and_selected_catalog_channel_agree(self):
        release=yaml.safe_load((ROOT/'mirror/releases/clife-1.19.7.yaml').read_text())
        config=yaml.safe_load((ROOT/'mirror/isovalent-images.yaml').read_text())
        channel='.'.join(str(release['version']).split('.')[:2])
        clife=[p for operator in config['mirror']['operators'] for p in operator['packages'] if p['name']=='clife']
        self.assertEqual(len(clife),1)
        self.assertEqual(clife[0]['defaultChannel'],channel)
        self.assertEqual([c['name'] for c in clife[0]['channels']],[channel])
        configured={i['name'] for i in config['mirror']['additionalImages']}
        self.assertIn(release['clifeBundle']['controllerImage'],configured)
        for image in release['runtimeImages']:
            self.assertIn(f"{image['image']}@{image['digest']}",configured)

if __name__ == '__main__': unittest.main()
