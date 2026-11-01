#!/usr/bin/env python3
"""Build the frozen V003 paper-readiness protocol, tasks and cumulative memory."""
from __future__ import annotations

import json
from pathlib import Path

from build_memory_component_configs import champion_memory

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'auto_research/config/paper_readiness_v003'
RUNS = ROOT / 'auto_research/agents/runs'
ALBA = Path('auto_research/agents')
MODEL = 'kimi/k3.1-pro[1m]'
SEEDS = [11, 29]
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


def base_harness(source: Path) -> dict:
    harness = read(source)
    harness['protocol']['budget_candidates'] = 12
    harness['protocol']['dev_split'] = 'seeded-year-blocks'
    harness['guardrails']['coverage_stop'] = False
    harness['memory']['experience_dir'] = str(ALBA / 'experience')
    harness.pop('composition', None)
    return harness


def seeds(names: list[str]) -> list[dict]:
    return [{'name': name, 'feature': name, 'transform': 'raw'} for name in names]


def initial_harness(task: str) -> tuple[dict, list[str]]:
    if task in ('T9', 'T11', 'T12'):
        harness = base_harness(RUNS / 'R2_CONFIG_001/base_harness.json')
    else:
        harness = base_harness(RUNS / 'R1_CONFIG_IC/base_harness.json')
        harness['probe']['duplication_min_overlap'] = 30
    if task == 'T9':
        harness['skills'].update({'crossover_pairs': 4, 'mutation_per_seed': 3})
        return harness, FEATURES
    if task == 'T10':
        harness['operators'] = ['residual_size', 'delta4']
        harness['skills'].update({'crossover_pairs': 0, 'mutation_per_seed': 2})
        return harness, FEATURES
    if task == 'T11':
        harness['operators'] = ['rank', 'delta4', 'delta13', 'neg']
        harness['skills'].update({'crossover_pairs': 8, 'mutation_per_seed': 1})
        return harness, FEATURES
    if task == 'T12':
        names = ['momentum_60', 'volatility_60', 'momentum_20', 'reversal_5',
                 'amihud_20', 'volume_ratio_5_20']
        harness['seed_pool'] = seeds(names)
        harness['probe']['year_split'] = True
        harness['skills'].update({'crossover_pairs': 2, 'mutation_per_seed': 2})
        return harness, names
    if task == 'T13':
        names = ['turnover_mean_20', 'volume_ratio_5_20', 'amihud_20',
                 'reversal_5', 'momentum_20', 'volatility_20']
        harness['seed_pool'] = seeds(names)
        harness['skills'].update({'crossover_pairs': 6, 'mutation_per_seed': 1})
        return harness, names
    names = ['momentum_20', 'volatility_20', 'reversal_5', 'amihud_20']
    harness['seed_pool'] = seeds(names)
    harness['skills'].update({'crossover_pairs': 0, 'mutation_per_seed': 2})
    harness['probe']['year_split'] = True
    return harness, names


