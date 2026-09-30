# PyInstaller build of the app (tools/build-setup.ps1 runs it): dist\osu-coach\osu-coach.exe and its libraries,
# no console window. The page (osu_coach/ui) and the fallback hitsounds (osu_coach/sounds) go next to the code, where
# gui.py and skin.py look for them.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH).parent
VERSION = next(line.split('"')[1] for line in (ROOT / "osu_coach" / "__init__.py").read_text().splitlines()
               if line.startswith("__version__"))
NUMS = tuple((list(map(int, VERSION.split("."))) + [0, 0, 0, 0])[:4])
# the name Task Manager shows for the process, and the file's details
VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMS, prodvers=NUMS),
    kids=[StringFileInfo([StringTable("040904B0", [
              StringStruct("FileDescription", "osu!coach"), StringStruct("ProductName", "osu!coach"),
              StringStruct("FileVersion", VERSION), StringStruct("ProductVersion", VERSION),
              StringStruct("InternalName", "osu-coach"), StringStruct("OriginalFilename", "osu-coach.exe")])]),
          VarFileInfo([VarStruct("Translation", [1033, 1200])])])

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
    version=VERSION_INFO,
    console=False,
    upx=False,          # packed exes look suspicious to antivirus programs
)
coll = COLLECT(exe, a.binaries, a.datas, name="osu-coach", upx=False)
