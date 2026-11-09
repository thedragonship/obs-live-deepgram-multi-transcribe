# PyInstaller spec: bundle the Python runtime, application dependencies and UI.
import sys
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('webview')
if sys.platform == 'win32':
    app_icon = 'ui/assets/app-icon.ico'
elif sys.platform == 'darwin':
    app_icon = 'ui/assets/app-icon.icns'
else:
    app_icon = None

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[('ui', 'ui')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='obs-live-deepgram', console=False, icon=app_icon)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, name='obs-live-deepgram')
if sys.platform == 'darwin':
    app_bundle = BUNDLE(
        coll,
        name='OBS Live Deepgram.app',
        bundle_identifier='com.obs-live-deepgram.multitranscribe',
        icon=app_icon,
        info_plist={
            'NSMicrophoneUsageDescription': 'OBS Live Deepgram capture l’entrée audio sélectionnée pour générer des sous-titres en direct.',
        },
    )