def main() -> None:
    questions = {
        'T9': '在高变异深度与四个组合槽的初始配置上，能否提高共同样本平均绝对 IC？',
        'T10': '在有限算子与零组合槽的初始配置上，能否提高共同样本平均绝对 IC？',
        'T11': '在无残差化且八个组合槽的初始配置上，能否提高共同样本平均绝对 IC？',
        'T12': '在长短周期混合种子池上，能否提高共同样本平均绝对 IC 并保留年度诊断？',
        'T13': '在流动性优先种子池与六个组合槽上，能否提高共同样本平均绝对 IC？',
        'T14': '在四特征小种子池上，能否提高共同样本平均绝对 IC 并保留稳定性诊断？',
    }
    diagnostics = {
        'T9': ['pass_rate', 'duplicate_rate', 'novelty', 'candidate_kind_distribution'],
        'T10': ['novelty', 'duplicate_rate', 'coverage', 'top_ic_mean'],
        'T11': ['turnover_proxy', 'pass_rate', 'candidate_kind_distribution', 'novelty'],
        'T12': ['coverage', 'year_ic_min', 'icir_p25', 'turnover_proxy'],
        'T13': ['turnover_proxy', 'coverage', 'duplicate_rate', 'top_ic_mean'],
        'T14': ['novelty', 'duplicate_rate', 'year_ic_min', 'icir_p25'],
    }
    task_files = []
    for task in ('T9', 'T10', 'T11', 'T12', 'T13', 'T14'):
        harness, features = initial_harness(task)
        harness_path = OUT / 'initial_harness' / f'{task}.json'
        write(harness_path, harness)
        traces = {str(seed): relative(OUT / 'initial_traces' / f'{task}_S{seed}.json') for seed in SEEDS}
        config = {
            'schema': 'AUTO-RESEARCH-PAPER-READINESS-TASK-1',
            'task': task, 'research_question': questions[task],
            'initial_harness': relative(harness_path), 'initial_safe_trace': traces,
            'primary_metric': 'mean_abs_ic', 'task_diagnostics': diagnostics[task],
            'task_constraints': {'min_coverage': 0.0, 'computable_mean_abs_ic': True,
                                 'development_end': '2020-11-30'},
            'seed_set': SEEDS, 'model_replicates': ['A', 'B'],
            'shared_memory': relative(OUT / 'memory/shared_research_memory.json'),
            'features': features,
        }
        filename = f'{task}.json'
        write(OUT / filename, config)
        task_files.append(filename)
    memory = read(ROOT / 'auto_research/config/memory_component_ablation_v002/memory/shared_research_memory.json')
    for unit_name, task in (
            ('MEMORY_COMPONENT_T5_S11_V002', 'high_turnover_roster'),
            ('MEMORY_COMPONENT_T6_S43_V002', 'low_diversity_roster'),
            ('MEMORY_COMPONENT_T7_S29_V002', 'head_candidate_roster'),
            ('MEMORY_COMPONENT_T8_S29_V002', 'long_horizon_roster')):
        memory['entries'].append(champion_memory(unit_name, task))
    memory['entries'].append({
        'source_run': 'T7_S11_MEMORY_d001', 'task': 'proposal_semantics_failure', 'status': 'FAILED',
        'scope': 'legacy independent-edit interpretation rejected a composite rank-only intent; V003 uses explicit atomic proposals',
        'edits': [],
    })
    write(OUT / 'memory/shared_research_memory.json', memory)
    contract = {
        'schema': 'AUTO-RESEARCH-LOOP-CONTRACT-1',
        'budget': {'max_decisions': 4, 'max_candidate_budget': 48, 'max_model_calls': 4,
                   'max_input_tokens': 240000, 'max_output_tokens': 32000,
                   'max_no_improvement': 4, 'max_debug_depth': 2},
        'primary': {'metric': 'mean_abs_ic', 'min_delta': 0.0},
        'guards': {'min_delta_coverage': -1.0, 'min_delta_pass_rate': -1.0,
                   'max_delta_turnover_proxy': 1.0, 'min_delta_novelty': -1.0,
                   'max_delta_duplicate_rate': 1.0},
        'online_evaluation': 'probe', 'final_evaluation': 'select',
    }
    write(OUT / 'shared_contract.json', contract)
    write(OUT / 'continuation_contract.json', contract)
    protocol = {
        'schema': 'AUTO-RESEARCH-PAPER-READINESS-PROTOCOL-1',
        'protocol_version': 'V003', 'date_frozen': '2026-10-31', 'model_id': MODEL,
        'random_model_id': 'random-atomic-edits-v1', 'random_seed': 20261031,
        'generation': {'effort': 'low', 'temperature': 'cli-default', 'tools': 'disabled',
                       'session_persistence': False, 'cli_model_override': MODEL,
                       'cli_version': '2.1.314', 'python_version': '3.13.15',
                       'proposal_schema': 'AUTO-RESEARCH-ATOMIC-PROPOSAL-1'},
        'proposer': 'skills/current/factor-evolution-harness2/scripts/claude_proposer_command.py',
        'random_proposer': 'skills/current/factor-evolution-harness2/scripts/random_proposer_command.py',
        'proposer_environment': {'AUTO_RESEARCH_CLAUDE_MAX_OUTPUT_TOKENS': '8000',
                                 'AUTO_RESEARCH_CLAUDE_MAX_USD': '0.50',
                                 'AUTO_RESEARCH_CLAUDE_CLI_MODEL': MODEL,
                                 'AUTO_RESEARCH_ATOMIC_PROPOSAL': '1'},
        'proposer_timeout_seconds': 600, 'execution_timeout_seconds': 600,
        'run_root': str(ALBA / 'runs'), 'task_files': task_files,
        'seeds': SEEDS, 'model_replicates': ['A', 'B'],
        'shared_decisions': 4, 'continuation_decisions': 4, 'candidate_budget': 12,
        'dev_split': 'seeded-year-blocks',
        'shared_contract': relative(OUT / 'shared_contract.json'),
        'continuation_contract': relative(OUT / 'continuation_contract.json'),
        'coverage_log_only': True, 'demo': False, 'max_parallel_units': 2,
        'screen_run_prefix': 'PAPER_READINESS_TASK_SCREEN',
        'screen_summary_file': 'initial_defects.json',
        'primary_estimands': ['memory_minus_scalar', 'diagnostic_minus_memory',
                              'memory_minus_random', 'memory_minus_fixed'],
        'analysis': {'task_bootstrap_samples': 10000, 'task_sign_flip_exact': True,
                     'component_multiplicity': 'Holm', 'bootstrap_seed': 20261031},
        'maximum_model_calls': 288, 'maximum_model_candidate_positions': 3456,
        'maximum_random_candidate_positions': 1152, 'maximum_output_tokens': 2304000,
        'score_window_read': False, 'independent_real_data_oos': False,
    }
    write(OUT / 'protocol.json', protocol)
    print(relative(OUT / 'protocol.json'))


if __name__ == '__main__':
    main()
