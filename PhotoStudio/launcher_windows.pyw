"""Windows launcher: show startup errors and write a readable log."""
from pathlib import Path
import runpy
import sys
import traceback
import os
folder=Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'PhotoStudio' / 'logs'
folder.mkdir(parents=True, exist_ok=True)
with (folder/'app.log').open('a',encoding='utf-8') as log:
    sys.stdout=log
    sys.stderr=log
    try:
        root=Path(__file__).resolve().parent
        os.chdir(root)
        runpy.run_path(str(root/'app.py'),run_name='__main__')
    except BaseException:
        traceback.print_exc()
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                f'Photo Studio could not start. Read the log:\n{folder / "app.log"}',
                'Photo Studio - Startup Error',0x10)
        except Exception:
            pass
        raise
