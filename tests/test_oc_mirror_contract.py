"""The generated public operator contract is consumed without hidden site defaults."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'mirror/package-inputs/hcp-oc-mirror-operator'
spec=importlib.util.spec_from_file_location('operator_contract',BASE/'validate-contract.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class ProducerContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.path=self.root/'contract.json'
        self.data=json.loads((BASE/'contract.json').read_text())
    def check(self,registry='registry.example.com:5000'):
        self.path.write_text(json.dumps(self.data))
        return module.validate(self.root,self.path,registry)
    def test_system_roots_and_exact_destination(self):
        self.assertEqual(len(self.check()['images']),10)
        with self.assertRaisesRegex(ValueError,'destination differs'):self.check('other.example:5000')
    def test_changed_registry_has_no_old_destination(self):
        self.data=json.loads(json.dumps(self.data).replace('registry.example.com:5000','other.example:5443'))
        self.assertNotIn('registry.example.com',json.dumps(self.check('other.example:5443')))
    def test_bundle_trust_requires_exact_runtime_bytes(self):
        ca=self.root/'mirror/certs/site.pem';ca.parent.mkdir(parents=True)
        ca.write_bytes(b'non-certificate test fixture; never used for TLS')
        self.data['trust']={'mode':'bundle','caBundle':'mirror/certs/site.pem','caSha256':hashlib.sha256(ca.read_bytes()).hexdigest()}
        self.check();ca.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'checked contract'):self.check()
    def test_unknown_trust_or_missing_catalog_fails(self):
        self.data['trust']={}
        with self.assertRaisesRegex(ValueError,'explicitly select'):self.check()
        self.setUp();self.data['images'].pop()
        with self.assertRaisesRegex(ValueError,'closure'):self.check()
    def test_changed_source_or_duplicate_role_fails(self):
        self.data['images'][0]['source']='other.example/image@'+self.data['images'][0]['digest']
        with self.assertRaisesRegex(ValueError,'reviewed release path'):self.check()
        self.setUp();self.data['images'].append(copy.deepcopy(self.data['images'][0]))
        with self.assertRaisesRegex(ValueError,'closure'):self.check()
if __name__=='__main__':unittest.main()
