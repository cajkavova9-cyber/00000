"""Pure (Qt-free, Playwright-free) helpers for the Facy login session."""
from __future__ import annotations

FACY_HOST = 'facy.ai'
LOGIN_MARKERS = ('/login', '/sign-in', '/signin', '/sign-up', '/signup', '/auth')


def is_facy_host(host: str) -> bool:
    host = (host or '').lstrip('.').lower()
    return host == FACY_HOST or host.endswith('.' + FACY_HOST)


def url_is_login(url: str) -> bool:
    low = (url or '').lower().split('?', 1)[0]
    return any(m in low for m in LOGIN_MARKERS)


def url_is_facy(url: str) -> bool:
    low = (url or '').lower()
    return low.startswith('https://facy.ai/') or low.startswith('https://www.facy.ai/') or low == 'https://facy.ai'


def facy_cookies(state) -> list:
    if not isinstance(state, dict):
        return []
    return [c for c in state.get('cookies') or []
            if isinstance(c, dict) and is_facy_host(str(c.get('domain', '')))]


def clean_state(state):
    """Keep only what the engine needs: facy.ai cookies and its localStorage."""
    if not isinstance(state, dict):
        return None
    origins = []
    for o in state.get('origins') or []:
        if isinstance(o, dict) and url_is_facy(str(o.get('origin', '')) + '/'):
            origins.append({'origin': o.get('origin'), 'localStorage': list(o.get('localStorage') or [])})
    return {'cookies': facy_cookies(state), 'origins': origins}


def validate_login(state, url: str):
    """Return (ok, message). The message is Russian text shown to the user."""
    if not url_is_facy(url):
        return False, 'Вернись в окне браузера на сайт facy.ai и заверши вход.'
    if url_is_login(url):
        return False, 'Вход ещё не завершён: в браузере открыта страница входа Facy.'
    if not facy_cookies(state):
        return False, 'Сессия Facy не найдена. Заверши вход в окне браузера и нажми «Готово» ещё раз.'
    return True, 'Вход выполнен.'
