#!/usr/bin/env python3
"""Figure: synthetic benchmark bars + QD archive-cells vs holdout gain."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BENCH = sorted((ROOT / 'auto_research/benchmark/results').glob('bench_decl_*.json'))[-1]
MECH = ROOT / 'auto_research/benchmark/results/mechanism.json'
OUT = ROOT / 'auto_research/paper/figures/fig_bench2.png'


def main() -> None:
    data = json.loads(BENCH.read_text(encoding='utf-8'))['summary']
    tasks = ['planted', 'weak', 'null']
    methods = ['random', 'evolution', 'bo', 'qd']
    labels = {'random': 'random', 'evolution': 'evolution', 'bo': 'BO', 'qd': 'QD-Elites'}
    colors = {'random': '#999999', 'evolution': '#4c72b0', 'bo': '#dd8452', 'qd': '#55a868'}
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    width = 0.2
    x = np.arange(len(tasks))
    for index, method in enumerate(methods):
        values = [data[task][method]['mean'] for task in tasks]
        axes[0].bar(x + (index - 1.5) * width, values, width, label=labels[method], color=colors[method])
    axes[0].axhline(0, color='black', linewidth=0.8)
    axes[0].set_xticks(x); axes[0].set_xticklabels(tasks)
    axes[0].set_ylabel('holdout top-5 fitness')
    axes[0].set_title('Synthetic benchmark (budget 24, 5 seeds)')
    axes[0].legend(frameon=False, fontsize=8)
    mech = json.loads(MECH.read_text(encoding='utf-8'))
    markers = {'planted': 'o', 'weak': 's', 'null': '^'}
    for task, rows in mech.items():
        cells = [row['stats']['archive_cells'] for row in rows]
        deltas = [row['delta'] for row in rows]
        axes[1].scatter(cells, deltas, marker=markers[task], label=task, s=45)
    axes[1].axhline(0, color='black', linewidth=0.8)
    axes[1].set_xlabel('QD archive cells (24 evaluations)')
    axes[1].set_ylabel('holdout gain vs random')
    axes[1].set_title('Archive diversity vs gain')
    axes[1].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=200)
    print('wrote', OUT)


if __name__ == '__main__':
    main()
