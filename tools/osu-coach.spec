# PyInstaller build of the app (tools/build-setup.ps1 runs it): dist\osu-coach\osu-coach.exe and its libraries,
# no console window. The page (osu_coach/ui) and the fallback hitsounds (osu_coach/sounds) go next to the code, where
# gui.py and skin.py look for them.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "tools" / "osu_coach_app.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "osu_coach" / "ui"), "osu_coach/ui"), (str(ROOT / "osu_coach" / "sounds"), "osu_coach/sounds")],
    # the commands import their modules as they run
    hiddenimports=collect_submodules("osu_coach"),
    # scikit-learn and scipy only train the map type guesser: the app runs its lite copy (typeguess.to_lite)
    excludes=["sklearn", "scipy", "joblib", "threadpoolctl", "matplotlib", "pandas", "PIL", "IPython", "pytest",
              "PyInstaller", "setuptools", "pip", "pydoc", "unittest", "numpy.f2py", "numpy.distutils"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="osu-coach",
    icon=str(ROOT / "osu_coach" / "ui" / "icon.ico"),
    console=False,
    upx=False,          # packed exes look suspicious to antivirus programs
)
coll = COLLECT(exe, a.binaries, a.datas, name="osu-coach", upx=False)
