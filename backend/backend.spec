# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

a = Analysis(
    ['backend/main_backend.py'],
    pathex=['.'], # Add project root to path so 'backend' and 'astrolib' are found
    binaries=[],
    datas=[
        # Ship the tracked template, not a real astrometrics.config: the latter
        # is gitignored (machine-specific frames_path plus an astrometry.net API
        # key), so referencing it fails the build on a clean checkout and would
        # bake a personal key into the binary. This path had also been stale --
        # it named backend/, where no config has ever lived.
        # The frozen app finds no astrometrics.config next to itself and so
        # falls back to AppConfiguration._populate_defaults(); the template
        # travels alongside as the reference to copy from.
        ('astrometricslib/astrometrics.config.example', '.'),
    ] + collect_data_files('astroquery') + collect_data_files('photutils') +
        collect_data_files('astropy') + collect_data_files('scipy'),
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'wayfindinglib.drivers.simulators.indi_simulator',

        'photutils.geometry.core',
        'photutils.geometry.circular_overlap',
        'photutils.geometry.rectangular_overlap',
        'email',
        'pydoc',
        'http.server',
        'xmlrpc.server'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True, # Keep console for now to see logs
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='backend',
)
