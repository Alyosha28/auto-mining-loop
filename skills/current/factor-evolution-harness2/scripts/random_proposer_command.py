#!/usr/bin/env python3
"""Generate bounded legal atomic harness edits without a model or market data."""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evo_harness import apply_edits, editable_surface, validate_proposal

SCHEMA = 'AUTO-RESEARCH-ATOMIC-PROPOSAL-1'


def write_json_new(path: Path, payload) -> None:
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')


def proposal(harness: dict, rng: random.Random) -> dict:
    surface = editable_surface()
    current = list(harness['operators'])
    allowed = surface['operators']['allowed']
    missing = sorted(set(allowed) - set(current))
    for attempt in range(100):
        edits = []
        updated = list(current)
        if missing and (rng.random() < 0.65 or len(current) == 1):
            operator = rng.choice(missing)
            edits.append({'id': 'random_append', 'kind': 'INV', 'component': 'operators',
                          'op': 'append', 'value': operator,
                          'intent': 'random legal operator exploration'})
            updated.append(operator)
        if len(updated) > 1 and rng.random() < 0.65:
            removable = [operator for operator in current if operator in updated]
            maximum = min(len(removable), len(updated) - 1)
            count = rng.randint(1, maximum) if maximum else 0
            removed = rng.sample(removable, count) if count else []
            if removed:
                edits.append({'id': 'random_remove', 'kind': 'DOM', 'component': 'operators.remove',
                              'op': 'remove', 'value': removed,
                              'intent': 'random legal operator-budget reallocation'})
        crossover = surface['skills.crossover']['allowed']
        edits.append({'id': 'random_crossover', 'kind': 'INV', 'component': 'skills.crossover',
                      'op': 'configure',
                      'value': {'pairs': rng.choice(crossover['pairs']),
                                'rule': rng.choice(crossover['rule']),
                                'pair_selection': rng.choice(crossover['pair_selection'])},
                      'intent': 'random legal crossover configuration'})
        edits.append({'id': 'random_depth', 'kind': 'INV', 'component': 'skills.mutation_per_seed',
                      'op': 'set', 'value': rng.choice(surface['skills.mutation_per_seed']['allowed']),
                      'intent': 'random legal mutation-depth exploration'})
        edits = edits[:5]
        validated = [validate_proposal(edit) for edit in edits]
        changed = apply_edits(harness, validated)
        if changed != harness:
            return {'schema': SCHEMA, 'edits': validated}
    raise RuntimeError('could not generate a changed legal atomic proposal')


def main() -> None:
    generation = Path(os.environ['AUTO_RESEARCH_GENERATION'])
    state = json.loads((generation / 'research_state.json').read_text(encoding='utf-8'))
    seed = os.environ.get('AUTO_RESEARCH_RANDOM_SEED')
    if seed is None:
        raise ValueError('AUTO_RESEARCH_RANDOM_SEED is required')
    rng = random.Random(f'{seed}:{os.environ["AUTO_RESEARCH_RUN_ID"]}')
    payload = proposal(state['current_harness'], rng)
    write_json_new(Path(os.environ['AUTO_RESEARCH_USAGE']), {
        'schema': 'AUTO-RESEARCH-MODEL-USAGE-1',
        'model_id': os.environ['AUTO_RESEARCH_MODEL_ID'],
        'calls': 0, 'input_tokens': 0, 'output_tokens': 0,
    })
    write_json_new(Path(os.environ['AUTO_RESEARCH_PROPOSAL']), payload)


if __name__ == '__main__':
    main()
