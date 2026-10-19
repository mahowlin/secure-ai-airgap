"""Non-secret destination joins only; no registry connection or image transfer."""
import copy
import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('registry', Path(__file__).resolve().parents[1]/'scripts/read-registry-contract.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class RegistryContract(unittest.TestCase):
    def test_standalone_and_changed_project_precedence(self):
        profile={'registry_dns_name':'standalone.example.org','registry_port':5000}
        self.assertEqual(m.resolve(profile),'standalone.example.org:5000')
        project={'platformServices':{'registry':'changed.example.net:5443','registryTrustPolicy':'additional-ca'},'registryTrustPolicy':'additional-ca','imageDelivery':{'mode':'mirrored','registry':'changed.example.net:5443'}}
        self.assertEqual(m.resolve(profile,project),'changed.example.net:5443')
        with self.assertRaises(ValueError):m.resolve(profile,project,'standalone.example.org:5000')
        bad=copy.deepcopy(project);bad['imageDelivery']['registry']='other.example.org:5000'
        with self.assertRaises(ValueError):m.resolve(profile,bad)
        del project['platformServices']['registry']
        with self.assertRaises(KeyError):m.resolve(profile,project)
