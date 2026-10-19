"""Offline selected operator input preparation; no registry, workflow or cluster calls."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('operator_artifacts', ROOT/'scripts/prepare-oc-mirror-artifacts.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OperatorArtifactPreparation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name);self.source = self.root/'source';self.source.mkdir()
        self.output = self.root/'prepared';self.contract = self.root/'contract.json'
        self.files = {'LICENSE':b'synthetic license\n','bundle/manifests/selected.yaml':b'kind: ClusterServiceVersion\n'}
        for name,raw in self.files.items():
            p=self.source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        self.value = {'repository':'example/operator','commit':'a'*40,'version':'v0.1.7',
                      'bundleImage':'example/bundle@sha256:'+'b'*64,'license':'Apache-2.0',
                      'files':[{'path':p,'sha256':hashlib.sha256(v).hexdigest(),'sourceKind':'git','sourcePath':p} for p,v in self.files.items()]}
        self.write_contract()
    def write_contract(self):
        self.contract.write_text(json.dumps(self.value))
    def test_offline_import_repeat_and_license_provenance(self):
        for _ in range(2):module.prepare(self.contract,self.output,self.source)
        self.assertEqual((self.output/'LICENSE').read_bytes(),self.files['LICENSE'])
        receipt=json.loads((self.output/'artifact-receipt.json').read_text())
        self.assertEqual(receipt['commit'],'a'*40)
        self.assertEqual(receipt['files'],self.value['files'])
    def test_tamper_and_conflict_preserve_existing_bytes(self):
        (self.source/'bundle/manifests/selected.yaml').write_bytes(b'tamper')
        with self.assertRaisesRegex(ValueError,'checksum'):module.prepare(self.contract,self.output,self.source)
        self.assertFalse(self.output.exists())
        (self.source/'bundle/manifests/selected.yaml').write_bytes(self.files['bundle/manifests/selected.yaml'])
        self.output.mkdir();(self.output/'LICENSE').write_bytes(b'independent')
        with self.assertRaisesRegex(ValueError,'conflicting'):module.prepare(self.contract,self.output,self.source)
        self.assertEqual((self.output/'LICENSE').read_bytes(),b'independent')
    def test_escaping_paths_and_unpinned_sources_rejected(self):
        self.value['files'][0]['path']='../escape';self.write_contract()
        with self.assertRaisesRegex(ValueError,'unsafe'):module.prepare(self.contract,self.output,self.source)
        self.value['commit']='main';self.write_contract()
        with self.assertRaisesRegex(ValueError,'immutable'):module.prepare(self.contract,self.output,self.source)
    def test_fetch_is_explicit_immutable_and_checksum_verified(self):
        urls=[]
        class Response:
            def __init__(self,raw):self.raw=raw
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return self.raw
        def get(url,timeout):
            urls.append(url);return Response(self.files[url.split('/'+'a'*40+'/',1)[1]])
        with self.assertRaises(ValueError):module.prepare(self.contract,self.output)
        with mock.patch.object(module.urllib.request,'urlopen',side_effect=get):
            module.prepare(self.contract,self.output,fetch=True)
        self.assertEqual(len(urls),2)
        self.assertTrue(all('/example/operator/'+'a'*40+'/' in u for u in urls))

    def test_bundle_outputs_use_pinned_image_not_git_template(self):
        import io,tarfile,subprocess
        self.value['files'][1].update(sourceKind='bundle',sourcePath='manifests/selected.yaml')
        self.write_contract()
        archive=io.BytesIO()
        with tarfile.open(fileobj=archive,mode='w') as bundle:
            raw=self.files['bundle/manifests/selected.yaml']
            member=tarfile.TarInfo('manifests/selected.yaml');member.size=len(raw)
            bundle.addfile(member,io.BytesIO(raw))
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(inner):return self.files['LICENSE']
        with mock.patch.object(module.subprocess,'run',return_value=subprocess.CompletedProcess([],0,archive.getvalue())) as crane, mock.patch.object(module.urllib.request,'urlopen',return_value=Response()) as git:
            module.prepare(self.contract,self.output,fetch=True)
        self.assertEqual(crane.call_args.args[0],['crane','export',self.value['bundleImage'],'-'])
        self.assertEqual(git.call_count,1)
        self.assertEqual((self.output/'bundle/manifests/selected.yaml').read_bytes(),self.files['bundle/manifests/selected.yaml'])


if __name__=='__main__':unittest.main()
