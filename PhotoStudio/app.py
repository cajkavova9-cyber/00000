"""Photo Studio HyperMotion — private, local-only production file processing service.

UI served to loopback browser; originals never modified. No remote storage or tracking.
Facy is an optional, separately authenticated external service.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:
    raise SystemExit('Pillow is required. Re-run the installer.')

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / 'engine'
DATA_DIR = Path.home() / 'Pictures' / 'Photo Studio'
TEMP_ROOT = Path(tempfile.mkdtemp(prefix='photostudio-'))
ALLOWED_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}
UPLOAD_MAX = 128 * 1024 * 1024
BODY_MAX = 1024 * 1024
MAX_FILES = 10000


def unique_path(dirpath: Path, name: str):
    dirpath.mkdir(parents=True, exist_ok=True)
    name = Path(name).name.replace('\0', '')
    stem = Path(name).stem or 'image'
    ext = Path(name).suffix.lower()
    candidate = dirpath / f'{stem}{ext}'
    n = 2
    while candidate.exists():
        candidate = dirpath / f'{stem}_{n}{ext}'
        n += 1
    return candidate


def safe_relative(raw):
    raw = str(raw or '').replace('\\', '/')
    parts = [part for part in raw.split('/') if part not in ('', '.')]
    if not parts or any(part == '..' or '\0' in part for part in parts):
        return ''
    return '/'.join(parts[-16:])


def choose_folder_native(initial: Path) -> Path | None:
    """OS-owned folder chooser. Never accept arbitrary browser-provided disk paths."""
    initial = Path(initial).expanduser()
    if not initial.is_dir():
        initial = Path.home()
    if sys.platform == 'darwin':
        # Quotes/backslashes are escaped for an AppleScript string literal.
        prompt = 'Choose where Photo Studio should save results'
        start = str(initial).replace('\\', '\\\\').replace('"', '\\"')
        script = 'POSIX path of (choose folder with prompt "' + prompt + '" default location POSIX file "' + start + '")'
        proc = subprocess.run(['osascript', '-e', 'activate', '-e', script], capture_output=True, text=True, timeout=180)
        if proc.returncode != 0:
            if '-128' in proc.stderr or 'User canceled' in proc.stderr:
                return None
            raise RuntimeError('macOS folder picker: ' + proc.stderr.strip()[:250])
        path = proc.stdout.strip()
    elif sys.platform == 'win32':
        # Powershell STA WinForms owner; path stays inside the local process.
        code = r'''
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = 'Choose a folder for Photo Studio results'
$d.SelectedPath = $env:PS_FACE_DEFAULT
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.Show()
try { $result = $d.ShowDialog($owner) } finally { $owner.Close(); $owner.Dispose() }
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    [Console]::WriteLine($d.SelectedPath)
}
'''
        env = dict(os.environ, PS_FACE_DEFAULT=str(initial))
        proc = subprocess.run(['powershell.exe','-NoProfile','-STA','-Command',code],
                              env=env,capture_output=True,text=True,timeout=180,encoding='utf-8',errors='replace')
        if proc.returncode != 0:
            raise RuntimeError('Windows folder picker: ' + proc.stderr.strip()[:250])
        path = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ''
    else:
        # Linux dev/test machines; production package targets Windows/macOS.
        if shutil.which('zenity'):
            proc = subprocess.run(['zenity','--file-selection','--directory',
                                   '--filename',str(initial)+'/'],capture_output=True,text=True,timeout=180)
            path = proc.stdout.strip() if proc.returncode == 0 else ''
        else:
            raise RuntimeError('No supported graphical folder picker on this system')
    if not path:
        return None
    folder = Path(path).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError('Selected result folder does not exist')
    return folder


@dataclass
class Upload:
    name: str
    path: Path
    relative: str = ''


@dataclass
class Job:
    id: str
    kind: str
    total: int
    output: Path
    done: int = 0
    status: str = 'queued'
    message: str = 'Queued'
    error: str = ''
    bytes_in: int = 0
    bytes_out: int = 0
    cancelled: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self):
        with self.lock:
            return dict(id=self.id, kind=self.kind, total=self.total,
                        done=self.done, status=self.status, message=self.message,
                        error=self.error, output=str(self.output),
                        bytes_in=self.bytes_in, bytes_out=self.bytes_out,
                        percent=round(self.done / max(self.total, 1) * 100))

    def update(self, **kwargs):
        with self.lock:
            for k, v in kwargs.items():
                setattr(self, k, v)


class Studio:
    def __init__(self, output_root=None):
        self.token = secrets.token_urlsafe(32)
        self.files: dict[str, Upload] = {}
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        self.output_root = Path(output_root or DATA_DIR).expanduser().resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.face_output_root = self.output_root
        self.output_roots = {k: self.output_root for k in ('face','crop','compress','sort')}
        self.settings_file = Path.home() / 'Library' / 'Application Support' / 'PhotoStudio' / 'settings.json' if sys.platform == 'darwin' else Path.home() / '.config' / 'PhotoStudio' / 'settings.json'
        self.settings = {}
        try:
            self.settings = json.loads(self.settings_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        # Restore only existing folders; never silently create a stale/removable location.
        saved_face = self.settings.get('face_output_dir')
        if output_root is None and isinstance(saved_face, str) and Path(saved_face).expanduser().is_dir():
            self.face_output_root = Path(saved_face).expanduser().resolve()
        self.output_roots['face'] = self.face_output_root
        for kind in ('crop','compress','sort'):
            saved = self.settings.get(kind + '_output_dir')
            if isinstance(saved,str) and Path(saved).expanduser().is_dir():
                self.output_roots[kind] = Path(saved).expanduser().resolve()
        self.session: dict | None = None
        self.auth_proc = None
        self.auth_status = 'disconnected'
        self.auth_detail = ''
        self.auth_url = ''
        self.auth_lock = threading.RLock()
        self.auth_thread = None

    def update_settings(self, req):
        if not isinstance(req, dict):
            raise ValueError('Invalid settings')
        allowed = {'theme', 'language'}
        settings = {k: v for k, v in req.items() if k in allowed}
        if 'language' in settings and settings['language'] not in {'ru', 'en', 'uk'}:
            raise ValueError('Unsupported language')
        if 'theme' in settings:
            theme = settings['theme']
            if not isinstance(theme, dict) or theme.get('count') not in (2,3):
                raise ValueError('Invalid theme')
            colors = theme.get('colors')
            import re
            if not isinstance(colors,list) or len(colors)!=3 or not all(isinstance(c,str) and re.fullmatch('#[0-9a-fA-F]{6}',c) for c in colors):
                raise ValueError('Invalid theme colors')
        with self.lock:
            self.settings.update(settings)
            self._persist_settings()
        return {'ok':True}

    def output_snapshot(self, kind):
        if kind not in self.output_roots:
            raise ValueError('Unknown tool')
        with self.lock:
            path = self.output_roots[kind]
            return {'kind': kind, 'path': str(path), 'default': str(self.output_root),
                    'custom': path != self.output_root}

    def select_output(self, kind):
        if kind not in self.output_roots:
            raise ValueError('Unknown tool')
        with self.lock:
            initial = self.output_roots[kind]
        selected = choose_folder_native(initial)
        if selected is None:
            return {**self.output_snapshot(kind), 'cancelled': True}
        try:
            with tempfile.TemporaryFile(dir=selected):
                pass
        except OSError as exc:
            raise ValueError('Cannot write into the selected folder') from exc
        with self.lock:
            self.output_roots[kind] = selected
            if kind == 'face':
                self.face_output_root = selected
            self.settings[kind + '_output_dir'] = str(selected)
            self._persist_settings()
        return self.output_snapshot(kind)

    def reset_output(self, kind):
        if kind not in self.output_roots:
            raise ValueError('Unknown tool')
        with self.lock:
            self.output_roots[kind] = self.output_root
            if kind == 'face':
                self.face_output_root = self.output_root
            self.settings.pop(kind + '_output_dir', None)
            self._persist_settings()
        return self.output_snapshot(kind)

    def face_output_snapshot(self):
        return self.output_snapshot('face')

    def select_face_output(self):
        return self.select_output('face')

    def reset_face_output(self):
        return self.reset_output('face')

    def _persist_settings(self):
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.settings_file.with_suffix('.tmp')
        temp.write_text(json.dumps(self.settings, ensure_ascii=False), encoding='utf-8')
        os.chmod(temp, 0o600)
        os.replace(temp, self.settings_file)

    def add_upload(self, stream, length, filename, relative=''):
        if length < 1 or length > UPLOAD_MAX:
            raise ValueError('Each file must be between 1 byte and 128 MB.')
        original = Path(str(filename).replace('\\', '/')).name
        if Path(original).suffix.lower() not in ALLOWED_EXT:
            raise ValueError('Unsupported image format.')
        with self.lock:
            if len(self.files) >= MAX_FILES:
                raise ValueError('Too many files in this session.')
        identifier = uuid.uuid4().hex
        dest = TEMP_ROOT / f'{identifier}{Path(original).suffix.lower()}'
        try:
            with dest.open('xb') as dst:
                remaining = length
                while remaining:
                    part = stream.read(min(1024 * 1024, remaining))
                    if not part:
                        raise ValueError('Incomplete upload.')
                    dst.write(part)
                    remaining -= len(part)
            with Image.open(dest) as img:
                img.verify()
            with self.lock:
                self.files[identifier] = Upload(original, dest, safe_relative(relative))
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        return {'id': identifier, 'name': original, 'bytes': length}

    def create_job(self, request):
        kind = request.get('kind', '')
        if kind not in {'compress', 'crop', 'sort', 'face'}:
            raise ValueError('Unknown operation.')
        ids = request.get('files') or []
        if not isinstance(ids, list) or not ids or len(ids) > MAX_FILES:
            raise ValueError('Select images first.')
        with self.lock:
            if len(self.jobs) > 100:
                self.jobs = {k: v for k, v in list(self.jobs.items())[-40:]}
            missing = [x for x in ids if x not in self.files]
            if missing:
                raise ValueError('Uploaded files expired. Select them again.')
            uploads = [self.files[x] for x in ids]
        if kind == 'face' and not self.session:
            raise ValueError('Connect Facy AI and complete sign-in first.')
        stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        with self.lock:
            base_output = self.output_roots[kind]
        if not base_output.is_dir():
            raise ValueError('Selected destination folder is unavailable. Choose another folder.')
        output = base_output / f'{stamp}_{kind}_{uuid.uuid4().hex[:5]}'
        output.mkdir(parents=True, exist_ok=False)
        job = Job(uuid.uuid4().hex, kind, len(uploads), output)
        with self.lock:
            self.jobs[job.id] = job
        worker = threading.Thread(target=self._run, args=(job, uploads, request), daemon=True)
        worker.start()
        return job.snapshot()

    def _save_image(self, img, destination, fmt, quality):
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name('.' + uuid.uuid4().hex + destination.suffix)
        opts = {}
        if fmt == 'JPEG':
            if img.mode not in {'RGB', 'L'}:
                bg = Image.new('RGB', img.size, '#FFFFFF')
                if 'A' in img.getbands():
                    bg.paste(img, mask=img.getchannel('A'))
                else:
                    bg.paste(img.convert('RGB'))
                img = bg
            opts.update(quality=quality, optimize=True, progressive=True)
        elif fmt == 'WEBP':
            opts.update(quality=quality, method=4)
        else:
            opts.update(optimize=True, compress_level=7)
        try:
            img.save(temp, format=fmt, **opts)
            # do not overwrite any result (each folder is new)
            os.link(temp, destination)
        finally:
            temp.unlink(missing_ok=True)

    def _run(self, job, uploads, request):
        try:
            job.update(status='running', message='Processing')
            if job.kind == 'face':
                self._run_facy(job, uploads, request)
            else:
                kind = job.kind
                quality = min(100, max(20, int(request.get('quality', 82))))
                fmt = str(request.get('format', 'WEBP')).upper()
                if fmt not in {'JPEG', 'PNG', 'WEBP'}:
                    fmt = 'WEBP'
                ratio = str(request.get('ratio', '1:1'))
                ratios = {'1:1': 1, '4:5': 4/5, '9:16': 9/16, '16:9': 16/9, '3:2': 3/2}
                size = min(250, max(1, int(request.get('perFolder', 250))))
                import random
                ordering = list(uploads)
                if kind == 'shuffle':
                    rng = random.SystemRandom()
                    if request.get('withinFolders'):
                        groups: dict[str, list[Upload]] = {}
                        for file in ordering:
                            groups.setdefault(str(Path(file.relative).parent), []).append(file)
                        ordering = []
                        for key in sorted(groups):
                            group = groups[key]
                            rng.shuffle(group)
                            ordering.extend(group)
                    else:
                        rng.shuffle(ordering)
                for i, upload in enumerate(ordering):
                    if job.cancelled.is_set():
                        job.update(status='cancelled', message='Cancelled; completed results remain on disk')
                        return
                    dest_dir = job.output
                    if kind == 'sort':
                        dest_dir /= f'Folder_{i // size + 1:03d}'
                    elif kind == 'shuffle' and request.get('withinFolders'):
                        rel = safe_relative(upload.relative)
                        if rel:
                            parent = Path(rel).parent
                            if str(parent) != '.':
                                dest_dir /= parent
                    if kind in ('shuffle', 'sort'):
                        dest = unique_path(dest_dir, f'{i+1:06d}_{upload.name}' if kind == 'shuffle' else upload.name)
                        from engine.batch_files import safe_copy
                        safe_copy(upload.path, dest)
                    else:
                        with Image.open(upload.path) as original:
                            Image.MAX_IMAGE_PIXELS = 120_000_000
                            img = ImageOps.exif_transpose(original)
                            if kind == 'crop':
                                selection = request.get('cropRect')
                                w, h = img.size
                                if isinstance(selection, dict):
                                    coords = [selection.get(k) for k in ('x','y','w','h')]
                                    if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and 0 <= v <= 1 for v in coords):
                                        raise ValueError('Invalid crop rectangle')
                                    x,y,cw,ch = coords
                                    if cw <= 0 or ch <= 0 or x+cw > 1.00001 or y+ch > 1.00001:
                                        raise ValueError('Crop rectangle outside image')
                                    if ratio in ratios:
                                        # In a mixed-size batch preserve a fixed output aspect,
                                        # while keeping the user's normalized selection centre.
                                        aspect = ratios[ratio]
                                        tw, th = cw*w, ch*h
                                        crop_w = max(1, round(min(tw, th*aspect)))
                                        crop_h = max(1, round(crop_w/aspect))
                                        crop_w = min(crop_w,w)
                                        crop_h = min(crop_h,h)
                                        cx,cy = (x+cw/2)*w,(y+ch/2)*h
                                        left = max(0,min(w-crop_w,round(cx-crop_w/2)))
                                        top = max(0,min(h-crop_h,round(cy-crop_h/2)))
                                        box = (left,top,left+crop_w,top+crop_h)
                                    else:
                                        # Free mode: same relative selection on every image.
                                        box = (max(0,round(x*w)),max(0,round(y*h)),min(w,round((x+cw)*w)),min(h,round((y+ch)*h)))
                                    if box[2]-box[0]<1 or box[3]-box[1]<1:
                                        raise ValueError('Crop selection is too small')
                                    img = img.crop(box)
                                else:
                                    aspect = ratios.get(ratio, 1)
                                    if w / h > aspect:
                                        width = int(h * aspect)
                                        box = ((w-width)//2, 0, (w+width)//2, h)
                                    else:
                                        height = int(w / aspect)
                                        box = (0, (h-height)//2, w, (h+height)//2)
                                    img = img.crop(box)
                                    zoom = min(100, max(25, int(request.get('zoom', 65))))
                                    if zoom < 100:
                                        cw = max(1, round(img.width*zoom/100))
                                        ch = max(1, round(img.height*zoom/100))
                                        cx, cy = img.width//2, img.height//2
                                        img = img.crop((cx-cw//2, cy-ch//2, cx-cw//2+cw, cy-ch//2+ch))
                                format_out = 'PNG' if upload.path.suffix.lower() == '.png' else 'JPEG'
                            else:
                                format_out = fmt
                            ext = {'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}[format_out]
                            dest = unique_path(dest_dir, Path(upload.name).stem + ext)
                            self._save_image(img, dest, format_out, quality)
                    job.bytes_in += upload.path.stat().st_size
                    job.bytes_out += dest.stat().st_size
                    job.update(done=i+1, message=f'{i+1} / {len(uploads)} · {upload.name}')
            job.update(status='done', message='Results saved')
        except Exception as exc:
            job.update(status='error', error=f'{type(exc).__name__}: {exc}', message='Operation failed')

    def _run_facy(self, job, uploads, request):
        identifier = request.get('source')
        with self.lock:
            source = self.files.get(identifier)
        if source is None or self.session is None:
            raise ValueError('Select source and connect Facy AI.')
        job.total = len(uploads)
        args = ([sys.executable, '--internal-worker', 'facy'] if getattr(sys, 'frozen', False) else [sys.executable, '-u', str(ENGINE / 'facy_process.py')])
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        job.process = proc
        payload = {'source': str(source.path), 'targets': [str(f.path) for f in uploads],
                   'output': str(job.output), 'state': self.session}
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + '\n')
        proc.stdin.flush()
        proc.stdin.close()
        for line in proc.stdout:
            if job.cancelled.is_set():
                proc.terminate()
                job.update(status='cancelled', message='Stopped by user')
                return
            try:
                event = json.loads(line)
                kind, val = event.get('kind'), event.get('value')
                if kind == 'progress' and isinstance(val, list):
                    job.update(done=int(val[0]))
                elif kind == 'status':
                    job.update(message=str(val)[:300])
                elif kind == 'error':
                    raise RuntimeError(str(val))
                elif kind == 'result':
                    job.update(done=int(val.get('saved', 0)))
            except json.JSONDecodeError:
                continue
        code = proc.wait(timeout=5)
        if code:
            raise RuntimeError(f'Facy returned error (code {code}); see status above.')
        if job.done != job.total:
            raise RuntimeError(f'Facy returned {job.done} of {job.total} images.')
        job.update(status='done', message='Facy results saved')

    def cancel(self, identifier):
        job = self.jobs[identifier]
        job.cancelled.set()
        if job.process and job.process.poll() is None:
            job.process.terminate()
        return job.snapshot()

    def open_output(self, path):
        p = Path(path).resolve()
        with self.lock:
            permitted = [self.output_root, self.face_output_root]
            # Allow opening results from older jobs even if destination changes.
            valid_jobs = [job.output.resolve() for job in self.jobs.values()]
        if not p.is_dir() or not (any(p == base or base in p.parents for base in permitted)
                                       or p in valid_jobs):
            raise ValueError('Invalid result folder.')
        system = platform.system()
        if system == 'Darwin':
            subprocess.Popen(['open', str(p)])
        elif system == 'Windows':
            os.startfile(str(p))
        else:
            subprocess.Popen(['xdg-open', str(p)])

    def begin_auth(self):
        with self.auth_lock:
            if self.auth_proc and self.auth_proc.poll() is None:
                return {'status': self.auth_status, 'detail': self.auth_detail}
            self.auth_status = 'starting'
            self.auth_detail = 'Starting Facy Chromium…'
            self.auth_proc = subprocess.Popen(
                ([sys.executable, '--internal-worker', 'login'] if getattr(sys, 'frozen', False) else [sys.executable, '-u', str(ENGINE / 'facy_login_process.py')]),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            self.auth_thread = threading.Thread(target=self._read_auth, daemon=True)
            self.auth_thread.start()
        return self.auth_snapshot()

    def _read_auth(self):
        from engine.facy_session import validate_login
        proc = self.auth_proc
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind, value = event.get('kind'), event.get('value')
            with self.auth_lock:
                if kind == 'launched':
                    self.auth_status = 'browser_open'
                elif kind == 'status':
                    self.auth_detail = str(value)[:300]
                elif kind == 'url':
                    self.auth_url = str(value)
                elif kind == 'state':
                    ok, msg = validate_login(value['state'], value['url'])
                    self.auth_detail = msg
                    if ok:
                        self.session = value['state']
                        self.auth_status = 'connected'
                    else:
                        self.auth_status = 'browser_open'
                elif kind == 'error':
                    self.auth_detail = str(value)[:300]
                    self.auth_status = 'error'
                elif kind in ('closed', 'cancelled'):
                    if self.auth_status != 'connected':
                        self.auth_status = 'disconnected'
        if self.auth_status not in ('connected', 'error'):
            self.auth_status = 'disconnected'

    def auth_snapshot(self):
        with self.auth_lock:
            return {'status': self.auth_status, 'detail': self.auth_detail,
                    'connected': self.session is not None}

    def complete_auth(self):
        with self.auth_lock:
            if not self.auth_proc or self.auth_proc.poll() is not None:
                raise ValueError('Start Facy connection first.')
            self.auth_proc.stdin.write('done\n')
            self.auth_proc.stdin.flush()
        return self.auth_snapshot()

    def disconnect(self):
        with self.auth_lock:
            self.session = None
            if self.auth_proc and self.auth_proc.poll() is None:
                try:
                    self.auth_proc.stdin.write('cancel\n')
                    self.auth_proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    self.auth_proc.terminate()
            self.auth_status = 'disconnected'
            self.auth_detail = ''
        return self.auth_snapshot()


class Handler(BaseHTTPRequestHandler):
    server_version = 'PhotoStudio/1.0'
    def log_message(self, format, *args):
        pass

    @property
    def studio(self) -> Studio:
        return self.server.studio

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        host = self.headers.get('Host', '')
        return host in (f'127.0.0.1:{self.server.server_address[1]}', f'localhost:{self.server.server_address[1]}')

    def _auth_ok(self):
        if not self._host_ok():
            return False
        if self.headers.get('X-PhotoStudio-Token', '') != self.studio.token:
            return False
        origin = self.headers.get('Origin')
        return not origin or origin == f'http://127.0.0.1:{self.server.server_address[1]}'

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {'error': 'Invalid Host'})
        route = urlparse(self.path).path
        if route in ('/', '/index.html'):
            html = (ROOT / 'web' / 'index.html').read_text(encoding='utf-8')
            html = html.replace('__PS_RUNTIME_TOKEN__', self.studio.token)
            # JSON is inserted as a JS expression, never as raw HTML; escape closing script markup.
            prefs = json.dumps(self.studio.settings,ensure_ascii=True).replace('<','\\u003c')
            html = html.replace('__PS_INITIAL_PREFERENCES__',prefs)
            body = html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'")
            self.end_headers(); self.wfile.write(body)
            return
        if not self._auth_ok():
            return self._json(403, {'error':'Forbidden'})
        if route.startswith('/api/results/'):
            rest = route.removeprefix('/api/results/').split('/')
            job = self.studio.jobs.get(rest[0])
            if not job:
                return self._json(404, {'error':'Job not found'})
            if len(rest) == 1:
                names = [str(p.relative_to(job.output)) for p in job.output.rglob('*')
                         if p.is_file() and p.suffix.lower() in ALLOWED_EXT]
                return self._json(200, {'files': names[:MAX_FILES]})
            if len(rest) == 2 and rest[1] == 'file':
                requested = parse_qs(urlparse(self.path).query).get('name', [''])[0]
                rel = safe_relative(requested)
                path = (job.output / rel).resolve()
                if (not rel or job.output.resolve() not in path.parents or
                    not path.is_file() or path.suffix.lower() not in ALLOWED_EXT):
                    return self._json(404, {'error':'Invalid result'})
                import mimetypes
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(path.stat().st_size))
                self.send_header('Cache-Control','no-store')
                self.end_headers()
                with path.open('rb') as f:
                    shutil.copyfileobj(f, self.wfile, length=1024*1024)
                return
        if route.startswith('/api/output/'):
            return self._json(200, self.studio.output_snapshot(route.split('/')[-1]))
        if route == '/api/face/output':
            return self._json(200, self.studio.face_output_snapshot())
        if route == '/api/settings':
            return self._json(200, self.studio.settings)
        if route == '/api/status':
            return self._json(200, {'ok': True, 'version':'8.0', 'output': str(self.studio.output_root),
                                    'facy': self.studio.auth_snapshot()})
        if route == '/api/facy/status':
            return self._json(200, self.studio.auth_snapshot())
        if route.startswith('/api/jobs/'):
            identifier = route.removeprefix('/api/jobs/')
            job = self.studio.jobs.get(identifier)
            return self._json(200 if job else 404, job.snapshot() if job else {'error':'Job not found'})
        return self._json(404, {'error':'Not found'})

    def do_POST(self):
        if not self._auth_ok():
            return self._json(403, {'error':'Forbidden'})
        route = urlparse(self.path).path
        try:
            length = int(self.headers.get('Content-Length', 0))
            if route == '/api/upload':
                name = unquote(self.headers.get('X-File-Name', ''))
                relative = unquote(self.headers.get('X-Relative-Path', ''))
                return self._json(200, self.studio.add_upload(self.rfile, length, name, relative))
            if length > BODY_MAX:
                raise ValueError('Request too large')
            request = json.loads(self.rfile.read(length) or b'{}')
            if route.startswith('/api/output/') and route.endswith('/select'):
                return self._json(200, self.studio.select_output(route.split('/')[-2]))
            if route.startswith('/api/output/') and route.endswith('/reset'):
                return self._json(200, self.studio.reset_output(route.split('/')[-2]))
            if route == '/api/face/output/select':
                return self._json(200, self.studio.select_face_output())
            if route == '/api/face/output/reset':
                return self._json(200, self.studio.reset_face_output())
            if route == '/api/settings':
                return self._json(200, self.studio.update_settings(request))
            if route == '/api/jobs':
                return self._json(200, self.studio.create_job(request))
            if route.startswith('/api/jobs/') and route.endswith('/cancel'):
                identifier = route.split('/')[3]
                return self._json(200, self.studio.cancel(identifier))
            if route == '/api/reveal':
                self.studio.open_output(request['path'])
                return self._json(200, {'ok':True})
            if route == '/api/facy/connect':
                return self._json(200, self.studio.begin_auth())
            if route == '/api/facy/complete':
                return self._json(200, self.studio.complete_auth())
            if route == '/api/facy/disconnect':
                return self._json(200, self.studio.disconnect())
            return self._json(404, {'error':'Not found'})
        except (ValueError, KeyError, IndexError, FileNotFoundError) as exc:
            return self._json(400, {'error':str(exc)})
        except Exception as exc:
            return self._json(500, {'error':f'{type(exc).__name__}: {exc}'})


def launch_browser(url):
    # The server is already bound before this thread starts; no artificial delay.
    if sys.platform == 'darwin':
        # Chromium --app mode offers a focused window if installed; Safari fallback.
        import shutil as _s
        chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if chrome.is_file():
            subprocess.Popen([str(chrome), f'--app={url}', '--no-first-run'])
            return
    webbrowser.open(url)


def serve(output_root=None, browser=True, port=0):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.studio = Studio(output_root)
    # Prevent unbounded threads accumulating during accidental long upload requests.
    server.daemon_threads = True
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    print(f'Photo Studio running at {url}', flush=True)
    if browser:
        threading.Thread(target=launch_browser, args=(url,), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        server.studio.disconnect()
        server.server_close()
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    serve(args.output, not args.no_browser, args.port)
