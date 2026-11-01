#!/usr/bin/env python3
"""Write lightweight result/trace artifacts without exposing held-out metrics to a proposer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
METRICS = ('pass_count', 'pass_rate', 'mean_abs_ic', 'top_ic_mean', 'icir_p25',
           'coverage', 'turnover_proxy', 'duplicate_rate', 'novelty')


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(text)


def deltas(reference: dict, candidate: dict) -> dict:
    return {key: candidate.get(key, 0.0) - reference.get(key, 0.0)
            for key in METRICS if isinstance(candidate.get(key), (int, float))
            and isinstance(reference.get(key), (int, float))}


def summarize(run_dir: Path, export_trace: bool = False, export_only: bool = False) -> dict:
    record = read_json(run_dir / 'step_record.json')
    manifest = read_json(run_dir / 'run_manifest.json')
    if manifest.get('status') != 'COMPLETED':
        raise ValueError(f'run is not completed: {run_dir}')
    if export_only and not export_trace:
        raise ValueError('export-only requires --export-trace')
    config = {}
    preregistration = manifest.get('preregistration') or {}
    if preregistration.get('path') and (ROOT / preregistration['path']).is_file():
        config = read_json(ROOT / preregistration['path'])
    evaluation = config.get('primary_evaluation', 'probe')
    if evaluation not in ('probe', 'select'):
        raise ValueError(f'unsupported primary evaluation: {evaluation}')
    final_reference_record = record.get('adopted_reference', record['reference'])
    reference = final_reference_record[evaluation]['metrics']
    adopted = record[f'adopted_{evaluation}']['metrics']
    proposal_reference = record['reference']['probe']['metrics']
    reference_names = [item['name'] for item in record['reference']['probe']['candidates']]
    decisions = {item['edit']: item for item in record['composition']['decisions']}
    edits = record.get('edits')
    if edits is None and (run_dir / 'generation/edits.json').is_file():
        edits = read_json(run_dir / 'generation/edits.json')
    edits_by_id = {item['id']: item for item in edits or []}
    proposals, trace_rows = [], []
    for probe in record['candidates']:
        edit = edits_by_id.get(probe.get('edit'))
        decision = decisions[probe['edit']]
        names = [item['name'] for item in probe['candidates']]
        proposal = {
            'edit': edit or {'id': probe['edit'], 'component': decision['component'],
                             'intent': decision['intent']},
            'probe_metrics': probe['metrics'],
            'delta': decision['delta'],
            'support_reason': decision.get('support_reason'),
            'verdict': decision['verdict'],
            'roster_effect': 'unchanged' if names == reference_names else 'changed',
            'candidate_roster': [{key: item.get(key) for key in
                                  ('name', 'kind', 'operator', 'parents', 'mean_ic', 'icir', 'n_dates',
                                   'fit_pair_correlation', 'fit_pair_strength')}
                                 for item in probe['candidates']],
        }
        proposals.append(proposal)
        trace_rows.append({'stage': 'PROBE', 'edit': probe['edit'], 'delta': decision['delta'],
                           'verdict': decision['verdict'], 'support_reason': decision.get('support_reason'),
                           'roster_effect': proposal['roster_effect'],
                           'runtime_seconds': probe['metrics'].get('runtime_seconds')})
    primary = {'evaluation': evaluation, 'metric': 'mean_abs_ic',
               'reference': reference['mean_abs_ic'], 'adopted': adopted['mean_abs_ic'],
               'delta': adopted['mean_abs_ic'] - reference['mean_abs_ic']}
    max_date = max(str(record[key]['metrics']['max_date_read'])
                   for key in (('adopted_probe',) if record.get('probe_only')
                               else ('adopted_probe', 'adopted_select')))
    result = {
        'schema': 'AUTO-RESEARCH-STEP-RESULT-1',
        'run_id': record['run_id'],
        'status': manifest['status'],
        'proposer': record['proposer'],
        'prompt_feedback': record.get('prompt_feedback'),
        'model_id': record.get('model_id'),
        'seed': record['seed'],
        'seed_semantics': record.get('seed_semantics'),
        'base_harness_hash': manifest['base_harness_hash'],
        'adopted_harness_hash': manifest['adopted_harness_hash'],
        'proposal_order': record['proposal_order'],
        'kept_edits': manifest['kept_edits'],
        'dropped_edits': [{'edit': item['edit'], 'support_reason': item.get('support_reason')}
                          for item in record['composition']['dropped']],
        'primary': primary,
        'probe': {'reference': record['reference']['probe']['metrics'],
                  'adopted': record['adopted_probe']['metrics']},
        'select': (None if record.get('probe_only') else
                   {'reference': record['reference']['select']['metrics'],
                    'adopted': record['adopted_select']['metrics']}),
        'dev_window': manifest['dev_window'],
        'max_date_read': max_date,
        'score_window_read': False,
        'independent_real_data_oos': False,
        'runtime_seconds': manifest.get('runtime_seconds'),
        'recovery_command': manifest['command'],
        'reflection_status': manifest.get('reflection_status'),
    }
    if record.get('probe_only'):
        result['select_evaluated'] = False
    if max_date > manifest['dev_window'][1]:
        raise ValueError('result contains dates beyond the authorized development window')
    trace_rows.append({'stage': 'DONE', 'status': 'COMPLETED', 'primary': primary,
                       'kept_edits': manifest['kept_edits']})
    result_path = run_dir / 'result.json'
    trace_path = run_dir / 'trace.jsonl'
    export_path = run_dir / 'generation' / (
        'next_execution_trace.json' if record.get('trace_file') else 'execution_trace.json')
    if export_trace and export_path.exists():
        raise FileExistsError(f'trace export already exists; use a new run instead of overwriting: {export_path}')
    if export_only:
        if read_json(result_path) != result:
            raise ValueError('existing result differs from the completed step; refusing to overwrite it')
        if not trace_path.is_file():
            raise FileNotFoundError(f'missing existing trace: {trace_path}')
    else:
        write_new(result_path, json.dumps(result, ensure_ascii=False, indent=1) + '\n')
        write_new(trace_path,
                  ''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in trace_rows))
    if export_trace:
        exported = {
            'schema': 'AUTO-RESEARCH-PROPOSER-TRACE-1',
            'source_run': record['run_id'],
            'scope': 'Prior proposal and probe evidence only; held-out outcomes are withheld.',
            'seed_semantics': record.get('seed_semantics'),
            'search_policy': {key: record['base_harness'].get(key)
                              for key in ('operators', 'seed_pool', 'skills', 'probe', 'protocol')},
            'reference_probe_metrics': proposal_reference,
            'adopted_probe_metrics': record['adopted_probe']['metrics'],
            'proposals': proposals,
            'kept_edits': manifest['kept_edits'],
            'next_question': 'Which diagnosed harness mechanism should change next, and what outcome would falsify it?',
        }
        write_new(export_path, json.dumps(exported, ensure_ascii=False, indent=1) + '\n')
        result['trace_export'] = str(export_path.relative_to(run_dir))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--export-trace', action='store_true')
    parser.add_argument('--export-only', action='store_true',
                        help='verify existing result/trace and only export the next proposer trace')
    args = parser.parse_args()
    result = summarize(args.run_dir, export_trace=args.export_trace, export_only=args.export_only)
    print(json.dumps({'run_id': result['run_id'], 'status': result['status'],
                      'primary': result['primary']}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
