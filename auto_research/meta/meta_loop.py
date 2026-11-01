#!/usr/bin/env python3
"""Meta-loop: prereg -> baseline -> sandbox patch -> candidate -> manifest -> claim.

Default candidate execution happens in a copied tree (auto_research/scripts,
auto_research/benchmark, skills/...), so the live tree is never modified.
Supports either a literal {target, old, new} patch or a unified diff.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import numpy as np
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / 'auto_research/meta/runs'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sample_ok(planned: int, n_pairs: int) -> bool:
    return (not planned) or (n_pairs >= planned)


def decide_adoption(metric_pass: bool, planned: int, n_pairs: int, is_mechanism: bool,
                    has_replication: bool, original_provisional: bool | None,
                    delta: float, min_delta: float, practical: float = 0.01) -> bool:
    if not sample_ok(planned, n_pairs) or not metric_pass:
        return False
    if is_mechanism:
        if delta < max(min_delta, practical):
            return False
        if has_replication and not original_provisional:
            return False
        if not has_replication:
            return False
    return True


def paired_ci(baseline: dict, candidate: dict) -> tuple[float | None, float | None, int]:
    base_runs = baseline.get('runs') or []
    cand_runs = candidate.get('runs') or []
    def key(row):
        return (row.get('task'), row.get('seed'))
    base = {key(row): row.get('fitness', row.get('holdout_fitness')) for row in base_runs}
    cand = {key(row): row.get('fitness', row.get('holdout_fitness')) for row in cand_runs}
    deltas = [cand[k] - base[k] for k in sorted(base.keys() & cand.keys())
              if base[k] is not None and cand[k] is not None]
    if len(deltas) < 2:
        return None, None, len(deltas)
    rng = np.random.default_rng(20261023)
    boots = [float(np.mean(rng.choice(deltas, len(deltas), replace=True))) for _ in range(5000)]
    return float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5)), len(deltas)


def run_command(command: str, cwd: Path | None = None, env: dict | None = None) -> dict:
    completed = subprocess.run(shlex.split(command), cwd=cwd or ROOT, capture_output=True,
                               text=True, timeout=3600, env=env)
    if completed.returncode != 0:
        raise SystemExit(f'command failed: {command}\n{completed.stderr[-1500:]}')
    lines = [line for line in completed.stdout.splitlines() if line.strip().startswith('{')]
    if not lines:
        raise SystemExit(f'no JSON output: {command}')
    return json.loads(lines[-1])


def make_sandbox(run_dir: Path) -> Path:
    sandbox = run_dir / 'sandbox'
    sandbox.mkdir()
    for rel in ('auto_research/scripts', 'auto_research/benchmark', 'skills/current/factor-evolution-harness2'):
        source = ROOT / rel
        if source.is_dir():
            shutil.copytree(source, sandbox / rel, ignore=shutil.ignore_patterns('__pycache__', '._*'))
    meta_dir = sandbox / 'auto_research/meta'
    meta_dir.mkdir(parents=True)
    for probe in (ROOT / 'auto_research/meta').glob('probe_*.py'):
        shutil.copy2(probe, meta_dir / probe.name)
    (sandbox / '.venv').symlink_to(ROOT / '.venv')
    return sandbox


def apply_patch(proposal: dict, sandbox: Path) -> dict:
    if 'diff' in proposal:
        patch_file = sandbox / 'patch.diff'
        patch_file.write_text(proposal['diff'], encoding='utf-8')
        check = subprocess.run(['patch', '-p1', '--dry-run', '-d', str(sandbox), '-i', str(patch_file)],
                               capture_output=True, text=True)
        if check.returncode != 0:
            raise SystemExit(f'patch dry-run failed: {check.stderr[-800:]}')
        subprocess.run(['patch', '-p1', '-d', str(sandbox), '-i', str(patch_file)], check=True,
                       capture_output=True, text=True)
        return {'type': 'unified_diff', 'sha256': sha256_text(proposal['diff'])}
    target = sandbox / proposal['target']
    original = target.read_text(encoding='utf-8')
    if original.count(proposal['old']) != 1:
        raise SystemExit('patch anchor must occur exactly once')
    target.write_text(original.replace(proposal['old'], proposal['new']), encoding='utf-8')
    return {'type': 'literal_replace', 'target': proposal['target'],
            'pre_sha256': sha256_text(original), 'post_sha256': sha256(target)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proposal', required=True)
    parser.add_argument('--os-sandbox', action='store_true')
    args = parser.parse_args()
    proposal = json.loads((ROOT / args.proposal).read_text())
    prereg_path = ROOT / proposal['prereg']
    if not prereg_path.is_file():
        raise SystemExit(f'missing prereg: {prereg_path}')
    prereg = json.loads(prereg_path.read_text())
    if prereg.get('status') != 'locked-before-data':
        raise SystemExit('prereg is not locked-before-data')
    patch_id = proposal['patch_id']
    run_dir = RUNS / patch_id
    if run_dir.exists():
        raise SystemExit(f'refusing to overwrite {run_dir}')
    run_dir.mkdir(parents=True)
    baseline = run_command(proposal['baseline_cmd'])
    sandbox = make_sandbox(run_dir)
    patch_manifest = apply_patch(proposal, sandbox)
    env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin:' + str(ROOT / '.venv/bin'),
           'HOME': str(run_dir / 'home'), 'TMPDIR': str(run_dir / 'tmp'),
           'PYTHONPATH': str(sandbox / 'auto_research') + ':' + str(sandbox / 'auto_research/scripts')}
    (run_dir / 'home').mkdir(exist_ok=True)
    (run_dir / 'tmp').mkdir(exist_ok=True)
    candidate_cmd = proposal['candidate_cmd']
    os_sandbox = False
    if args.os_sandbox:
        profile = ('(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'
                   f'(allow file-write* (subpath "{sandbox}") (subpath "{run_dir / "home"}") '
                   f'(subpath "{run_dir / "tmp"}") (subpath "/private/tmp") (subpath "/private/var/folders"))\n')
        candidate_cmd = 'sandbox-exec -p ' + shlex.quote(profile) + ' ' + proposal['candidate_cmd']
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        os_sandbox = True
    candidate = run_command(candidate_cmd, cwd=sandbox, env=env)
    success = proposal['success']
    metric = success['metric']
    delta = candidate[metric] - baseline[metric]
    ci_low, ci_high, n_pairs = paired_ci(baseline, candidate)
    planned_pairs = prereg.get('fixed_n') or len(prereg.get('design', {}).get('seeds', []) or [])
    sample_complete = sample_ok(planned_pairs, n_pairs)
    metric_pass = delta > success.get('min_delta', 0.0)
    if not sample_complete:
        metric_pass = False
    if ci_low is not None:
        metric_pass = metric_pass and ci_low > 0
    if 'guard_metric' in success:
        guard_delta = candidate[success['guard_metric']] - baseline[success['guard_metric']]
        metric_pass = metric_pass and guard_delta >= -success.get('guard_tolerance', 0.0)
    patch_class = proposal.get('patch_class', 'unspecified')
    is_mechanism = patch_class == 'mechanism_change'
    replication_of = proposal.get('replication_of')
    original_provisional = None
    if is_mechanism and replication_of:
        original_path = RUNS / replication_of / 'manifest.json'
        if not original_path.is_file():
            raise SystemExit(f'missing original manifest: {original_path}')
        original = json.loads(original_path.read_text())
        original_provisional = bool(original.get('provisional_pass'))
    provisional_pass = bool(is_mechanism and metric_pass and not replication_of)
    adopted = decide_adoption(metric_pass, planned_pairs, n_pairs, is_mechanism,
                              bool(replication_of), original_provisional, delta,
                              success.get('min_delta', 0.0))
    manifest = {
        'schema': 'AUTO-RESEARCH-META-RUN-1',
        'patch_id': patch_id,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'prereg': proposal['prereg'],
        'prereg_id': prereg['prereg_id'],
        'patch': patch_manifest,
        'sandbox': True,
        'os_sandbox': bool(os_sandbox),
        'isolation': ('copied file tree + scrubbed env + macOS sandbox-exec (deny network, restricted writes)'
                      if args.os_sandbox else 'copied file tree + scrubbed env; not a VM/container'),
        'rollback': 'live tree untouched; sandbox retained for audit',
        'baseline': baseline,
        'candidate': candidate,
        'metric': metric,
        'metric_delta': delta,
        'paired_ci': [ci_low, ci_high],
        'n_pairs': n_pairs,
        'planned_pairs': planned_pairs,
        'sample_complete': bool(sample_complete),
        'adopted': bool(adopted),
        'proposal_rationale': proposal.get('rationale', ''),
        'replication_of': proposal.get('replication_of'),
        'patch_class': patch_class,
        'provisional': provisional_pass,
        'provisional_pass': provisional_pass,
    }
    for name, payload in (('manifest.json', manifest), ('baseline.json', baseline), ('candidate.json', candidate)):
        (run_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    if adopted:
        spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'meta_loop', 'claims': [{
            'claim_id': f'CLM-META-{patch_id}', 'type': 'experimental_result',
            'statement': (f"Meta-loop 采纳 {patch_id}：{metric} 由 {baseline[metric]:.6f} 变为 {candidate[metric]:.6f}（Δ={delta:+.6f}），"
                          + (f"结构改动经两阶段（初测 + replication {proposal.get('replication_of')}）通过；" if is_mechanism else "") +
                          f"在预注册 {prereg['prereg_id']} 的 sandbox A/B 下达到判据；live 树未修改。"),
            'evidence': [str((run_dir / 'manifest.json').relative_to(ROOT)),
                         str((run_dir / 'baseline.json').relative_to(ROOT)),
                         str((run_dir / 'candidate.json').relative_to(ROOT)),
                         proposal['prereg']],
            'run_refs': ['auto_research/meta'],
            'preregistration': proposal['prereg'],
            'score_window_read': False,
        }]}
        spec_path = run_dir / 'claim_spec.json'
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        subprocess.run([sys.executable, str(ROOT / 'auto_research/scripts/claim_ledger.py'), 'add',
                        '--spec', str(spec_path), '--snapshot'], check=True, cwd=ROOT)
        subprocess.run([sys.executable, str(ROOT / 'auto_research/scripts/review_claims.py'), 'run'], check=True, cwd=ROOT)
    print(json.dumps({'patch_id': patch_id, 'adopted': adopted, 'metric': metric,
                      'baseline': baseline[metric], 'candidate': candidate[metric], 'delta': delta,
                      'run_dir': str(run_dir.relative_to(ROOT))}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
