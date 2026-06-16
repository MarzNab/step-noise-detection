# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Step Noise Finder application."""

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
                StringStruct('FileDescription',  'Step Noise Finder'),
                StringStruct('FileVersion',      '1.0.0.0'),
                StringStruct('InternalName',     'StepNoiseFinder'),
                StringStruct('OriginalFilename', 'StepNoiseFinder.exe'),
                StringStruct('ProductName',      'Step Noise Finder'),
                StringStruct('ProductVersion',   '1.0.0.0'),
            ]),
        ]),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)

a = Analysis(
    [os.path.join(ROOT, 'src', 'step_finder.py')],
    pathex=[os.path.join(ROOT, 'src')],
    binaries=[],
    datas=[],
    hiddenimports=[
        'tdd_reader',
        'numpy',
        'numpy.f2py',
        'numpy.f2py.auxfuncs',
        'numpy.f2py.cfuncs',
        'numpy.f2py.crackfortran',
        'numpy.f2py.f2py2e',
        'scipy',
        'scipy.signal',
        'scipy.signal.windows',
        'scipy.linalg',
        'scipy._lib',
        'scipy._lib.array_api_compat',
        'scipy._lib.array_api_compat.numpy',
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
    a.binaries,
    a.datas,
    [],
    exclude_binaries=False,
    name='StepNoiseFinder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    onefile=True,
    console=False,         # No console window — GUI app
    icon=None,
    version=version_info,
)
