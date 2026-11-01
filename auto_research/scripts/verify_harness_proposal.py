#!/usr/bin/env python3
"""Candidate-level verification of an approved harness proposal (P2 todo).

Applies the approved edit to a base harness and runs a matched evo step
(same seed/budget/probe) against the unmodified harness. Writes a result,
a claim spec, and sends the claim to the rules reviewer.

No score-window data is read; verifier stays frozen.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim_ledger  # noqa: E402
import review_claims  # noqa: E402

BASE_HARNESS = ROOT / 'evo_rsif/runs/rsif_e1_s7/round_01/harness/base.json'
STEP = ROOT / 'skills/current/factor-evolution-harness2/scripts/run_evo_step.py'
PREREG = 'auto_research/PREREGISTRATION.json'
OUT = ROOT / 'auto_research/harness_improvements/verification'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def evo_harness():
    path = ROOT / 'skills/current/factor-evolution-harness2/scripts/evo_harness.py'
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location('evo_harness_verify', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_step(run_id: str, harness: Path, output: Path) -> dict:
    command = [sys.executable, str(STEP), '--run-id', run_id, '--output', str(output),
               '--harness', str(harness), '--budget', '24', '--k', '1', '--seed', '7',
               '--preregistration', PREREG]
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        raise SystemExit(f'run failed: {run_id}\n{result.stderr[-1500:]}')
    record = json.loads((output / 'step_record.json').read_text(encoding='utf-8'))
    manifest = json.loads((output / 'run_manifest.json').read_text(encoding='utf-8'))
    return {'run_id': run_id, 'output': str(output.relative_to(ROOT)),
            'step_record': str((output / 'step_record.json').relative_to(ROOT)),
            'probe': record['reference']['probe']['metrics'],
            'select': record['reference']['select']['metrics'],
            'kept': manifest['kept_edits']}


def main() -> None:
    harness_module = evo_harness()
    proposal = json.loads((ROOT / 'auto_research/harness_improvements/proposals.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    reviewed = json.loads((ROOT / 'auto_research/harness_improvements/reviews.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    if reviewed.get('decision') != 'approve':
        raise SystemExit('proposal is not approved; refusing verification run')
    base = json.loads(BASE_HARNESS.read_text(encoding='utf-8'))
    modified = harness_module.apply_edit(json.loads(json.dumps(base)), reviewed and proposal['edit'])
    experiment_dir = OUT / proposal['proposal_id']
    if (experiment_dir / 'result.json').exists():
        raise SystemExit(f'verification already exists: {experiment_dir}')
    experiment_dir.mkdir(parents=True, exist_ok=True)
    modified_harness = experiment_dir / 'modified_harness.json'
    modified_harness.write_text(json.dumps(modified, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    base_run = run_step('verify_prop_base', BASE_HARNESS, experiment_dir / 'base_run')
    proposal_run = run_step('verify_prop_modified', modified_harness, experiment_dir / 'proposal_run')
    base_select, prop_select = base_run['select'], proposal_run['select']
    base_probe, prop_probe = base_run['probe'], proposal_run['probe']
    delta = {'turnover_proxy': round(prop_probe['turnover_proxy'] - base_probe['turnover_proxy'], 4),
             'icir_p25': round(prop_select['icir_p25'] - base_select['icir_p25'], 4),
             'mean_abs_ic': round(prop_select['mean_abs_ic'] - base_select['mean_abs_ic'], 4),
             'top_ic_mean': round(prop_probe['top_ic_mean'] - base_probe['top_ic_mean'], 4)}
    supported = (delta['turnover_proxy'] <= -0.02 and delta['icir_p25'] >= -0.01
                 and prop_probe['coverage'] >= 0.96)
    verdict = 'supported' if supported else 'not_supported'
    result = {'schema': 'AUTO-RESEARCH-HARNESS-VERIFICATION-1',
              'proposal_id': proposal['proposal_id'], 'created_at': now(),
              'verdict': verdict, 'delta': delta,
              'base_run': base_run, 'proposal_run': proposal_run,
              'rule': 'supported if turnover_proxy <= -0.02 and icir_p25 >= -0.01 and coverage >= 0.96',
              'score_window_read': False}
    (experiment_dir / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n',
                                                encoding='utf-8')
    claim_spec = {
        'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'p2_harness_verification',
        'claims': [{
            'claim_id': f"CLM-{proposal['proposal_id']}",
            'type': 'experimental_result',
            'statement': (f"候选层验证 {proposal['proposal_id']}（append ema5）：turnover_proxy Δ={delta['turnover_proxy']}, "
                          f"icir_p25 Δ={delta['icir_p25']}, mean|IC| Δ={delta['mean_abs_ic']}，verdict={verdict}。"),
            'evidence': [base_run['step_record'], proposal_run['step_record'],
                         str((experiment_dir / 'result.json').relative_to(ROOT)),
                         'auto_research/harness_improvements/reviews.jsonl'],
            'run_refs': [base_run['output'], proposal_run['output']],
            'preregistration': PREREG, 'score_window_read': False,
        }],
    }
    claim_path = experiment_dir / 'claim_spec.json'
    claim_path.write_text(json.dumps(claim_spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    added = claim_ledger.add_claims(claim_path)
    reviewed_claims = review_claims.review_all()
    print(json.dumps({'proposal_id': proposal['proposal_id'], 'verdict': verdict, 'delta': delta,
                      'claims': [claim['claim_id'] for claim in added],
                      'reviews': [row['decision'] for row in reviewed_claims]}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
