"""Separate process: visible Chromium for logging in to Facy.

Protocol: JSON lines on stdout ({'kind': ..., 'value': ...}); the parent sends
a line 'done' (take the session and quit) or 'cancel' on stdin. No passwords
are ever read; only cookies/localStorage of facy.ai are returned.
"""
import json
import sys
import threading
import time

try:
    from .facy_session import clean_state
except ImportError:  # direct script in development mode
    from facy_session import clean_state

FACY_URL = 'https://facy.ai/swap-face-ai/photo-batch'
cmd = {'value': None}


def emit(kind, value=None):
    print(json.dumps({'kind': kind, 'value': value}, ensure_ascii=False), flush=True)


def reader():
    for line in sys.stdin:
        word = line.strip().lower()
        if word == 'cancel':
            cmd['value'] = 'cancel'
            return
        if word == 'done':
            cmd['value'] = 'done'
    cmd['value'] = 'cancel'  # parent closed the pipe


def launch(p):
    errors = []
    args = ['--disable-blink-features=AutomationControlled', '--no-first-run', '--no-default-browser-check',
            '--disable-extensions', '--disable-background-networking', '--disable-sync',
            '--disable-component-update', '--disable-default-apps']
    for kw in ({}, {'channel': 'chrome'}, {'channel': 'msedge'}):
        try:
            return p.chromium.launch(headless=False, args=args,
                                     ignore_default_args=['--enable-automation'], **kw)
        except Exception as exc:  # try the next browser
            errors.append(str(exc).splitlines()[0] if str(exc) else type(exc).__name__)
    raise RuntimeError('Не удалось открыть браузер для входа. ' + ' | '.join(errors[:2]))


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        emit('error', 'Не установлен Playwright. Запусти установщик Photo Studio заново.')
        return 2
    threading.Thread(target=reader, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = launch(p)
            context = browser.new_context()
            page = context.new_page()
            emit('launched', 'Браузер запущен')
            try:
                page.goto(FACY_URL, wait_until='commit', timeout=60000)
                emit('status', 'Сайт открыт. Войди в Facy в окне браузера.')
            except Exception:
                emit('status', 'Сайт грузится медленно. Подожди или обнови страницу в браузере.')
            last_url = ''
            while True:
                if cmd['value'] == 'cancel':
                    emit('cancelled')
                    break
                try:
                    if page.is_closed():
                        emit('closed')
                        break
                    url = page.url
                    if url != last_url:
                        last_url = url
                        emit('url', url)
                    if cmd['value'] == 'done':
                        state = clean_state(context.storage_state())
                        cmd['value'] = None  # stay open: the user may retry
                        emit('state', {'state': state, 'url': url})
                except Exception as exc:
                    if cmd['value'] == 'done':
                        emit('error', f'Не удалось прочитать сессию: {exc}')
                    else:
                        emit('closed')
                    break
                time.sleep(0.3)
            try:
                browser.close()
            except Exception:
                pass
    except Exception as exc:
        emit('error', str(exc))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
