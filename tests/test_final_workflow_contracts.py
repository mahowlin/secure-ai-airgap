"""Public workflow adapter and pre-mutation canonical destination checks."""
import os
from pathlib import Path
import subprocess
import sys
import json
import tempfile
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[1]

class FinalWorkflowContracts(unittest.TestCase):
    def test_conflicting_selection_fails_before_output_or_registry_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'output';selection=root/'selection.yaml';selection.write_text('destination:\n  registry: conflict.example:5000\n')
            env=dict(os.environ,PROFILE_NAME='example',PROJECT_CONTRACT='',LEGACY_REGISTRY_OVERRIDE='')
            result=subprocess.run([sys.executable,str(ROOT/'scripts/read-registry-contract.py'),'--output',str(out),'--selection',str(selection)],env=env,text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse(out.exists());self.assertIn('conflicts',result.stderr)

    def test_public_adapters_and_selected_oci_boundary(self):
        doc=yaml.safe_load((ROOT/'.github/workflows/sync-images.yaml').read_text());on=doc.get('on',doc.get(True))
        for trigger in ['workflow_call','workflow_dispatch']:
            for field in ['environment','project_contract','hcp_cache_contract','hcp_cache_ca_file']:
                self.assertIn(field,on[trigger]['inputs'])
        self.assertIn('read-registry-contract.py',str(doc['jobs']['validate-inputs']))
        for name in ['sync-platform','sync-redhat-ops','sync-isovalent','sync-other','sync-portworx','sync-hcp-cache']:
            self.assertIn('validate-inputs',doc['jobs'][name]['needs'])
        oci=doc['jobs']['package-oci'];body=str(oci)
        self.assertNotIn('--insecure',body);self.assertIn('prepare-isovalent-auth.py',body)
        self.assertIn('--ca-file "$SSL_CERT_FILE" --registry-config "$HELM_REGISTRY_CONFIG"',body)
        self.assertTrue(any(s.get('if')=='always()' and 'ISOVALENT_AUTH_DIR' in s.get('run','') for s in oci['steps']))

    def test_actual_oci_push_uses_private_selected_ca_and_auth_then_cleans(self):
        doc=yaml.safe_load((ROOT/'.github/workflows/sync-images.yaml').read_text())
        job=doc['jobs']['package-oci'];body=next(s['run'] for s in job['steps'] if s.get('name')=='Package and Push to OCI Registry')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bin_dir=root/'bin';bin_dir.mkdir();chart=root/'charts/platform-idms';chart.mkdir(parents=True);(chart/'Chart.yaml').write_text('version: 1.0.0-fixture\n')
            private=root/'auth';private.mkdir();(private/'config.json').write_text('{"auths":{}}');(private/'ca.pem').write_text('synthetic-public-ca')
            helm=bin_dir/'helm';helm.write_text('#!'+sys.executable+'\n'+"""import sys,os,json
from pathlib import Path
a=sys.argv[1:]
if a[0]=='package':
 p=Path(a[a.index('-d')+1]);p.mkdir(parents=True,exist_ok=True);(p/'platform-idms-1.0.0-fixture.tgz').write_bytes(b'synthetic-package')
elif a[:2]==['show','chart']:print('version: 1.0.0-fixture')
elif a[0]=='push':
 assert '--insecure-skip-tls-verify' not in a
 assert a[a.index('--ca-file')+1]==os.environ['SSL_CERT_FILE']
 assert a[a.index('--registry-config')+1]==os.environ['HELM_REGISTRY_CONFIG']
 assert Path(a[1]).is_file()
 Path(os.environ['PUSH_LOG']).write_text(json.dumps(a))
else:raise SystemExit(95)
""");helm.chmod(0o755)
            env=dict(os.environ,PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],RUNNER_TEMP=str(root),LOCAL_REGISTRY='selected.example:5000',PLATFORM_DAY2_PROFILE='',GITHUB_STEP_SUMMARY=str(root/'summary'),SSL_CERT_FILE=str(private/'ca.pem'),HELM_REGISTRY_CONFIG=str(private/'config.json'),ISOVALENT_AUTH_DIR=str(private),PUSH_LOG=str(root/'push.json'))
            result=subprocess.run(['bash','-euo','pipefail','-c',body],cwd=root,env=env,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr);self.assertTrue((root/'push.json').is_file());self.assertEqual(list(root.glob('platform-idms.*')),[])
            cleanup=next(s['run'] for s in job['steps'] if s.get('name')=='Remove selected OCI credentials and trust')
            subprocess.run(['bash','-euo','pipefail','-c',cleanup],env=env,check=True);self.assertFalse(private.exists())

    def test_profile_alias_and_failed_push_cleanup(self):
        doc=yaml.safe_load((ROOT/'.github/workflows/sync-images.yaml').read_text())
        job=doc['jobs']['package-oci'];body=next(s['run'] for s in job['steps'] if s.get('name')=='Package and Push to OCI Registry')
        for fail_push in ('', '2'):
            with self.subTest(fail_push=fail_push),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);bin_dir=root/'bin';bin_dir.mkdir();chart=root/'charts/platform-idms';chart.mkdir(parents=True);(chart/'Chart.yaml').write_text('version: 1.0.0-fixture\n')
                runtime=root/'runtime';runtime.mkdir();private=root/'auth';private.mkdir();(private/'config.json').write_text('{"auths":{}}');(private/'ca.pem').write_text('synthetic-public-ca')
                helm=bin_dir/'helm';helm.write_text('#!'+sys.executable+'\n'+'''import sys,os,json
from pathlib import Path
a=sys.argv[1:]
version=Path('charts/platform-idms/Chart.yaml').read_text().split(':',1)[1].strip()
if a[0]=='package':
 p=Path(a[a.index('-d')+1]);p.mkdir(parents=True,exist_ok=True);(p/f'platform-idms-{version}.tgz').write_bytes(b'synthetic-package')
elif a[:2]==['show','chart']:print('version: '+version)
elif a[0]=='push':
 assert a[a.index('--ca-file')+1]==os.environ['SSL_CERT_FILE']
 assert a[a.index('--registry-config')+1]==os.environ['HELM_REGISTRY_CONFIG']
 assert '--insecure-skip-tls-verify' not in a
 assert Path(a[1]).is_file()
 log=Path(os.environ['PUSH_LOG']);calls=json.loads(log.read_text()) if log.exists() else []
 calls.append(a);log.write_text(json.dumps(calls))
 if os.environ['FAIL_PUSH']==str(len(calls)):raise SystemExit(23)
else:raise SystemExit(95)
''');helm.chmod(0o755)
                sed=bin_dir/'sed';sed.write_text('#!'+sys.executable+'\n'+'''import sys,re
from pathlib import Path
a=sys.argv[1:];assert a[0]=='-i'
version=a[1].split('version: ',1)[1].rsplit('/',1)[0]
p=Path(a[-1]);p.write_text(re.sub(r'^version:.*$', 'version: '+version, p.read_text(), flags=re.M))
''');sed.chmod(0o755)
                env=dict(os.environ,PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],RUNNER_TEMP=str(runtime),LOCAL_REGISTRY='selected.example:5000',PLATFORM_DAY2_PROFILE='ocp-4.19-cilium-1.19',GITHUB_STEP_SUMMARY=str(root/'summary'),SSL_CERT_FILE=str(private/'ca.pem'),HELM_REGISTRY_CONFIG=str(private/'config.json'),ISOVALENT_AUTH_DIR=str(private),PUSH_LOG=str(root/'push.json'),FAIL_PUSH=fail_push)
                result=subprocess.run(['bash','-euo','pipefail','-c',body],cwd=root,env=env,text=True,capture_output=True)
                self.assertEqual(result.returncode,23 if fail_push else 0,result.stderr)
                calls=json.loads((root/'push.json').read_text());self.assertEqual(len(calls),2)
                self.assertEqual(Path(calls[1][1]).parent.name,'alias')
                self.assertTrue(Path(calls[1][1]).name.startswith('platform-idms-0.0.0-platform-day2.'))
                self.assertEqual(list(runtime.iterdir()),[], 'owned package dir and any mistaken sibling must be removed')
                cleanup=next(s['run'] for s in job['steps'] if s.get('name')=='Remove selected OCI credentials and trust')
                subprocess.run(['bash','-euo','pipefail','-c',cleanup],env=env,check=True);self.assertFalse(private.exists())

if __name__=='__main__':unittest.main()
