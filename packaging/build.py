"""Run on the target OS (macOS or Windows) to produce real binaries.
This is a build script for GitHub Actions; end users do not execute it.
"""
from __future__ import annotations
import os
import platform
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
app = root / 'PhotoStudio'
browser_cache = root / 'native_browsers'
assert browser_cache.is_dir(), 'Bundle missing: run playwright install chromium in build CI.'
assert any(browser_cache.iterdir()), 'Bundled Chromium is empty.'
os.chdir(app)

args = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
        '--name', 'PhotoStudio', '--onedir', '--windowed',
        '--collect-all', 'playwright',
        '--hidden-import', 'engine.size_limit_compress',
        '--hidden-import', 'engine.facy_process',
        '--hidden-import', 'engine.facy_login_process',
        '--hidden-import', 'engine.facy_engine',
        '--hidden-import', 'engine.facy_session',
        '--add-data', 'web:web',
        '--add-data', f'{browser_cache}:ms-playwright',
        '--add-data', 'resources/app_icon.png:resources',
        ]
if sys.platform == 'darwin':
    args += ['--icon', 'resources/app_icon.icns',
             '--osx-bundle-identifier', 'com.photostudio.desktop']
elif sys.platform == 'win32':
    args += ['--icon', 'resources/app_icon.ico']
args.append('native_main.py')
subprocess.run(args, check=True)

if sys.platform == 'darwin':
    bundle = app / 'dist' / 'PhotoStudio.app'
    assert bundle.is_dir(), bundle
    outdir = root / 'artifacts'
    outdir.mkdir(exist_ok=True)
    mount = root / 'dmg-content'
    mount.mkdir(exist_ok=True)
    import shutil
    shutil.copytree(bundle, mount / 'Photo Studio.app', dirs_exist_ok=True)
    apps = mount / 'Applications'
    if not apps.exists():
        apps.symlink_to('/Applications')
    subprocess.run(['hdiutil','create','-volname','Photo Studio', '-srcfolder',str(mount),
                    '-ov','-format','UDZO',str(outdir/'PhotoStudio-macOS.dmg')], check=True)
    print('Built', outdir / 'PhotoStudio-macOS.dmg')
elif sys.platform == 'win32':
    dist_exe = app / 'dist' / 'PhotoStudio' / 'PhotoStudio.exe'
    assert dist_exe.is_file(), dist_exe
    print('Built', dist_exe)
else:
    raise SystemExit('Distribution CI supports macOS and Windows only')
