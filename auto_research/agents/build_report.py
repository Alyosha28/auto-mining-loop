#!/usr/bin/env python3
"""Generate the agentic evaluation report from experiment artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'auto_research/agents/experiments'
OUT = ROOT / 'auto_research/agents/AGENTIC_EVAL_CN.md'


def load(path: Path) -> dict | None:
    if not path.is_file() or path.name.startswith('._'):
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def main() -> None:
    matrices = []
    for path in sorted(EXP.glob('matrix_*.json')):
        data = load(path)
        if data:
            matrices.append({'file': path.name, **data})
    lines = ['# Agentic Auto-Research 评估报告（自动生成）', '',
             f'更新时间：{datetime.now(timezone.utc).isoformat()}', '',
             '> 注意：mock proposer 的矩阵只验证 pipeline，不是模型能力结论；不同模型不得混算。', '',
             '## 实验矩阵', '']
    for matrix in matrices:
        lines += [f"### {matrix.get('model') or 'baselines'}（budget {matrix['budget']}，seeds {matrix['seeds']}，"
                  f"proposer={matrix.get('proposer')}）", '',
                  '| arm | holdout top5 mean | Elo↔fitness Spearman |', '|---|---|---|']
        for arm, value in matrix['summary'].items():
            if arm == 'baseline':
                lines.append(f"| random | {value['random_mean']} | — |")
                lines.append(f"| evolution | {value['evolution_mean']} | — |")
            else:
                lines.append(f"| {arm} | {value.get('holdout_top5_mean')} | {value.get('elo_spearman_mean')} |")
        lines.append('')
    manifests = sorted((EXP / 'manifests').glob('*.manifest.json')) if (EXP / 'manifests').is_dir() else []
    analyses = sorted(list(EXP.glob('analysis_matrix_*.json')) + list((EXP.parent).glob('analysis_matrix_*.json')))
    lines += ['## 证据绑定（manifests / analyses）', '']
    if manifests:
        lines += ['| manifest | result hash ok | score window |', '|---|---|---|']
        for path in manifests:
            data = load(path) or {}
            result = Path('auto_research/agents/experiments') / Path(data.get('result_file', '')).name
            ok = data.get('result_sha256') and result.is_file()
            lines.append(f"| {path.name} | {bool(ok)} | {data.get('score_window_read')} |")
    else:
        lines.append('- 暂无 manifest')
    lines += ['', f"- analysis files: {', '.join(path.name for path in analyses) if analyses else 'none'}", '',
              '## 状态', '',
              f"- 已有矩阵：{', '.join((m.get('provider') or 'local') + '/' + str(m.get('model')) for m in matrices) or 'none'}；",
              f"- 本地 Sage 矩阵：{'已完成' if any((m.get('provider') or 'local') == 'local' for m in matrices) else '运行中/待完成'}；",
              '- gemma/cajal：仅 plumbing smoke test，不参与能力比较；',
              '- 边界：本地数据/本地模型；score 窗未读；新时间窗未重读；主库/evo 库未混入。', '',
              '## 复现命令', '', '```bash',
              '.venv/bin/python auto_research/agents/run_matrix.py --model <model-id> --seeds 7,11,17 --budget 24',
              '.venv/bin/python auto_research/agents/build_report.py',
              '```', '']
    OUT.write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'report': str(OUT.relative_to(ROOT)), 'matrices': [m['file'] for m in matrices]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
