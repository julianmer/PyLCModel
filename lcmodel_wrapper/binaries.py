####################################################################################################
#                                           binaries.py                                            #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 26/06/26                                                                                #
#                                                                                                  #
# Purpose: Resolution of the LCModel executable. The wrapper does NOT ship binaries; it resolves   #
#          one at run time in priority order: an explicit "path2exec", a previously cached         #
#          download/build, a download of the matching community binary from                        #
#          github.com/schorschinho/LCModel, or a build from source via "gfortran".                 #
#                                                                                                  #
# LCModel itself is a separate BSD-3-Clause program by Stephen Provencher (see LICENSE.lcmodel).   #
#                                                                                                  #
####################################################################################################

import lzma
import os
import platform
import shutil
import stat
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional


#*****************************#
#   upstream binary registry  #
#*****************************#
_RAW_BASE = "https://raw.githubusercontent.com/schorschinho/LCModel/main"

# (relative path in upstream repo, compression, output executable name)
_BINARIES = {
    "linux-x86_64":   ("binaries/linux/lcmodel.xz", "xz", "lcmodel"),
    "darwin-arm64":   ("binaries/macos/sequoia/m4/lcmodel.zip", "zip", "lcmodel"),
    "darwin-arm64-monterey": ("binaries/macos/monterey/m1/lcmodel.zip", "zip", "lcmodel"),
    "darwin-x86_64":  ("binaries/macos/catalina/intel/lcmodel.zip", "zip", "lcmodel"),
    "windows-amd64":  ("binaries/win/win10/LCModel.exe.zip", "zip", "LCModel.exe"),
}

_SOURCE_URL = f"{_RAW_BASE}/source/LCModel.f"


#*********************#
#   platform helpers  #
#*********************#
def _platform_keys():
    """Return an ordered list of candidate registry keys for the current platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        return ["linux-x86_64"]
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return ["darwin-arm64", "darwin-arm64-monterey"]
        return ["darwin-x86_64"]
    if system == "windows":
        return ["windows-amd64"]
    return []


def _cache_dir() -> Path:
    override = os.environ.get("LCMODEL_CACHE_DIR")
    if override:
        base = Path(override)
    elif platform.system().lower() == "windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "lcmodel_wrapper"
    else:
        base = Path.home() / ".cache" / "lcmodel_wrapper"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _make_executable(path: Path):
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _exec_name() -> str:
    return "LCModel.exe" if platform.system().lower() == "windows" else "lcmodel"


#******************#
#   download path  #
#******************#
def _download(url: str, dest: Path):
    print(f"[lcmodel_wrapper] Downloading LCModel binary from {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "lcmodel_wrapper"})
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _download_binary(cache: Path) -> Optional[Path]:
    target = cache / _exec_name()

    last_err = None
    for key in _platform_keys():
        rel, comp, out_name = _BINARIES[key]
        url = f"{_RAW_BASE}/{rel}"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                archive = tmp / os.path.basename(rel)
                _download(url, archive)

                if comp == "zip":
                    with zipfile.ZipFile(archive) as zf:
                        members = [m for m in zf.namelist() if not m.endswith("/")]
                        # pick the executable-looking member
                        member = next(
                            (m for m in members if os.path.basename(m) == out_name),
                            members[0] if members else None,
                        )
                        if member is None:
                            raise RuntimeError(f"Empty archive: {url}")
                        with zf.open(member) as src, open(target, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                elif comp == "xz":
                    with lzma.open(archive) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                else:
                    shutil.copy2(archive, target)

            _make_executable(target)
            return target
        except Exception as err:   # try the next candidate (e.g. M1 fallback)
            last_err = err
            print(f"[lcmodel_wrapper] Could not fetch {url}: {err}")

    if last_err is not None:
        print(f"[lcmodel_wrapper] Binary download failed: {last_err}")
    return None


#***************#
#   build path  #
#***************#
def _build_from_source(cache: Path) -> Optional[Path]:
    if shutil.which("gfortran") is None:
        print("[lcmodel_wrapper] Cannot build LCModel: 'gfortran' not found on PATH.")
        return None

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "LCModel.f"   # the source is fetched on demand
        try:
            _download(_SOURCE_URL, src)
        except Exception as err:
            print(f"[lcmodel_wrapper] Could not download LCModel source: {err}")
            return None

        system = platform.system().lower()
        target = cache / _exec_name()
        try:
            if system == "windows":
                subprocess.run(
                    ["gfortran", "-ffpe-summary=none", "-std=legacy", "-O3",
                     str(src), "-o", str(target)],
                    check=True,
                )
            elif system == "darwin":
                obj = tmp / "LCModel.o"
                subprocess.run(
                    ["gfortran", "-c", "-fno-backslash", "-fno-f2c", "-O3",
                     "-fall-intrinsics", "-std=legacy", "-ffpe-summary=none",
                     str(src), "-o", str(obj)],
                    check=True,
                )
                subprocess.run(["gfortran", str(obj), "-o", str(target)], check=True)
            else:  # linux
                subprocess.run(
                    ["gfortran", "-ffpe-summary=none", "-std=legacy", "-O3",
                     str(src), "-o", str(target)],
                    check=True,
                )
        except subprocess.CalledProcessError as err:
            print(f"[lcmodel_wrapper] LCModel build failed: {err}")
            return None

    if target.is_file():
        _make_executable(target)
        return target
    return None


#*********************#
#   public resolver   #
#*********************#
def resolve_executable(path2exec: Optional[str] = None,
                       allow_download: bool = True,
                       allow_build: bool = True,
                       cache_dir: Optional[str] = None) -> str:
    """Resolve a usable LCModel executable and return its absolute path.

    Resolution order: explicit path -> cached -> download -> build.
    Raises "RuntimeError" if no executable can be obtained.
    """
    # 1. explicit user path
    if path2exec:
        p = Path(path2exec).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"path2exec does not exist: {p}")
        try:
            _make_executable(p)
        except PermissionError:
            pass
        return str(p.resolve())

    cache = Path(cache_dir) if cache_dir else _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)

    # 2. cached
    cached = cache / _exec_name()
    if cached.is_file():
        _make_executable(cached)
        return str(cached.resolve())

    # 3. download
    if allow_download:
        got = _download_binary(cache)
        if got is not None:
            return str(got.resolve())

    # 4. build
    if allow_build:
        built = _build_from_source(cache)
        if built is not None:
            return str(built.resolve())

    raise RuntimeError(
        "Could not resolve an LCModel executable.\n"
        f"  platform: {platform.system()} {platform.machine()}\n"
        "Options:\n"
        "  - pass path2exec='/path/to/lcmodel' to PyLCModel,\n"
        "  - ensure internet access so the matching binary can be downloaded from\n"
        "    https://github.com/schorschinho/LCModel,\n"
        "  - or install 'gfortran' so it can be built from source.\n"
        f"(detail: download={allow_download}, build={allow_build}, "
        f"gfortran={'yes' if shutil.which('gfortran') else 'no'})"
    )
