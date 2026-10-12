#!/usr/bin/env python3
"""Read only OCI manifest/config metadata from Iris; never pull image layers."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.tools.login_apptainer_sif_preheat import DEFAULT_ULHPC_CONFIG, _ssh_config


def remote_script():
    return r'''
import concurrent.futures,hashlib,json,sys,urllib.request,urllib.parse,urllib.error
from datetime import datetime,timezone
requests=json.load(sys.stdin)
repository='xinzhou1997/swe_rpg_unified'
accept='application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'
query=urllib.parse.urlencode({'service':'registry.docker.io','scope':'repository:'+repository+':pull'})
with urllib.request.urlopen('https://auth.docker.io/token?'+query,timeout=30) as response:
    token=json.load(response)['token']
def get(kind,digest):
    req=urllib.request.Request('https://registry-1.docker.io/v2/'+repository+'/'+kind+'/'+digest,
                              headers={'Authorization':'Bearer '+token,'Accept':accept})
    with urllib.request.urlopen(req,timeout=30) as response:
        raw=response.read()
        if 'sha256:'+hashlib.sha256(raw).hexdigest()!=digest:
            raise ValueError('OCI content SHA-256 differs from requested digest')
        header=response.headers.get('Docker-Content-Digest')
        if header and header!=digest:
            raise ValueError('OCI digest header differs from requested digest')
        return json.loads(raw),header
def probe(row):
    result={'instance_id':row['instance_id'],'image_ref':row['image_ref'],'expected_digest':row['oci_digest']}
    try:
        manifest,header=get('manifests',row['oci_digest'])
        result.update(root_digest_verified=True,root_digest_header=header,root_media_type=manifest.get('mediaType'))
        selected=row['oci_digest']
        if 'manifests' in manifest:
            choices=[m for m in manifest['manifests'] if m.get('platform',{}).get('os')=='linux'
                     and m.get('platform',{}).get('architecture')=='amd64']
            if len(choices)!=1:
                result.update(status='unsupported_platform',linux_amd64_matches=len(choices));return result
            selected=choices[0]['digest'];manifest,_=get('manifests',selected)
        config,unused=get('blobs',manifest['config']['digest'])
        result.update(selected_manifest_digest=selected,os=config.get('os'),architecture=config.get('architecture'),
                      layers=[{'digest':m['digest'],'bytes':m['size']} for m in manifest['layers']])
        result['compressed_layer_bytes']=sum(m['bytes'] for m in result['layers'])
        result['status']='available_linux_amd64' if (result['os'],result['architecture'])==('linux','amd64') else 'unsupported_platform'
    except urllib.error.HTTPError as exc:
        result.update(status='http_error',http_status=exc.code)
    except (OSError,ValueError,KeyError) as exc:
        result.update(status='probe_error',error_type=type(exc).__name__,error=str(exc)[:300])
    return result
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    records=list(pool.map(probe,requests))
print(json.dumps({'checked_at':datetime.now(timezone.utc).isoformat(),'records':records}))
'''


def summarize(records):
    layers = {}
    for row in records:
        for layer in row.get('layers', []):
            if layer['digest'] in layers and layers[layer['digest']] != layer['bytes']:
                raise ValueError('OCI layer size disagrees for the same digest')
            layers[layer['digest']] = layer['bytes']
    return {'status_counts':dict(Counter(r['status'] for r in records)),
            'sum_per_image_compressed_layer_bytes':sum(r.get('compressed_layer_bytes',0) for r in records),
            'unique_compressed_layer_bytes':sum(layers.values()),'unique_layers':len(layers)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request-manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--ulhpc-config',type=Path,default=DEFAULT_ULHPC_CONFIG)
    args=parser.parse_args()
    raw=args.request_manifest.read_bytes()
    request=json.loads(raw)
    for row in request['requests']:
        if row['image_ref'] != 'xinzhou1997/swe_rpg_unified@'+row['oci_digest']:
            raise ValueError('Unexpected RPG registry or digest')
    target,port,key=_ssh_config(args.ulhpc_config)
    command=['ssh','-p',port]
    if key: command+=['-i',key]
    command += [target,'/usr/bin/python3.11 -c '+shlex.quote(remote_script())]
    raw_report=subprocess.check_output(command,input=json.dumps(request['requests']).encode(),timeout=600)
    report=json.loads(raw_report)
    expected=[r['instance_id'] for r in request['requests']]
    if [r['instance_id'] for r in report['records']] != expected:
        raise ValueError('Registry audit coverage differs from frozen request universe')
    report.update(schema_version=1,purpose='swe_rpg_readonly_registry_metadata_audit',
                  source_request_manifest=str(args.request_manifest),source_request_manifest_sha256=hashlib.sha256(raw).hexdigest(),
                  runtime='Iris login /usr/bin/python3.11',metadata_concurrency=2,
                  image_layers_downloaded=False,sif_pull_verified=False,summary=summarize(report['records']))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.output.exists():
        raise FileExistsError('Refusing to overwrite prior registry evidence')
    temp=args.output.with_suffix('.json.tmp');temp.write_text(json.dumps(report,indent=2)+'\n');temp.replace(args.output)
    print(json.dumps(report['summary']))
    return 0 if all(r['status']=='available_linux_amd64' for r in report['records']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
