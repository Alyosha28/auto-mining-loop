#!/usr/bin/env python3
"""Build the editable V002 memory component-ablation protocol and task files."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'auto_research/config/memory_component_ablation_v002'
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


def step_memory(step: Path, task: str) -> dict:
    record = read(step / 'step_record.json')
    manifest = read(step / 'run_manifest.json')
    active = (record.get('active_branch') or {}).get('edits')
    kept = set(active if active is not None else manifest.get('kept_edits', []))
    edits = record.get('edits')
    if edits is None:
        edits = read(step / 'generation/edits.json')
    return {
        'source_run': record['run_id'], 'task': task, 'status': 'COMPLETED',
        'scope': 'historical development probe; no select outcomes supplied; applicability limited to source harness and seed',
        'probe_metrics': record['adopted_probe']['metrics'],
        'edits': [edit for edit in edits if edit.get('id') in kept],
        'harness': record['adopted_harness'],
    }


def champion_memory(unit_name: str, task: str) -> dict:
    unit = read(RUNS / unit_name / 'result.json')
    loop = ROOT / unit['diagnostic_run']
    state = read(loop / 'loop_state.json')
    run_id = state['champion']['run_id']
    return step_memory(loop / 'decisions' / run_id / 'child/step', task)


def base_harness(source: Path) -> dict:
    harness = read(source)
    harness['protocol']['budget_candidates'] = 12
    harness['protocol']['dev_split'] = 'seeded-year-blocks'
    harness['guardrails']['coverage_stop'] = False
    harness['memory']['experience_dir'] = 'auto_research/agents/runs/MEMORY_COMPONENT_SHARED_EXPERIENCE_V002'
    harness.pop('composition', None)
    return harness


def initial_harness(task: str) -> tuple[dict, list[str]]:
    if task == 'T5':
        harness = base_harness(RUNS / 'R2_CONFIG_001/base_harness.json')
        harness['skills']['crossover_pairs'] = 8
        harness['skills']['mutation_per_seed'] = 1
        return harness, FEATURES
    if task == 'T6':
        harness = base_harness(RUNS / 'R1_CONFIG_IC/base_harness.json')
        harness['probe']['duplication_min_overlap'] = 30
        harness['skills']['crossover_pairs'] = 0
        harness['skills']['mutation_per_seed'] = 2
        return harness, FEATURES
    if task == 'T7':
        harness = base_harness(RUNS / 'R1_CONFIG_IC/base_harness.json')
        harness['probe']['duplication_min_overlap'] = 30
        harness['skills']['crossover_pairs'] = 6
        harness['skills']['mutation_per_seed'] = 1
        return harness, FEATURES
    harness = base_harness(RUNS / 'R2_CONFIG_001/base_harness.json')
    harness['seed_pool'] = [
        {'name': 'momentum_60', 'feature': 'momentum_60', 'transform': 'raw'},
        {'name': 'volatility_60', 'feature': 'volatility_60', 'transform': 'raw'},
        *harness['seed_pool'][2:],
    ]
    harness['probe']['year_split'] = True
    return harness, ['momentum_60', 'volatility_60', *FEATURES[2:]]


def main() -> None:
    questions = {
        'T5': '在高换手候选池上，能否通过改变候选生成机制提高共同样本平均绝对 IC，并保留换手诊断？',
        'T6': '在低多样性候选池上，能否通过改变候选生成机制提高共同样本平均绝对 IC？',
        'T7': '在头部候选表现不足的初始状态下，能否提高候选池共同样本平均绝对 IC？',
        'T8': '在长周期种子池上，能否提高共同样本平均绝对 IC，并保留覆盖与年度稳定性诊断？',
    }
    defects = {
        'T5': '高 turnover_proxy 与槽位集中',
        'T6': '低 novelty 的候选池',
        'T7': '头部候选 top_ic_mean 不足',
        'T8': '长周期种子池的覆盖与年度稳定性',
    }
    diagnostics = {
        'T5': ['turnover_proxy', 'candidate_kind_distribution', 'coverage', 'duplicate_rate', 'novelty'],
        'T6': ['novelty', 'duplicate_rate', 'pass_rate', 'coverage'],
        'T7': ['top_ic_mean', 'candidate_ic_distribution', 'pass_rate', 'duplicate_rate'],
        'T8': ['coverage', 'year_ic_min', 'icir_p25', 'novelty', 'duplicate_rate'],
    }
    task_files = []
    for task in ('T5', 'T6', 'T7', 'T8'):
        harness, features = initial_harness(task)
        harness_path = OUT / 'initial_harness' / f'{task}.json'
        write(harness_path, harness)
        traces = {str(seed): relative(OUT / 'initial_traces_r002' / f'{task}_S{seed}.json') for seed in SEEDS}
        config = {
            'schema': 'AUTO-RESEARCH-MEMORY-COMPONENT-TASK-1',
            'task': task, 'research_question': questions[task], 'initial_defect': defects[task],
            'initial_harness': relative(harness_path), 'initial_safe_trace': traces,
            'editable_surface': 'runtime editable_surface.json; frozen protocol and guardrails excluded',
            'primary_metric': 'mean_abs_ic', 'task_diagnostics': diagnostics[task],
            'task_constraints': {'min_coverage': 0.0, 'computable_mean_abs_ic': True,
                                 'development_end': '2020-11-30'},
            'budget': {'decisions_per_model_arm': 2, 'model_calls_per_arm': 2,
                       'candidates_per_decision': 12, 'input_tokens_per_arm': 120000,
                       'output_tokens_per_arm': 16000, 'output_tokens_per_call': 8000},
            'seed_set': SEEDS,
            'stop_rule': 'execute two decisions per model arm without early stop or adaptive parallel branch',
            'select_freeze_rule': 'one champion per arm evaluated once after all model decisions',
            'shared_memory': relative(OUT / 'memory/shared_research_memory.json'),
            'features': features,
        }
        filename = f'{task}.json'
        write(OUT / filename, config)
        task_files.append(filename)
    memories = [
        step_memory(RUNS / 'R1_DIAG_S11_IC_002', 'cross_task_normalization'),
        step_memory(RUNS / 'R2_TRACE_S11_001', 'cross_task_allocation'),
        champion_memory('DIAGNOSTIC_CONTINUATION_T1_S11_V001', 'resource_allocation_probe'),
        champion_memory('DIAGNOSTIC_CONTINUATION_T2_S43_V001', 'duplicate_roster_probe'),
        champion_memory('DIAGNOSTIC_CONTINUATION_T3_S43_V001', 'pass_rate_probe'),
        champion_memory('DIAGNOSTIC_CONTINUATION_T4_S29_V001', 'stability_probe'),
        {'source_run': 'T3_S43_PARENT_d001', 'task': 'output_budget_failure', 'status': 'FAILED',
         'scope': 'per-call output failure; no candidate execution; retained to make budget failure visible',
         'edits': []},
    ]
    write(OUT / 'memory/shared_research_memory.json',
          {'schema': 'AUTO-RESEARCH-SHARED-MEMORY-1', 'entries': memories})
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
        'schema': 'AUTO-RESEARCH-MEMORY-COMPONENT-PROTOCOL-1',
        'protocol_version': 'V002', 'date_frozen': '2026-10-30', 'model_id': MODEL,
        'generation': {'effort': 'low', 'temperature': 'cli-default', 'tools': 'disabled',
                       'session_persistence': False, 'cli_model_override': None},
        'proposer': relative(SCRIPTS / 'claude_proposer_command.py'),
        'proposer_environment': {'AUTO_RESEARCH_CLAUDE_MAX_OUTPUT_TOKENS': '8000',
                                 'AUTO_RESEARCH_CLAUDE_MAX_USD': '0.50'},
        'proposer_timeout_seconds': 600, 'execution_timeout_seconds': 600,
        'run_root': 'auto_research/agents/runs', 'task_files': task_files, 'seeds': SEEDS,
        'shared_decisions': 2, 'continuation_decisions': 2, 'candidate_budget': 12,
        'dev_split': 'seeded-year-blocks',
        'shared_contract': relative(OUT / 'shared_contract.json'),
        'continuation_contract': relative(OUT / 'continuation_contract.json'),
        'coverage_log_only': True, 'demo': False, 'max_parallel_units': 2,
        'screen_run_prefix': 'MEMORY_COMPONENT_TASK_SCREEN_R002',
        'screen_summary_file': 'initial_defects_r002.json',
        'primary_estimand': 'median of 12 paired memory-minus-scalar select mean_abs_ic deltas',
        'panel_support': {'minimum_positive_task_seed_medians': 3,
                          'all_leave_one_task_out_medians_positive': True,
                          'overall_median_positive': True},
        'score_window_read': False, 'independent_real_data_oos': False,
    }
    write(OUT / 'protocol.json', protocol)
    print(relative(OUT / 'protocol.json'))


if __name__ == '__main__':
    main()
