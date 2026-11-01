#!/usr/bin/env python3
"""Generate paper figures from frozen result artifacts."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'auto_research/paper/figures'
BENCH = ROOT / 'auto_research/synth_benchmark/synth_result.json'
ROOTP = ROOT / 'auto_research'


def protocol_figure() -> None:
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    ax.set_xlim(0, 10); ax.set_ylim(0, 4.5); ax.axis('off')
    boxes = [
        (0.2, 1.2, 'Agent search\n(expressions / combos)', '#dbeafe'),
        (2.6, 1.2, 'Claim proposal\n(statement + evidence)', '#e0e7ff'),
        (5.0, 1.2, 'Rule-based reviewer\n(R1–R11)', '#ede9fe'),
        (7.4, 1.2, 'Evidence ledger\n(append-only, hashes)', '#dcfce7'),
    ]
    for x, y, text, color in boxes:
        ax.add_patch(FancyBboxPatch((x, y), 2.0, 1.1, boxstyle='round,pad=0.05', fc=color, ec='#334155'))
        ax.text(x + 1.0, y + 0.55, text, ha='center', va='center', fontsize=8.5)
    for x in (2.2, 4.6, 7.0):
        ax.add_patch(FancyArrowPatch((x, 1.75), (x + 0.35, 1.75), arrowstyle='-|>', mutation_scale=10, color='#334155'))
    ax.text(0.2, 3.4, 'Frozen evaluation stack: dev 2017–2020 | embargo 2020-12 | one-shot score 2021–2023 (already used)',
            fontsize=8, style='italic', color='#475569')
    ax.text(0.2, 0.55, 'Superseding claims, negative results and protocol deviations are first-class records',
            fontsize=8, style='italic', color='#475569')
    ax.add_patch(FancyBboxPatch((0.2, 2.35), 9.2, 0.6, boxstyle='round,pad=0.05', fc='#f8fafc', ec='#94a3b8'))
    ax.text(4.8, 2.65, 'Governance ladder: search-only  →  holdout replication  →  strict replication',
            ha='center', va='center', fontsize=9, weight='bold', color='#0f172a')
    fig.tight_layout()
    fig.savefig(OUT / 'fig_protocol.png', dpi=300, bbox_inches='tight')
    plt.close(fig)


def benchmark_figure() -> None:
    data = json.loads(BENCH.read_text(encoding='utf-8'))['summary']
    tasks = ['planted', 'weak', 'null']
    policies = [('naive', 'Naive (search-only)'), ('governed', 'Governed (holdout)'), ('strict', 'Strict (|IC|≥0.02)')]
    colors = ['#f59e0b', '#3b82f6', '#10b981']
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    width = 0.26
    for index, (key, label) in enumerate(policies):
        values = [data[task][f'{key}_declarations'] for task in tasks]
        offsets = [i + (index - 1) * width for i in range(len(tasks))]
        bars = ax.bar(offsets, values, width=width, label=label, color=colors[index])
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + 0.2, str(value), ha='center', fontsize=8)
    ax.set_xticks(range(len(tasks)))
    ax.set_xticklabels(['planted\n(true signal)', 'weak\n(β=0.12)', 'null\n(no signal)'])
    ax.set_ylabel('Declared discoveries (out of 20 seeds)')
    ax.set_ylim(0, 21)
    ax.legend(fontsize=8, frameon=False, ncol=3, loc='upper center', bbox_to_anchor=(0.5, 1.13))
    ax.spines[['top', 'right']].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / 'fig_benchmark.png', dpi=300, bbox_inches='tight')
    plt.close(fig)


def real_data_figure() -> None:
    # holdout fitness: generated best vs existing-feature baseline, by paradigm
    paradigms = ['standalone', 'residual', 'stability', 'combination']
    generated = [0.126107, 0.003551, 0.106659, 0.161926]
    baseline = [0.146254, 0.004622, 0.137088, 0.133362]
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    x = range(len(paradigms))
    width = 0.36
    ax.bar([i - width / 2 for i in x], generated, width=width, label='Generated (best)', color='#6366f1')
    ax.bar([i + width / 2 for i in x], baseline, width=width, label='Existing-feature baseline', color='#cbd5e1')
    ax.set_xticks(list(x))
    ax.set_xticklabels(paradigms)
    ax.set_ylabel('Dev holdout fitness')
    ax.legend(fontsize=8, frameon=False)
    ax.spines[['top', 'right']].set_visible(False)
    ax.set_title('Only combination search beats the existing-feature baseline', fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / 'fig_real_data.png', dpi=300, bbox_inches='tight')
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    protocol_figure()
    benchmark_figure()
    real_data_figure()
    print(json.dumps({'figures': [str(path.relative_to(ROOT)) for path in sorted(OUT.glob('*.png'))]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
