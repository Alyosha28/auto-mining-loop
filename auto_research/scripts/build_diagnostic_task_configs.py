#!/usr/bin/env python3
"""Build the editable V001 diagnostic-continuation task and protocol files."""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'auto_research/config/diagnostic_continuation_v1'
RUNS = ROOT / 'auto_research/agents/runs'
SCRIPTS = ROOT / 'skills/current/factor-evolution-harness2/scripts'
MODEL = 'kimi/k3.1-pro[1m]'
SEEDS = [11, 29, 43]
FEATURES = ['momentum_20', 'volatility_20', 'turnover_mean_20',
            'reversal_5', 'amihud_20', 'volume_ratio_5_20']


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def memory_entry(run_id: str, task: str, status: str, include_rejected: bool = False) -> dict:
    run = RUNS / run_id
    entry = {'source_run': run_id, 'task': task, 'status': status,
             'scope': 'historical development probe; not independent OOS; scope limited to the source harness and seed'}
    result_path = run / 'result.json'
    if result_path.is_file():
        result = read(result_path)
        if isinstance(result.get('probe'), dict):
            entry['probe_metrics'] = result['probe']['adopted']
    record_path = run / 'step_record.json'
    if record_path.is_file():
        record = read(record_path)
        kept = set(read(run / 'run_manifest.json').get('kept_edits', []))
        edits = record.get('edits', [])
        entry['edits'] = [edit for edit in edits if not kept or edit.get('id') in kept]
    if include_rejected:
        rejected = list(run.glob('decisions/*/rejected_proposal.json'))
        if rejected:
            entry['edits'] = read(rejected[0])
    return entry


def harness_for(task: str) -> dict:
    if task == 'T1':
        harness = read(RUNS / 'R2_CONFIG_001/base_harness.json')
    elif task == 'T2':
        harness = read(RUNS / 'R3_CONFIG_001/base_harness.json')
        harness.pop('composition', None)
    elif task == 'T3':
        harness = read(RUNS / 'R1_CONFIG_IC/base_harness.json')
        harness['protocol']['dev_split'] = 'seeded-year-blocks'
        harness['probe']['duplication_min_overlap'] = 30
        harness['skills']['crossover_pairs'] = 4
        harness['skills']['mutation_per_seed'] = 2
    else:
        harness = read(RUNS / 'R2_CONFIG_001/frozen_seed11_harness.json')
        harness['skills']['mutation_per_seed'] = 2
        harness['probe']['year_split'] = True
    harness['protocol']['budget_candidates'] = 12
    harness['protocol']['dev_split'] = 'seeded-year-blocks'
    harness['guardrails']['coverage_stop'] = False
    harness['memory']['experience_dir'] = 'auto_research/agents/runs/DIAGNOSTIC_CONTINUATION_SHARED_EXPERIENCE_V001'
    harness.pop('composition', None)
    return harness


