#!/usr/bin/env python3
"""Existing image-sync producer extension and read-only selected-cache check.

Uses the public full-byte graph verifier, not the private lab population tools.
Reader mode never copies. Receipts describe raw content, not native cold pulls.
"""
import argparse,hashlib,importlib.util,json,os,re,ssl,tempfile,subprocess
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('image_graph',ROOT/'mirror/package-inputs/hcp-oc-mirror-operator/verify-image.py')
graph=importlib.util.module_from_spec(spec);spec.loader.exec_module(graph)

def sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def load_contract(path,expected_site):
    c=json.loads(path.read_text());unsigned={k:v for k,v in c.items() if k!='sha256'}
    if c.get('format')!='hcp-selected-cache/v1' or sha(unsigned)!=c.get('sha256'):raise ValueError('selected contract content hash/format mismatch')
    p=c['policy']
    if p['siteRegistry']!=expected_site:raise ValueError('contract differs from selected site registry')
    if p['producerTrust']['mode'] not in ('system','additional-ca'):raise ValueError('TLS verification is mandatory')
    if not 0<len(c['roots'])<=p['maxRoots']:raise ValueError('empty or over-budget selected roots')
    seen=set()
    for r in c['roots']:
        if not re.fullmatch(r'[a-z0-9][a-z0-9.:/_-]+@sha256:[0-9a-f]{64}',r['source']) or r['source'] in seen:raise ValueError('duplicate/unpinned root')
        base=r['source'].split('@',1)[0]
        if '/' not in base or ':' in base.rsplit('/',1)[-1]:raise ValueError('source must be canonical authority/repository@digest, not a tag alias')
        seen.add(r['source'])
        for k in ('siteRepository','cacheRepository'):
            if not re.fullmatch(r'[a-z0-9]+(?:[._/-][a-z0-9]+)*',r[k]) or '..' in r[k].split('/'):raise ValueError('unsafe destination repository')
    return c

def destination(c,r,reader):
    cached=reader and r['scope']=='cache';p=c['policy']
    return f"{p['pullRegistry'] if cached else p['siteRegistry']}/{r['cacheRepository'] if cached else r['siteRepository']}@{r['source'].rsplit('@',1)[1]}"

def run(c,client,stage,copy=False,phase="workers"):
    if stage=='reader' and copy:raise ValueError('reader verification cannot copy images')
    images={}
    for root in c['roots']:
        if stage=='reader' and phase=='control-planes' and root['phase']!='control-planes':continue
        targets={destination(c,root,False)}
        if stage=='reader':targets.add(destination(c,root,True))
        for dest in sorted(targets):
            repo,digest=dest.rsplit('@',1)
            if copy:
                # Immutable digest tag, existing conflicts/referrers are preserved.
                tag=repo+':'+digest.replace(':','-',1)
                result=graph.ensure_image(client,root['source'],tag,digest)
            else:
                result=graph.verify(client,repo,digest)
            arch={'x86_64':'amd64','aarch64':'arm64'}.get(root['architecture'],root['architecture'])
            if not any(p.get('os')=='linux' and p.get('architecture')==arch for p in result['platforms']):
                raise ValueError('selected architecture missing from image graph: '+dest)
            images[dest]=result
    return {'contractSha256':c['sha256'],'inventorySha256':c['inventorySha256'],
            'verifiedAt':datetime.now(timezone.utc).isoformat(),'stage':stage,'phase':phase,'images':images,
            'credentialRef':c['policy']['readerSecretRef'] if stage=='reader' else c['policy']['siteWriterSecretRef'],
            'readerSecretRef':c['policy']['readerSecretRef'] if stage=='reader' else c['policy']['siteWriterSecretRef'],
            'trust':c['policy']['producerTrust'],'runtimePrerequisites':c['policy']['runtimePrerequisites'],
            'qualification':'full raw descriptor/blob bytes on this runner path only; no cold-pull/offline-boot claim'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--contract',type=Path,required=True);p.add_argument('--expected-site-registry',required=True)
    p.add_argument('--stage',choices=['site','reader'],required=True);p.add_argument('--copy',action='store_true')
    p.add_argument('--phase',choices=['control-planes','workers'],default='workers')
    p.add_argument('--auth-dir',type=Path,required=True);p.add_argument('--ca-file',type=Path)
    p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--runtime-prerequisites-file',type=Path,help='Owner-observed current reader Secret UID/version and DNS/TLS evidence refs; no Secret values')
    a=p.parse_args();a.receipt.unlink(missing_ok=True);c=load_contract(a.contract,a.expected_site_registry)
    if subprocess.check_output(['crane','version'],text=True).strip().removeprefix('v')!='0.22.0':raise ValueError('selected graph verifier requires crane0.22.0')
    if a.stage=='reader' and a.copy:p.error('reader stage cannot copy')
    if a.stage=='reader' and (not a.runtime_prerequisites_file or json.loads(a.runtime_prerequisites_file.read_text()) != c['policy']['runtimePrerequisites']):
        raise ValueError('current runtime DNS/TLS/auth prerequisite observation is absent or changed')
    auth=a.auth_dir/'config.json'
    if not auth.is_file() or auth.stat().st_mode&0o077:raise ValueError('private Docker config.json with mode0600 is required')
    # Runtime credential bytes stay in a protected file, never receipt/stdout.
    creds=json.loads(auth.read_text());authorities={c['policy']['siteRegistry']}
    if a.stage=='reader':authorities.add(c['policy']['pullRegistry'])
    else:authorities.update(r['source'].split('/')[0] for r in c['roots'])
    if not authorities<=set(creds.get('auths',{})):raise ValueError('auth file lacks selected source/reader authorities')
    os.environ['DOCKER_CONFIG']=str(a.auth_dir.resolve())
    trust=c['policy']['producerTrust'];temp=None
    try:
        if trust['mode']=='additional-ca':
            if not a.ca_file or hashlib.sha256(a.ca_file.read_bytes()).hexdigest()!=trust['sha256']:raise ValueError('current public CA differs from selected trust')
            bundle=ssl.get_default_verify_paths().cafile
            if not bundle:raise ValueError('system TLS bundle unavailable')
            temp=tempfile.NamedTemporaryFile(prefix='hcp-cache-ca-',delete=False)
            temp.write(Path(bundle).read_bytes()+b'\n'+a.ca_file.read_bytes());temp.close();os.environ['SSL_CERT_FILE']=temp.name
        elif a.ca_file:raise ValueError('system trust selection rejects unrelated CA override')
        result=run(c,graph.Crane(),a.stage,a.copy,a.phase)
        a.receipt.parent.mkdir(parents=True,exist_ok=True);a.receipt.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    finally:
        if temp:Path(temp.name).unlink(missing_ok=True)
if __name__=='__main__':main()
