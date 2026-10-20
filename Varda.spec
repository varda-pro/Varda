# -*- mode: python ; coding: utf-8 -*-

import shutil
import sys
import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

VERSION = tomllib.loads(Path('pyproject.toml').read_text())['project']['version']
MACOS = sys.platform == 'darwin'

# The USGS library folders also hold photos and web pages (~95 MB); Varda reads
# only the spectra and wavelength .txt files (~4 MB)
LIBRARY_SPECTRA = [(str(f), str(f.parent)) for f in Path('librarySpectra').rglob('*.txt')]

# Processing > Optional Features installs PyTorch with the bundled uv, into a
# separate environment that imports the worker package from its own folder
# (so the app's bundled libraries never shadow the environment's)
UV = shutil.which('uv')
if UV is None:
    raise SystemExit('uv must be on PATH to build Varda (it is bundled for Optional Features)')
TORCH_WORKER = [
    (str(f), 'torch_worker/varda_torch_worker')
    for f in Path('src/varda_torch_worker').glob('*.py')
]

# Kindle a Feature gives the AI the plugin authoring guide and the examples
PLUGIN_DOCS = [
    (str(f), str(f.parent))
    for f in Path('docs/plugins').rglob('*')
    if f.is_file() and '__pycache__' not in f.parts
]

a = Analysis(
    ['src/varda/main.py'],
    pathex=[],
    binaries=[(UV, 'bin')],
    # astropy (FITS) needs nothing here: pyinstaller-hooks-contrib's astropy hook
    # collects its data files, submodules and metadata (about 27 MB)
    # GDAL data shipped in rasterio, PROJ data from pyproj, HyPyRameter's parameter
    # tables, Varda's resources and the bundled spectral library
    datas=collect_data_files('rasterio') + collect_data_files('pyproj')
    + collect_data_files('hypyrameter')
    + [('resources', 'resources')]
    + LIBRARY_SPECTRA
    + TORCH_WORKER
    + PLUGIN_DOCS
    # keyring finds its keychain backends through package metadata
    + copy_metadata('keyring')
    # Help > About reads the version from package metadata; Cite reads CITATION.cff
    + copy_metadata('varda')
    + [('CITATION.cff', '.'), ('LICENSE', '.')],
    # HyPyRameter's optional denoiser (iovf_generic) needs packages Varda does not ship
    hiddenimports=collect_submodules('rasterio')
    + collect_submodules('hypyrameter', filter=lambda name: 'iovf' not in name)
    # Only plugins import the plugin API, so PyInstaller can't see it's needed
    + ['varda.api']
    + collect_submodules('keyring.backends'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # OpenCV (~90 MB) is unused; imageio's optional OpenCV plugin would pull it in.
    # PyTorch is never bundled, even if the build environment has it.
    excludes=['cv2', 'imageio.plugins.opencv', 'torch'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Varda',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # A normal windowed app everywhere (no console window behind Varda on
    # Windows); the self-test writes a report file when there's no console
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['resources/logo.ico', 'resources/logo.icns'],  # PyInstaller picks .ico on Windows, .icns on macOS
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Varda',
)

# macOS: a real Varda.app. Gatekeeper judges a downloaded bundle as one unit,
# so the user approves it once; a bare folder of libraries gets every library
# blocked separately ("library load disallowed by system policy").
if MACOS:
    app = BUNDLE(
        coll,
        name='Varda.app',
        icon='resources/logo.icns',
        bundle_identifier='io.github.varda-pro.varda',
        version=VERSION,
        info_plist={
            'CFBundleName': 'Varda',
            'CFBundleDisplayName': 'Varda',
            'CFBundleShortVersionString': VERSION,
            'NSHighResolutionCapable': True,
            'NSRequiresAquaSystemAppearance': False,  # follow light/dark mode
            'LSMinimumSystemVersion': '12.0',
        },
    )