def main() -> None:
    questions = {
        'T1': '在固定候选预算下，重新分配变异、交叉与原始候选位置，能否提高共同样本候选池平均绝对 IC？',
        'T2': '在重复候选占比较高的初始状态下，能否通过改变候选生成机制提高共同样本候选池平均绝对 IC，并保留重复率诊断？',
        'T3': '在合格候选不足的初始状态下，能否通过改变候选生成机制提高共同样本候选池平均绝对 IC？',
        'T4': '在年度稳定性较弱的初始状态下，能否提高共同样本候选池平均绝对 IC，并单独报告年度与日期块稳定性？',
    }
    defects = {
        'T1': 'crossover 或 mutation 槽位分配低效',
        'T2': 'roster duplicate_rate 较高',
        'T3': 'roster pass_rate 不足',
        'T4': '年度 IC 或 icir_p25 稳定性不足',
    }
    diagnostics = {
        'T1': ['candidate_kind_distribution', 'pass_rate', 'novelty', 'duplicate_rate',
               'turnover_proxy', 'coverage'],
        'T2': ['duplicate_rate', 'duplicate_pairs', 'novelty', 'coverage', 'pass_rate', 'turnover_proxy'],
        'T3': ['pass_rate', 'candidate_ic', 'candidate_icir', 'coverage', 'novelty',
               'duplicate_rate', 'turnover_proxy'],
        'T4': ['icir_p25', 'year_ic', 'date_block_delta', 'coverage', 'novelty',
               'duplicate_rate', 'turnover_proxy'],
    }
    memories = {
        'T1': [memory_entry('R1_DIAG_S11_IC_002', 'cross_task_normalization', 'COMPLETED'),
               memory_entry('R4_LIVE_LOCAL_KIMI_S11_004', 'failed_resource_allocation', 'INVALID', True)],
        'T2': [memory_entry('R2_TRACE_S11_001', 'cross_task_allocation', 'COMPLETED'),
               memory_entry('R3_TRACE_S43_002', 'failed_composition', 'NULL')],
        'T3': [memory_entry('R1_DIAG_S11_IC_002', 'cross_task_normalization', 'COMPLETED'),
               memory_entry('R2_TRACE_S11_001', 'cross_task_allocation', 'COMPLETED')],
        'T4': [memory_entry('R2_TRACE_S11_001', 'cross_task_allocation', 'COMPLETED'),
               memory_entry('R3_TRACE_S43_002', 'failed_stability_composition', 'NULL')],
    }
    task_files = []
    for task in ('T1', 'T2', 'T3', 'T4'):
        harness_path = OUT / 'initial_harness' / f'{task}.json'
        memory_path = OUT / 'memory' / f'{task}.json'
        write(harness_path, harness_for(task))
        write(memory_path, {'schema': 'AUTO-RESEARCH-SHARED-MEMORY-1', 'entries': memories[task]})
        traces = {str(seed): relative(OUT / 'initial_traces' / f'{task}_S{seed}.json') for seed in SEEDS}
        config = {
            'schema': 'AUTO-RESEARCH-DIAGNOSTIC-TASK-1',
            'task': task,
            'research_question': questions[task],
            'initial_defect': defects[task],
            'initial_harness': relative(harness_path),
            'initial_safe_trace': traces,
            'editable_surface': 'runtime editable_surface.json; frozen protocol and guardrails excluded',
            'primary_metric': 'mean_abs_ic',
            'task_diagnostics': diagnostics[task],
            'task_constraints': {'min_coverage': 0.0, 'computable_mean_abs_ic': True,
                                 'development_end': '2020-11-30'},
            'budget': {'shared_decisions': 2, 'continuation_decisions_per_arm': 2,
                       'candidates_per_decision': 12, 'model_calls_per_stage': 2,
                       'input_tokens_per_stage': 120000, 'output_tokens_per_stage': 16000,
                       'output_tokens_per_call': 8000, 'bounded_repairs_per_unit': 1},
            'seed_set': SEEDS,
            'stop_rule': 'execute the frozen decision count; no early stop or adaptive parallel branch',
            'select_freeze_rule': 'evaluate one champion per arm once after all continuation decisions',
            'shared_memory': relative(memory_path),
            'features': FEATURES,
            'defect_confirmation': relative(RUNS / f'DIAGNOSTIC_CONTINUATION_TASK_SCREEN_V001_S{SEEDS[0]}' / 'result.json'),
        }
        filename = f'{task}.json'
        write(OUT / filename, config)
        task_files.append(filename)
    contract = {
        'schema': 'AUTO-RESEARCH-LOOP-CONTRACT-1',
        'budget': {'max_decisions': 2, 'max_candidate_budget': 24, 'max_model_calls': 2,
                   'max_input_tokens': 120000, 'max_output_tokens': 16000,
                   'max_no_improvement': 2, 'max_debug_depth': 1},
        'primary': {'metric': 'mean_abs_ic', 'min_delta': 0.0},
        'guards': {'min_delta_coverage': -1.0, 'min_delta_pass_rate': -1.0,
                   'max_delta_turnover_proxy': 1.0, 'min_delta_novelty': -1.0,
                   'max_delta_duplicate_rate': 1.0},
        'online_evaluation': 'probe', 'final_evaluation': 'select',
    }
    write(OUT / 'shared_contract.json', contract)
    write(OUT / 'continuation_contract.json', contract)
    protocol = {
        'schema': 'AUTO-RESEARCH-DIAGNOSTIC-CONTINUATION-PROTOCOL-1',
        'protocol_version': 'V001',
        'date_frozen': '2026-10-30',
        'model_id': MODEL,
        'generation': {'effort': 'low', 'temperature': 'cli-default', 'tools': 'disabled',
                       'session_persistence': False, 'cli_model_override': None},
        'proposer': relative(SCRIPTS / 'claude_proposer_command.py'),
        'proposer_environment': {'AUTO_RESEARCH_CLAUDE_MAX_OUTPUT_TOKENS': '8000',
                                 'AUTO_RESEARCH_CLAUDE_MAX_USD': '0.50'},
        'proposer_timeout_seconds': 600,
        'execution_timeout_seconds': 600,
        'run_root': 'auto_research/agents/runs',
        'task_files': task_files,
        'seeds': SEEDS,
        'shared_decisions': 2,
        'continuation_decisions': 2,
        'candidate_budget': 12,
        'dev_split': 'seeded-year-blocks',
        'shared_contract': relative(OUT / 'shared_contract.json'),
        'continuation_contract': relative(OUT / 'continuation_contract.json'),
        'coverage_log_only': True,
        'demo': False,
        'panel_estimand': 'median of 12 paired diagnostic-minus-parent select mean_abs_ic deltas',
        'panel_support': {'minimum_positive_task_seed_medians': 3,
                          'all_leave_one_task_out_medians_positive': True,
                          'overall_median_positive': True},
        'score_window_read': False,
        'independent_real_data_oos': False,
    }
    write(OUT / 'protocol.json', protocol)
    print(relative(OUT / 'protocol.json'))


if __name__ == '__main__':
    main()
