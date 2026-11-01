#!/usr/bin/env python3
"""Build the auto-research knowledge graph from claims, experiments and lessons.

v0 (P1): nodes = claim / evidence / experiment / panel / operator / lesson;
edges = has_evidence / derived_from / used_panel / proposed_operator / tested_in.
Sources are read-only; outputs are knowledge/graph.json + a timestamped snapshot.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLAIMS = ROOT / 'auto_research/ledger/claims.jsonl'
REVIEWS = ROOT / 'auto_research/ledger/reviews.jsonl'
KG = ROOT / 'auto_research/knowledge/graph.json'
SNAPSHOTS = ROOT / 'auto_research/knowledge/snapshots'
EXPERIENCE = ROOT / 'evo_rsif/experience/index.json'
PANEL_RE = re.compile(r'(data_processed/[A-Za-z0-9_.\-]+)')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def edit_catalog() -> dict:
    path = ROOT / 'skills/current/factor-evolution-harness2/scripts/evo_harness.py'
    spec = importlib.util.spec_from_file_location('evo_harness_catalog', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {item['id']: item for item in module.EDIT_CATALOG}


def panel_family(panel: str) -> str:
    name = panel.lower()
    if 'fundamental' in name:
        return 'fundamental'
    if 'intraday' in name or 'joint_mining_features' in name:
        return 'intraday_microstructure'
    if 'local_quote' in name or 'price_volume' in name:
        return 'price_volume'
    if 'regime' in name:
        return 'market_regime'
    return 'other'


def experiment_attrs(run_ref: str) -> dict:
    directory = ROOT / run_ref
    attrs = {'path': run_ref, 'exists': directory.exists(), 'kind': 'unknown'}
    if not directory.exists():
        return attrs
    if directory.is_dir() and (directory / 'chain_result.json').is_file():
        attrs['kind'] = 'evo_chain'
        chain = load_json(directory / 'chain_result.json')
        attrs.update({'experiment': chain.get('experiment'), 'seed': chain.get('seed'),
                      'rounds': chain.get('rounds_completed'), 'utility_gain': chain.get('utility_gain')})
        rounds = chain.get('rounds') or []
        for row in rounds:
            round_dir = ROOT / row.get('round_dir', '')
            if (round_dir / 'run_manifest.json').is_file():
                manifest = load_json(round_dir / 'run_manifest.json')
                attrs.setdefault('domain', manifest.get('domain'))
                attrs.setdefault('proposal_order', (json.loads((round_dir / 'step_record.json').read_text()).get('proposal_order')
                                                    if (round_dir / 'step_record.json').is_file() else None))
                break
    elif (directory / 'run_manifest.json').is_file():
        attrs['kind'] = 'evo_step'
        manifest = load_json(directory / 'run_manifest.json')
        attrs.update({'domain': manifest.get('domain'),
                      'budget': manifest.get('budget_candidates'),
                      'seed': manifest.get('seed'),
                      'kept_edits': manifest.get('kept_edits')})
        data_source = manifest.get('data_source') or ''
        attrs['panels'] = PANEL_RE.findall(data_source)
        record_path = directory / 'step_record.json'
        if record_path.is_file():
            record = load_json(record_path)
            attrs['proposal_order'] = record.get('proposal_order')
            metrics = (record.get('reference') or {}).get('select', {}).get('metrics') if isinstance(record.get('reference') or {}, dict) else None
            if metrics:
                attrs['reference_select'] = {key: metrics.get(key) for key in
                                             ('mean_abs_ic', 'icir_p25', 'turnover_proxy', 'pass_count')}
    elif (directory / 'manifest.json').is_file():
        attrs['kind'] = 'mining_batch'
        manifest = load_json(directory / 'manifest.json')
        attrs.update({'batch': manifest.get('batch'), 'panels': manifest.get('panels'),
                      'variants': manifest.get('variants'), 'max_candidates': manifest.get('max_candidates')})
    elif (directory / 'fair_result.json').is_file():
        attrs['kind'] = 'fair_replay'
        fair = load_json(directory / 'fair_result.json')
        attrs['pairs'] = [pair.get('tag') for pair in fair.get('pairs', [])]
    elif run_ref.endswith('/batches'):
        attrs['kind'] = 'batch_container'
    return attrs


def build() -> dict:
    catalog = edit_catalog()
    claims = load_jsonl(CLAIMS)
    reviews = {row['claim_id']: row for row in load_jsonl(REVIEWS)}
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def add_node(node_id: str, node_type: str, attrs: dict) -> None:
        if node_id not in nodes:
            nodes[node_id] = {'id': node_id, 'type': node_type, 'attrs': attrs}

    def add_edge(source: str, target: str, edge_type: str, attrs: dict | None = None) -> None:
        edges.append({'source': source, 'target': target, 'type': edge_type, 'attrs': attrs or {}})

    for claim in claims:
        review = reviews.get(claim['claim_id'], {})
        claim_id = f"claim:{claim['claim_id']}"
        add_node(claim_id, 'claim', {
            'statement': claim['statement'], 'claim_type': claim['type'],
            'verdict': review.get('decision', 'unreviewed'),
            'score_window_read': claim.get('score_window_read', False),
            'evidence_count': len(claim.get('evidence', [])),
        })
        for entry in claim.get('evidence', []):
            evidence_id = f"evidence:{entry['path']}"
            add_node(evidence_id, 'evidence', {'path': entry['path'], 'sha256': entry['sha256'],
                                               'suffix': Path(entry['path']).suffix})
            add_edge(claim_id, evidence_id, 'has_evidence')
        for run_ref in claim.get('run_refs', []):
            experiment_id = f'experiment:{run_ref}'
            attrs = experiment_attrs(run_ref)
            add_node(experiment_id, 'experiment', attrs)
            add_edge(claim_id, experiment_id, 'derived_from')
            for panel in attrs.get('panels') or []:
                panel_id = f'panel:{panel}'
                add_node(panel_id, 'panel', {'path': panel, 'family': panel_family(panel)})
                add_edge(experiment_id, panel_id, 'used_panel')
            for edit_id in attrs.get('proposal_order') or []:
                edit = catalog.get(edit_id)
                if not edit:
                    continue
                operator = edit.get('value') if edit.get('component') == 'operators' else edit.get('id')
                operator_id = f'operator:{operator}'
                add_node(operator_id, 'operator', {'value': operator, 'edit_id': edit_id,
                                                   'kind': edit.get('kind')})
                add_edge(experiment_id, operator_id, 'proposed_operator')

    lessons = load_json(EXPERIENCE).get('entries', []) if isinstance(load_json(EXPERIENCE), dict) else []
    if not lessons and EXPERIENCE.is_file():
        raw = json.loads(EXPERIENCE.read_text(encoding='utf-8'))
        lessons = raw if isinstance(raw, list) else []
    for lesson in lessons:
        lesson_id = f"lesson:{lesson.get('id', 'unknown')}"
        add_node(lesson_id, 'lesson', {'domain': lesson.get('domain'), 'cue': lesson.get('cue'),
                                       'lesson': lesson.get('lesson'), 'confidence': lesson.get('confidence')})
        source = str(lesson.get('source', ''))
        for experiment_id in [node_id for node_id in nodes if node_id.startswith('experiment:')]:
            run_ref = experiment_id.split('experiment:', 1)[1]
            if run_ref and run_ref in source:
                add_edge(lesson_id, experiment_id, 'tested_in')

    # literature layer (paper nodes + informs edges)
    papers_path = ROOT / 'auto_research/knowledge/papers.json'
    if papers_path.is_file():
        papers = json.loads(papers_path.read_text(encoding='utf-8')).get('papers', [])
        for paper in papers:
            paper_id = f"paper:{paper['paper_id']}"
            add_node(paper_id, 'paper', {'title': paper.get('title'), 'url': paper.get('url'),
                                         'year': paper.get('year'), 'topics': paper.get('relation', {}).get('topics', [])})
            relation = paper.get('relation', {})
            for claim_id in relation.get('claim_ids', []):
                target = f"claim:{claim_id}"
                if target in nodes:
                    add_edge(paper_id, target, 'informs', {'role': 'evidence/reference'})
            for operator in relation.get('operators', []):
                target = f"operator:{operator}"
                if target in nodes:
                    add_edge(paper_id, target, 'informs', {'role': 'operator rationale'})

    # code layer (component nodes + supports_claim edges)
    code_path = ROOT / 'auto_research/knowledge/code.json'
    if code_path.is_file():
        components = json.loads(code_path.read_text(encoding='utf-8')).get('components', [])
        for component in components:
            code_id = f"code:{component['code_id']}"
            add_node(code_id, 'code', {'path': component.get('path'), 'role': component.get('role'),
                                       'topic': component.get('topic')})
            for claim_id in component.get('supports_claims', []):
                target = f"claim:{claim_id}"
                if target in nodes:
                    add_edge(code_id, target, 'supports_claim', {'role': 'implementation/evidence pipeline'})

    # contradiction edges among claims (polarity heuristic)
    def polarity(statement: str) -> str:
        negative = re.search(r'不成立|未提升|为负|负结果|无证据|mixed|weak|limited|not supported|no evidence', statement, re.I)
        positive = re.search(r'成立|提升|优于|支持|正|improved|positive', statement, re.I)
        if negative:
            return 'negative'
        if positive:
            return 'positive'
        return 'neutral'

    claim_nodes = [node_id for node_id in nodes if node_id.startswith('claim:')]
    claim_neighbours: dict[str, set[str]] = {}
    for edge in edges:
        if edge['type'] in ('has_evidence', 'derived_from'):
            claim_neighbours.setdefault(edge['source'], set()).add(edge['target'])
    for index, left in enumerate(claim_nodes):
        for right in claim_nodes[index + 1:]:
            left_pol = polarity(nodes[left]['attrs']['statement'])
            right_pol = polarity(nodes[right]['attrs']['statement'])
            if left_pol == 'neutral' or right_pol == 'neutral' or left_pol == right_pol:
                continue
            # a real contradiction must be about the same evidence/experiment space,
            # not merely one positive and one negative claim in the same project
            shared = claim_neighbours.get(left, set()) & claim_neighbours.get(right, set())
            if not shared:
                continue
            add_edge(left, right, 'contradicts', {'reason': f'{left_pol}_vs_{right_pol}',
                                                  'shared': sorted(shared)[:5]})
            add_edge(right, left, 'contradicts', {'reason': f'{right_pol}_vs_{left_pol}',
                                                  'shared': sorted(shared)[:5]})

    payload = {
        'schema': 'AUTO-RESEARCH-KG-2',
        'built_at': now(),
        'sources': {
            'claims': {'path': str(CLAIMS.relative_to(ROOT)), 'sha256': sha256(CLAIMS) if CLAIMS.is_file() else None},
            'reviews': {'path': str(REVIEWS.relative_to(ROOT)), 'sha256': sha256(REVIEWS) if REVIEWS.is_file() else None},
            'experience': {'path': str(EXPERIENCE.relative_to(ROOT)), 'sha256': sha256(EXPERIENCE) if EXPERIENCE.is_file() else None},
        },
        'nodes': sorted(nodes.values(), key=lambda node: node['id']),
        'edges': edges,
        'counts': {
            'nodes': len(nodes),
            'edges': len(edges),
            'by_type': {node_type: sum(1 for node in nodes.values() if node['type'] == node_type)
                        for node_type in sorted({node['type'] for node in nodes.values()})},
        },
    }
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    snapshot = SNAPSHOTS / f"graph_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    KG.parent.mkdir(parents=True, exist_ok=True)
    KG.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    snapshot.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return payload


def main() -> None:
    payload = build()
    print(json.dumps({'graph': str(KG.relative_to(ROOT)), 'counts': payload['counts']}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
