#!/usr/bin/env python3
"""Experience library, blind selector and append-only ledger for the v2 evo loop.

Experience entries follow the Harness^2 ``EXPERIENCE.md`` shape:
cue / lesson / evidence / confidence.  Only probes that show an execution
contrast supported by numbers are stored; otherwise NO_ENTRY is recorded.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class ExperienceLibrary:
    """Per-domain experience library with an index and one EXPERIENCE.md per domain."""

    def __init__(self, root: Path, domain: str):
        self.root = Path(root)
        self.domain = domain
        self.dir = self.root / domain
        self.index_path = self.root / 'index.json'
        self.markdown_path = self.dir / 'EXPERIENCE.md'

    def entries(self) -> list[dict]:
        if not self.index_path.is_file():
            return []
        return json.loads(self.index_path.read_text(encoding='utf-8'))

    def load_lessons(self, query: str, top_k: int = 3) -> list[dict]:
        entries = [entry for entry in self.entries() if entry.get('domain') == self.domain]
        tokens = {token for token in query.lower().split() if len(token) > 2}
        scored = []
        for entry in entries:
            cue = entry.get('cue', '').lower()
            hits = sum(1 for token in tokens if token in cue)
            scored.append((hits, entry.get('confidence', 'low'), entry))
        rank = {'high': 2, 'medium': 1, 'low': 0}
        scored.sort(key=lambda item: (-item[0], -rank.get(item[1], 0)))
        return [entry for _hits, _confidence, entry in scored[:top_k]]

    def add(self, *, cue: str, lesson: str, evidence: dict, confidence: str,
            harness_hash: str, source: str = '') -> dict:
        import fcntl
        self.dir.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        entry_id = 'exp_' + _sha256(f'{self.domain}|{cue}|{lesson}|{harness_hash}')[:12]
        entry = {
            'id': entry_id,
            'domain': self.domain,
            'cue': cue,
            'lesson': lesson,
            'evidence': evidence,
            'confidence': confidence,
            'harness_hash': harness_hash,
            'source': source,
            'created_at': datetime.now(timezone.utc).isoformat(),
        }
        with self.index_path.with_suffix('.lock').open('w') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            entries = self.entries()
            if any(item['id'] == entry_id for item in entries):
                return entry
            entries.append(entry)
            temporary = self.index_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
            temporary.replace(self.index_path)
            block = (
                f"\n## {entry_id}\n\n"
                f"- cue: {cue}\n"
                f"- lesson: {lesson}\n"
                f"- evidence: {json.dumps(evidence, ensure_ascii=False)}\n"
                f"- confidence: {confidence}\n"
                f"- harness: `{harness_hash}`\n"
                f"- created: {entry['created_at']}\n"
            )
            with self.markdown_path.open('a', encoding='utf-8') as stream:
                if not self.markdown_path.exists() or self.markdown_path.stat().st_size == 0:
                    stream.write(f'# EXPERIENCE — {self.domain}\n\n'
                                 '可迁移的经验条目（cue/lesson/evidence/confidence）。证据不足时写 NO_ENTRY。\n')
                stream.write(block)
        return entry


def blind_select(compositions: list[dict], select_metrics: list[dict]) -> dict:
    """Pick a composition from anonymised select-split metrics.

    No harness identity is used: only the (pass_count, mean_abs_ic, coverage)
    numbers of the select split, in a fixed lexicographic order.  Ties are
    broken by the original list order (seeded deterministically).
    """
    scored = []
    for index, metrics in enumerate(select_metrics):
        scored.append((-metrics.get('pass_count', 0), -metrics.get('mean_abs_ic', 0.0),
                       -metrics.get('coverage', 0.0), index))
    best = min(scored, key=lambda item: item[:3])[3]
    return {'selected_index': best, 'selected_id': compositions[best]['id'],
            'rule': 'lexicographic(pass_count, mean_abs_ic, coverage) on anonymised select split',
            'candidates': [{'id': f'candidate_{index}', 'pass_count': metrics.get('pass_count'),
                            'mean_abs_ic': metrics.get('mean_abs_ic'),
                            'coverage': metrics.get('coverage')}
                           for index, metrics in enumerate(select_metrics)]}


class Ledger:
    """Append-only JSONL ledger; failed and unknown records are never deleted."""

    def __init__(self, path: Path, run_id: str):
        self.path = Path(path)
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> dict:
        row = {'run_id': self.run_id, 'at': datetime.now(timezone.utc).isoformat(),
               **record}
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        return row


def load_cross_domain_lessons(root: Path, domains: list[str], query: str, top_k: int = 3) -> list[dict]:
    """Read lessons from any of ``domains`` (used for transfer experiments)."""
    index_path = Path(root) / 'index.json'
    if not index_path.is_file():
        return []
    entries = [entry for entry in json.loads(index_path.read_text(encoding='utf-8'))
               if entry.get('domain') in set(domains)]
    tokens = {token for token in query.lower().split() if len(token) > 2}
    rank = {'high': 2, 'medium': 1, 'low': 0}
    scored = []
    for entry in entries:
        text = (entry.get('cue', '') + ' ' + entry.get('lesson', '')).lower()
        hits = sum(1 for token in tokens if token in text)
        scored.append((hits, rank.get(entry.get('confidence', 'low'), 0), entry))
    scored.sort(key=lambda item: (-item[0], -item[1]))
    return [entry for _hits, _rank, entry in scored[:top_k]]
