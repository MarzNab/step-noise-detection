# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Step Noise Detection application."""

import os
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

ROOT = os.path.abspath('.')

version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=(1, 0, 0, 0),
        prodvers=(1, 0, 0, 0),
        mask=0x3F,
        flags=0x0,
        OS=0x40004,        # VOS_NT_WINDOWS32
        fileType=0x1,      # VFT_APP
        subtype=0x0,
    ),
    kids=[
        StringFileInfo([
            StringTable('040904B0', [
                StringStruct('CompanyName',      'Nabsys'),
                StringStruct('FileDescription',  'Step Noise Detection'),
                StringStruct('FileVersion',      '1.0.0.0'),
                StringStruct('InternalName',     'StepNoiseDetection'),
                StringStruct('OriginalFilename', 'StepNoiseDetection.exe'),
                StringStruct('ProductName',      'Step Noise Detection'),
                StringStruct('ProductVersion',   '1.0.0.0'),
            ]),
        ]),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)

a = Analysis(
    [os.path.join(ROOT, 'src', 'main.py')],
    pathex=[os.path.join(ROOT, 'src')],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'doc'), 'doc'),
    ],
    hiddenimports=[
        'detectors',
        'detectors.rms',
        'detectors.edge',
        'detectors.fft',
        'detectors.envelope',
        'tdd_reader',
        'app',
        'numpy',
        'scipy',
        'scipy.signal',
        'scipy.signal.windows',
        'matplotlib',
        'matplotlib.backends.backend_tkagg',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StepNoiseDetection',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,         # No console window — GUI app
    icon=None,
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='StepNoiseDetection',
)
