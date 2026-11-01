#!/usr/bin/env python3
"""Evaluate multiple frozen harnesses on one common development sample."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evo_harness import harness_hash
from evo_probe import (CoverageStop, GuardrailError, check_guardrails, load_panel,
                       probe_rounds, split_dates, split_dev_dates, synthetic_panel)
from research_contract import canonical_hash, evaluate_adoption, load_contract
from run_evo_step import DEFAULT_PANEL, _validate_seed_features

RESULT_SCHEMA = 'AUTO-RESEARCH-COMMON-COMPARISON-1'


def write_json_new(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')


def parse_arm(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition('=')
    if not separator or not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or not path:
        raise GuardrailError('arms must use NAME=HARNESS_PATH with a simple unique name')
    return name, Path(path)


def load_arms(values: list[str], args: argparse.Namespace) -> tuple[list[str], list[dict], list[Path]]:
    names, harnesses, paths = [], [], []
    for value in values:
        name, path = parse_arm(value)
        if name in names:
            raise GuardrailError(f'duplicate arm name: {name}')
        harness = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(harness, dict) or harness.get('schema') != 'EVO-HARNESS-1':
            raise GuardrailError(f'{name} must be an EVO-HARNESS-1 JSON file')
        protocol = harness.get('protocol')
        if not isinstance(protocol, dict):
            raise GuardrailError(f'{name} has no protocol object')
        start, end = protocol.get('dev_start'), protocol.get('dev_end')
        if not isinstance(start, str) or not isinstance(end, str) or not (
                '2017-01-01' <= start <= end <= '2020-11-30'):
            raise GuardrailError(f'{name} has an invalid development window')
        budget = int(protocol.get('budget_candidates', 0))
        if budget < 1:
            raise GuardrailError(f'{name} has an invalid candidate budget')
        if args.budget and budget != args.budget:
            raise GuardrailError(f'{name} budget differs from --budget')
        split = protocol.get('dev_split', 'legacy')
        if split not in ('legacy', 'seeded-year-blocks'):
            raise GuardrailError(f'{name} has an unsupported dev split')
        if args.dev_split and split != args.dev_split:
            raise GuardrailError(f'{name} dev split differs from --dev-split')
        names.append(name)
        harnesses.append(harness)
        paths.append(path.absolute())
    if len(names) < 2:
        raise GuardrailError('common comparison needs at least two frozen harnesses')
    identity = {(harness['protocol']['dev_start'], harness['protocol']['dev_end'],
                 int(harness['protocol']['budget_candidates']),
                 harness['protocol'].get('dev_split', 'legacy')) for harness in harnesses}
    if len(identity) != 1:
        raise GuardrailError('all frozen harnesses must share dev window, budget and dev split')
    return names, harnesses, paths


def load_comparison_panel(args: argparse.Namespace, harnesses: list[dict], output: Path):
    protocol = harnesses[0]['protocol']
    start, end = protocol['dev_start'], protocol['dev_end']
    if args.demo:
        import pandas as pd
        panel = synthetic_panel(seed=args.seed)
        return panel.loc[(panel['date'] >= pd.Timestamp(start))
                         & (panel['date'] <= pd.Timestamp(end))].copy(), 'synthetic'
    seed_panel = args.seed_panel.absolute() if args.seed_panel else DEFAULT_PANEL
    harness_features = {item['feature'] for harness in harnesses
                        for item in harness.get('seed_pool', [])}
    if args.features:
        features = [value.strip() for value in args.features.split(',') if value.strip()]
        missing = sorted(harness_features - set(features))
        if missing:
            raise GuardrailError(f'--features omits harness seed features: {missing}')
    else:
        features = sorted(harness_features)
    _validate_seed_features(features)
    if len(features) < 2:
        raise GuardrailError('common comparison needs at least two seed features')
    base_columns = ['research_mask', 'forward_return', 'log_market_cap']
    if seed_panel == DEFAULT_PANEL:
        panel = load_panel(DEFAULT_PANEL, base_columns + features, start, end,
                           cache_path=output / 'cache/dev_comparison_panel.parquet')
    else:
        base = load_panel(DEFAULT_PANEL, base_columns, start, end)
        feature_frame = load_panel(seed_panel, features, start, end)
        panel = base.merge(feature_frame, on=['date', 'symbol'], how='left')
        (output / 'cache').mkdir(parents=True, exist_ok=True)
        panel.to_parquet(output / 'cache/dev_comparison_panel.parquet', index=False)
    source = f'{DEFAULT_PANEL.relative_to(ROOT)} + seeds from {seed_panel.relative_to(ROOT)}'
    return panel, source


def run(args: argparse.Namespace) -> int:
    started = datetime.now(timezone.utc)
    output = args.output.absolute()
    if output.exists() and any(output.iterdir()):
        raise GuardrailError('comparison output must be a new or empty directory')
    output.mkdir(parents=True, exist_ok=True)
    contract = load_contract(args.contract)
    write_json_new(output / 'contract.json', contract)
    names, original_harnesses, paths = load_arms(args.arm, args)
    harnesses = json.loads(json.dumps(original_harnesses))
    for harness in harnesses:
        harness.setdefault('guardrails', {})['coverage_stop'] = not args.coverage_log_only
    panel, data_source = load_comparison_panel(args, original_harnesses, output)
    protocol = harnesses[0]['protocol']
    split_mode = protocol.get('dev_split', 'legacy')
    if split_mode == 'seeded-year-blocks':
        fit_dates, probe_dates, select_dates = split_dev_dates(panel, args.seed)
    else:
        probe_dates, select_dates = split_dates(panel, float(protocol.get('probe_split', 0.7)))
        fit_dates = probe_dates
    split_record = {'mode': split_mode, 'seed': args.seed if split_mode == 'seeded-year-blocks' else None,
                    'fit': [str(value.date()) for value in fit_dates],
                    'probe': [str(value.date()) for value in probe_dates],
                    'select': [str(value.date()) for value in select_dates]}
    write_json_new(output / 'dev_split.json', split_record)
    probe_results = probe_rounds(harnesses, panel, fit_dates, probe_dates, seed=args.seed,
                                 include_daily=True)
    select_results = [] if args.probe_only else probe_rounds(
        harnesses, panel, fit_dates, select_dates, seed=args.seed, include_daily=True)
    for harness, probe in zip(harnesses, probe_results):
        check_guardrails(harness, probe)
    if not args.probe_only:
        for harness, select in zip(harnesses, select_results):
            check_guardrails(harness, select)
    split_results = [('probe', probe_results)] + ([] if args.probe_only else [('select', select_results)])
    for split, results in split_results:
        hashes = {result['metrics'].get('common_support_sha256') for result in results}
        fit_hashes = {result['metrics'].get('fit_common_support_sha256') for result in results}
        if len(hashes) != 1 or len(fit_hashes) != 1 or None in hashes or None in fit_hashes:
            raise GuardrailError(f'{split} common-support hashes differ across frozen harnesses')
    arms = {}
    for index, (name, harness, path, probe) in enumerate(
            zip(names, original_harnesses, paths, probe_results)):
        write_json_new(output / f'probe/{name}.json', probe)
        arm = {'harness': str(path), 'harness_sha256': harness_hash(harness),
               'probe_metrics': probe['metrics']}
        if not args.probe_only:
            select = select_results[index]
            write_json_new(output / f'select/{name}.json', select)
            arm['select_metrics'] = select['metrics']
        arms[name] = arm
    reference = names[0]
    comparisons = {}
    for name in names[1:]:
        comparison = {'probe': evaluate_adoption(
            contract, arms[reference]['probe_metrics'], arms[name]['probe_metrics'])}
        if not args.probe_only:
            comparison['select'] = evaluate_adoption(
                contract, arms[reference]['select_metrics'], arms[name]['select_metrics'])
        comparisons[name] = comparison
    evaluated_results = [*probe_results, *select_results]
    max_date = max(str(result['metrics']['max_date_read']) for result in evaluated_results)
    if max_date > protocol['dev_end'] or any(
            result['metrics'].get('score_window_reads', 0) for result in evaluated_results):
        raise GuardrailError('comparison read outside the authorized development window')
    result = {
        'schema': RESULT_SCHEMA, 'status': 'COMPLETED', 'reference': reference,
        'arms': arms, 'comparisons': comparisons,
        'common_support_verified': True, 'selection_performed': False,
        'primary_evaluation': 'probe' if args.probe_only else 'select', 'dev_window': [protocol['dev_start'], protocol['dev_end']],
        'dev_split': split_record, 'max_date_read': max_date,
        'score_window_read': False, 'independent_real_data_oos': False,
        'finished_at': datetime.now(timezone.utc).isoformat(),
    }
    write_json_new(output / 'result.json', result)
    manifest = {
        'schema': 'AUTO-RESEARCH-COMMON-COMPARISON-MANIFEST-1', 'status': 'COMPLETED',
        'command': ' '.join([Path(sys.executable).name, Path(__file__).name] + sys.argv[1:]),
        'contract_sha256': canonical_hash(contract), 'data_source': data_source,
        'seed': args.seed, 'demo': args.demo, 'coverage_log_only': args.coverage_log_only,
        'probe_only': args.probe_only, 'arm_count': len(names), 'score_window_read': False,
        'started_at': started.isoformat(), 'finished_at': result['finished_at'],
    }
    write_json_new(output / 'run_manifest.json', manifest)
    evaluation = 'probe' if args.probe_only else 'select'
    print(json.dumps({'status': 'COMPLETED', 'reference': reference,
                      'comparisons': {name: {f'{evaluation}_supported': value[evaluation]['supported'],
                                             f'{evaluation}_primary_delta': value[evaluation]['primary_delta']}
                                      for name, value in comparisons.items()},
                      'output': str(output)}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arm', action='append', required=True,
                        help='frozen harness as NAME=PATH; repeat for every arm')
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=11)
    parser.add_argument('--budget', type=int, default=0)
    parser.add_argument('--dev-split', choices=['legacy', 'seeded-year-blocks'])
    parser.add_argument('--seed-panel', type=Path)
    parser.add_argument('--features')
    parser.add_argument('--coverage-log-only', action='store_true')
    parser.add_argument('--probe-only', action='store_true',
                        help='exclude select evaluation from online proposer optimization')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    try:
        return run(args)
    except (CoverageStop, GuardrailError, ValueError, FileNotFoundError, FileExistsError) as error:
        print(f'STOP: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
