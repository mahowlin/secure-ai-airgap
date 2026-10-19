"""Use the existing OCI fixtures through the selected producer/reader and promoter."""
import copy,hashlib,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tests'))
from test_hcp_oc_mirror_graph import Fixture

def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
M=module('selected_images',ROOT/'scripts/hcp-cache-images.py')
P=module('bundle_promoter',ROOT/'scripts/promote-image-policy-bundle.py')
class SelectedCache(unittest.TestCase):
 def setUp(self):
  self.f=Fixture();self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.c={'format':'hcp-selected-cache/v1','inventorySha256':'fixture-inventory','selection':{'architecture':'x86_64'},'policy':{'siteRegistry':'site.example:5000','pullRegistry':'reader.example:443','siteWriterSecretRef':'site-writer','readerSecretRef':'cache-reader','maxRoots':10,'maxAgeSeconds':300,'producerTrust':{'mode':'system'},'runtimePrerequisites':{'readerSecretUID':'fixture-uid'}},'roots':[{'source':'upstream.example/release@'+self.f.root,'siteRepository':'release','cacheRepository':'content/release','scope':'cache','phase':'workers','architecture':'x86_64'}]};self.sign()
 def sign(self):
  self.c.pop('sha256',None);self.c['sha256']=M.sha(self.c);(self.root/'contract.json').write_text(json.dumps(self.c))
 def run_fixture(self,stage,copy=False):
  with patch.object(M.graph.subprocess,'run',side_effect=self.f.run),patch.object(M.graph.subprocess,'Popen',side_effect=self.f.popen):return M.run(self.c,M.graph.Crane(),stage,copy)
 def test_reader_is_full_byte_read_only_and_missing_blob_fails(self):
  result=self.run_fixture('reader');self.assertTrue(all(r['fullBytesVerified'] for r in result['images'].values()));self.assertFalse(any(c[1]=='copy' for c in self.f.calls));self.assertEqual(result['readerSecretRef'],'cache-reader')
  self.f.blobs.pop(next(iter(self.f.blobs)))
  with self.assertRaises(ValueError):self.run_fixture('reader')
  with self.assertRaisesRegex(ValueError,'cannot copy'):M.run(self.c,None,'reader',True)
 def test_control_plane_verification_does_not_require_worker_only_content(self):
  r=copy.deepcopy(self.c['roots'][0]);r.update(source='upstream.example/early@'+self.f.root,siteRepository='early',cacheRepository='content/early',scope='preceding',phase='control-planes');self.c['roots'].append(r);self.sign()
  with patch.object(M.graph.subprocess,'run',side_effect=self.f.run),patch.object(M.graph.subprocess,'Popen',side_effect=self.f.popen):
   result=M.run(self.c,M.graph.Crane(),'reader',phase='control-planes')
  self.assertEqual(set(result['images']),{M.destination(self.c,r,True)})
 def test_architecture_is_checked_from_graph_not_inventory_label_only(self):
  self.c['roots'][0]['architecture']='s390x'
  with self.assertRaisesRegex(ValueError,'architecture'):self.run_fixture('reader')
 def test_contract_destination_hash_and_duplicate_fail(self):
  M.load_contract(self.root/'contract.json','site.example:5000')
  with self.assertRaisesRegex(ValueError,'site registry'):M.load_contract(self.root/'contract.json','other.example')
  self.c['roots'].append(copy.deepcopy(self.c['roots'][0]));self.sign()
  with self.assertRaisesRegex(ValueError,'duplicate'):M.load_contract(self.root/'contract.json','site.example:5000')
  self.c['sha256']='changed';(self.root/'contract.json').write_text(json.dumps(self.c))
  with self.assertRaisesRegex(ValueError,'hash'):M.load_contract(self.root/'contract.json','site.example:5000')
 def test_existing_bundle_promotion_is_complete_and_repeatable(self):
  receipt=self.run_fixture('site');path=self.root/'verification.json';path.write_text(json.dumps(receipt))
  args=SimpleNamespace(sync_target='hcp-cache',cache_contract=str(self.root/'contract.json'),cache_verification=str(path),registry='site.example:5000',output_root=str(self.root/'bundles'),run_id='fixture-run',workflow='sync-images')
  P.promote_selected_cache(args);first={str(p.relative_to(self.root)):p.read_bytes() for p in (self.root/'bundles').rglob('*') if p.is_file()};P.promote_selected_cache(args);self.assertEqual(first,{str(p.relative_to(self.root)):p.read_bytes() for p in (self.root/'bundles').rglob('*') if p.is_file()})
  receipt['images']={};path.write_text(json.dumps(receipt))
  with self.assertRaisesRegex(ValueError,'incomplete'):P.promote_selected_cache(args)
 def test_workflow_explicit_target_separate_bootstrap_and_tls(self):
  import yaml
  workflow=yaml.safe_load((ROOT/'.github/workflows/sync-images.yaml').read_text())
  job=workflow['jobs']['sync-hcp-cache'];self.assertIn("inputs.sync_target == 'hcp-cache'",job['if']);text=json.dumps(job)
  self.assertNotIn('--insecure',text);self.assertNotIn('tls-verify=false',text);self.assertIn('HCP_CACHE_SOURCE_AUTH',text);self.assertIn('promote-image-policy-bundle.py',text)
if __name__=='__main__':unittest.main()
