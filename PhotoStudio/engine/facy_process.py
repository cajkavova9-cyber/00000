"""Isolated Facy Playwright worker. Receives sensitive session JSON via stdin."""
import json
import os
import sys
import threading
from pathlib import Path
try:
    from .facy_engine import run_facy
except ImportError:  # direct script in development mode
    from facy_engine import run_facy


def emit(kind, payload):
    print(json.dumps({'kind': kind, 'value': payload}, ensure_ascii=False), flush=True)


def main():
    try:
        request = json.loads(sys.stdin.readline())
        targets = [Path(p) for p in request['targets']]
        last = {'done': 0}

        def tracking_emit(kind, payload):
            if kind == 'progress' and isinstance(payload, (list, tuple)) and payload:
                last['done'] = payload[0]
            emit(kind, payload)

        def on_stall(label, seconds):
            # Auto-stop: Facy never finished one file. Everything already
            # saved stays in the output folder; the user can simply start again.
            emit('error', f'{label} не завершилось за {seconds} сек: Facy не отдал файл. '
                          f"Автоостановка. Уже сохранено {last['done']} из {len(targets)} фото, "
                          'они лежат в папке результатов. Запусти обработку ещё раз для оставшихся.')
            os._exit(3)

        completed, total = run_facy(
            Path(request['source']),
            targets,
            Path(request['output']),
            request['state'],
            tracking_emit,
            threading.Event(),
            on_stall=on_stall,
        )
        emit('result', {'saved': completed, 'total': total})
    except Exception as exc:
        emit('error', f'{type(exc).__name__}: {exc}')
        sys.exit(1)


if __name__ == '__main__':
    main()
