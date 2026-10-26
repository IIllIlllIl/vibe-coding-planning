"""Bind accepted permission-only build reports without changing RPG source rows.

Original image authority, raw reports and split membership remain independently
hash-pinned. This is preparation glue, not a new execution or scoring path.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from src.swe_rpg_pce.dataset import runtime_image_ref, prepare_subset
from src.swe_verified_pce.dataset import file_sha256
from src.optimization.safe_pce_preparation import _write_once


def freeze(*, image_manifest, repair_plan, report_root, output):
    original = json.loads(image_manifest.read_text())
    plan = json.loads(repair_plan.read_text())
    if (plan.get('policy') != 'permission_only_readability_v1'
            or file_sha256(image_manifest) != plan['source_image_manifest_sha256']):
        raise ValueError('repair plan differs from frozen image authority')
    by_id = {v['instance_id']: (ref, v) for ref, v in original['records'].items()}
    if len({r['instance_id'] for r in plan['records']}) != len(plan['records']):
        raise ValueError('duplicate repair case')
    result = deepcopy(original)
    for item in plan['records']:
        ref, parent = by_id[item['instance_id']]
        path = report_root / item['instance_id'] / 'sif-build.json'
        report = json.loads(path.read_text())
        audit = report.get('permission_audit', {})
        if (parent['sif_sha256'] != item['base_sha256'] or parent['sif_path'] != item['base_sif']
                or report.get('base_sif_sha256') != parent['sif_sha256']
                or report.get('image') != item['derived_ref'] or report.get('rg_archive_sha256') is not None
                or report.get('definition_sha256') is not None
                or audit.get('allowlisted_paths') != item['permission_paths']):
            raise ValueError('permission repair report/parent/allowlist identity differs')
        newer = result['records'][ref]
        newer.update(runtime_ref=report['image'], sif_path=report['sif_path'],
            sif_sha256=report['sif_sha256'], sif_bytes=report['sif_bytes'],
            permission_derivative=dict(policy=plan['policy'], source_ref=ref,
                source_sif_sha256=parent['sif_sha256'], build_report_sha256=file_sha256(path),
                build_report_path=str(Path(plan['operation']) / item['instance_id'] / 'sif-build.json'),
                permission_audit=audit))
        runtime_image_ref(newer, ref)
    result.update(purpose='original_rpg_images_with_audited_permission_only_runtime_derivatives',
        source_image_manifest_sha256=file_sha256(image_manifest), repair_plan_sha256=file_sha256(repair_plan))
    # The parent acceptance certifies original image/base checks, not this new
    # runtime identity or its as-yet independent evaluator/startup acceptance.
    result['source_acceptance'] = {k: result.pop(k) for k in
        ('manifest_id', 'acceptance_report', 'acceptance_report_sha256', 'created_at') if k in result}
    _write_once(output, (json.dumps(result, sort_keys=True, indent=2) + '\n').encode())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('image-manifest', 'repair-plan', 'report-root', 'output', 'snapshot', 'selection', 'destination'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    freeze(image_manifest=args.image_manifest, repair_plan=args.repair_plan,
           report_root=args.report_root, output=args.output)
    selected = json.loads(args.selection.read_text())['selected_instance_ids']
    prepare_subset(snapshot=args.snapshot, image_manifest=args.image_manifest, instance_ids=selected,
                   destination=args.destination, runtime_image_manifest=args.output)
    print('Prepared audited runtime bindings; no model or HPC job started.')


if __name__ == '__main__':
    main()
