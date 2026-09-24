from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

METRICS = ('tool_calls', 'tool_ms', 'lines_added', 'lines_removed', 'api_ms',
           'requests', 'hooks', 'hook_ms', 'denials')

FIELDS = ('input', 'output', 'total', 'cached', 'turns')


def _directory() -> Path:
    from pyclaw import pyclaw_home
    return pyclaw_home() / 'usage'


def record(row: dict) -> Path:
    day = str(row.get('at') or '')[:10] or str(row.get('day') or '')
    directory = _directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{day}.jsonl'
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    return path


def _rows(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get('day'):
            out.append(row)
    return out


def read_days(limit: int, until: date | None = None) -> list[dict]:
    last = until or date.today()
    wanted = {(last - timedelta(days=back)).isoformat()
              for back in range(max(1, int(limit)))}
    directory = _directory()
    try:
        files = sorted(directory.glob('*.jsonl'))
    except OSError:
        return []
    rows = []
    for path in files:
        if path.stem in wanted:
            rows += _rows(path)
    return sorted(rows, key=lambda row: str(row.get('at') or ''))


def totals(rows: list[dict]) -> dict:
    out = {name: 0 for name in ('records',) + FIELDS + METRICS}
    for row in rows:
        out['records'] += 1
        for name in FIELDS:
            out[name] += int(row.get(name) or 0)
        metrics = row.get('metrics') or {}
        for name in METRICS:
            out[name] += int(metrics.get(name) or 0)
    return out


def by_day(rows: list[dict]) -> list[tuple[str, dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row['day']), []).append(row)
    return [(day, totals(grouped[day]))
            for day in sorted(grouped, reverse=True)]


def row(at: datetime, session: str, provider: str, model: str, *,
        input_tokens: int, output_tokens: int, cached: int, turns: int,
        metrics: dict) -> dict:
    moment = at.replace(microsecond=0)
    return {'at': moment.isoformat(), 'day': moment.date().isoformat(),
            'session': session, 'provider': provider, 'model': model,
            'input': int(input_tokens), 'output': int(output_tokens),
            'total': int(input_tokens) + int(output_tokens),
            'cached': int(cached),
            'turns': int(turns),
            'metrics': {name: int(metrics.get(name) or 0)
                        for name in METRICS}}
