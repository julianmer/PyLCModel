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
#          github.com/schorschinho/LCModel, a download of the CI-built binary attached to a        #
#          PyLCModel release, a container image (docker/podman) behind a launcher script that      #
#          behaves like a native executable (see container.py), or a build from source via         #
#          "gfortran".                                                                             #
#                                                                                                  #
#          Every candidate is exercised before it is accepted (see "verify_executable"), so a      #
#          wrong-architecture download can never be cached and served forever.                     #
#                                                                                                  #
# LCModel itself is a separate BSD-3-Clause program by Stephen Provencher (see LICENSE.lcmodel).   #
#                                                                                                  #
####################################################################################################

import hashlib
import lzma
import os
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ._version import __version__


#*****************************#
#   upstream binary registry  #
#*****************************#
_RAW_BASE = "https://raw.githubusercontent.com/schorschinho/LCModel/main"

# (relative path in upstream repo, compression, output executable name)
_BINARIES = {
    "linux-x86_64":   ("binaries/linux/lcmodel.xz", "xz", "lcmodel"),
    # Upstream has no aarch64 Linux build yet. The entry is kept so that the moment one
    # lands it is used with no release on our side; until then the fetch 404s and the
    # PyLCModel release below covers it. The directory name is what "uname -m" reports,
    # matching the path the upstream Makefile derives.
    "linux-aarch64":  ("binaries/linux/aarch64/lcmodel.xz", "xz", "lcmodel"),
    "darwin-arm64":   ("binaries/macos/sequoia/m4/lcmodel.zip", "zip", "lcmodel"),
    "darwin-arm64-monterey": ("binaries/macos/monterey/m1/lcmodel.zip", "zip", "lcmodel"),
    "darwin-x86_64":  ("binaries/macos/catalina/intel/lcmodel.zip", "zip", "lcmodel"),
    "windows-amd64":  ("binaries/win/win10/LCModel.exe.zip", "zip", "LCModel.exe"),
}

# LCModel.f alone does not compile: it pulls in six include files that live beside it in
# the upstream "source/" directory.
_SOURCE_FILES = (
    "LCModel.f",
    "lcmodel.inc",
    "lipid-1.inc",
    "liver-1.inc",
    "muscle-1.inc",
    "nml_lcmodel.inc",
    "nml_lcmodl.inc",
)
_SOURCE_BASE = f"{_RAW_BASE}/source"


#*******************************#
#   PyLCModel release registry  #
#*******************************#
# Binaries compiled in this repository's CI (.github/workflows/lcmodel-binaries.yml) from
# the pinned upstream source and attached to a GitHub release. They exist for the gaps
# upstream leaves open - no aarch64 Linux build, and macOS builds that dynamically link
# the builder's libgfortran and so only run on machines with the same Homebrew setup.
# Ours are linked statically (fully on Linux; libgfortran/libgcc on macOS).
#
# Only ever tried after the upstream registry, so the moment upstream ships an equivalent
# file this table is silently bypassed.
#
# The release is the one tagged with this package's own version ("v0.3.0"), so a wheel
# always pairs with the artifacts built alongside it and old wheels keep working.
_RELEASE_REPO = "julianmer/PyLCModel"
_DEFAULT_RELEASE_TAG = f"v{__version__}"

# registry key -> asset name. Every asset is an xz-compressed raw executable with a
# "<asset>.sha256" sidecar produced by the same CI run.
_RELEASE_ASSETS = {
    "linux-x86_64":  "lcmodel-linux-x86_64.xz",
    "linux-aarch64": "lcmodel-linux-aarch64.xz",
    "darwin-arm64":  "lcmodel-macos-arm64.xz",
    "darwin-x86_64": "lcmodel-macos-x86_64.xz",
}


def _release_tag() -> str:
    return os.environ.get("LCMODEL_RELEASE_TAG", _DEFAULT_RELEASE_TAG)


def _release_base() -> str:
    return f"https://github.com/{_RELEASE_REPO}/releases/download/{_release_tag()}"


#*********************#
#   container image   #
#*********************#
# The same CI pushes the Linux binary as a multi-arch image (linux/amd64, linux/arm64).
# On a host with docker or podman this is the one artifact that runs identically on every
# Linux and every Apple-silicon generation, because inside the container it is always the
# same statically linked Linux binary - no macOS SDK, Homebrew or Gatekeeper involved.
_DEFAULT_CONTAINER_IMAGE = f"ghcr.io/{_RELEASE_REPO.split('/')[0]}/lcmodel"

# Name of the cached launcher script - two lines calling lcmodel_wrapper.container.
# Distinct from the native executable name so the two never compete for one cache slot.
_SHIM_NAME = "lcmodel-container.cmd" if os.name == "nt" else "lcmodel-container"
_SHIM_NAMES = ("lcmodel-container", "lcmodel-container.cmd")

_PULL_TIMEOUT = float(os.environ.get("LCMODEL_PULL_TIMEOUT", "900"))


def _container_image() -> str:
    return os.environ.get("LCMODEL_DOCKER_IMAGE", f"{_DEFAULT_CONTAINER_IMAGE}:{_release_tag()}")


#*********************#
#   platform helpers  #
#*********************#
_MACHINE_ALIASES = {
    "x86_64": "x86_64", "amd64": "x86_64", "x64": "x86_64",
    "i386": "x86", "i686": "x86",
    "aarch64": "arm64", "arm64": "arm64", "armv8b": "arm64", "armv8l": "arm64",
}


def _normalized_machine() -> str:
    """Map the many spellings of a CPU architecture onto a canonical name."""
    machine = platform.machine().lower()
    return _MACHINE_ALIASES.get(machine, machine)


def _platform_keys() -> List[str]:
    """Return an ordered list of candidate registry keys for the current platform.

    An empty list means "no prebuilt binary applies" - resolution then falls through to
    the source build rather than downloading something that cannot run here.
    """
    system = platform.system().lower()
    machine = _normalized_machine()

    if system == "linux":
        if machine == "x86_64":
            return ["linux-x86_64"]
        if machine == "arm64":
            return ["linux-aarch64"]
        return []
    if system == "darwin":
        if machine == "arm64":
            # The Intel build is a last resort via Rosetta 2; verification rejects it
            # cleanly if Rosetta is not installed.
            return ["darwin-arm64", "darwin-arm64-monterey", "darwin-x86_64"]
        return ["darwin-x86_64"]
    if system == "windows":
        if machine in ("x86_64", "arm64"):   # Windows on ARM emulates x64
            return ["windows-amd64"]
        return []
    return []


def _cache_root() -> Path:
    override = os.environ.get("LCMODEL_CACHE_DIR")
    if override:
        base = Path(override)
    elif platform.system().lower() == "windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "lcmodel_wrapper"
    else:
        base = Path.home() / ".cache" / "lcmodel_wrapper"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _cache_dir() -> Path:
    """Per-architecture cache directory.

    Keyed by architecture so a home directory shared across a mixed-architecture cluster
    (an x86_64 login node and aarch64 compute nodes, say) does not have both fighting
    over one file.
    """
    base = _cache_root() / f"{platform.system().lower()}-{_normalized_machine()}"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _make_executable(path: Path):
    st = os.stat(path)
    os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _exec_name() -> str:
    return "LCModel.exe" if platform.system().lower() == "windows" else "lcmodel"


def _install(src: Path, cache: Path) -> Path:
    """Move a finished binary into the cache atomically.

    Callers build or extract into a temporary location first, so an interrupted download
    or a failed compile can never leave a truncated file at the cached path where the
    next run would pick it up.
    """
    target = cache / _exec_name()
    _make_executable(src)
    os.replace(src, target)
    return target


def _quarantine(path: Path, reason: str = "unknown") -> Optional[Path]:
    """Move a rejected binary aside so the next provider is not shadowed by it.

    Renamed rather than deleted: the artifact stays available for debugging, and a
    concurrent process losing the race must not crash.
    """
    try:
        dest_dir = _cache_root() / "quarantine"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{Path(path).name}-{reason}-{time.time_ns()}"
        os.replace(path, dest)
        print(f"[lcmodel_wrapper] Quarantined unusable binary at {dest}")
        return dest
    except (OSError, PermissionError):
        return None


#***********************#
#   health verification #
#***********************#
# LCModel writes a diagnostic block to stdout whenever it cannot parse its control
# namelist, which is exactly what happens when we feed it an empty one.
_HEALTH_MARKERS = (b"MYCONT", b"LCModel")

_VERIFY_TIMEOUT = float(os.environ.get("LCMODEL_VERIFY_TIMEOUT", "60"))

# Keyed on (resolved path, mtime, size) so repeated PyLCModel() construction is free.
_VERIFIED: Dict[tuple, Tuple[bool, str]] = {}

_ELF_MACHINES = {0x03: "x86", 0x3E: "x86-64", 0x28: "ARM", 0xB7: "AArch64", 0xF3: "RISC-V"}
_MACHO_CPUS = {0x01000007: "x86_64", 0x0100000C: "arm64",
               0x00000007: "i386", 0x0000000C: "arm"}


def _describe_binary(path: Path) -> str:
    """Best-effort 'what is this file, really' for error messages only.

    Never used to accept or reject - purely so that a mismatch reports "file is ELF
    x86-64, host is Linux AArch64" instead of a bare "Exec format error".
    """
    host = f"host is {platform.system()} {platform.machine()}"
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return host
    if len(head) < 8:
        return f"file is not an executable ({len(head)} bytes); {host}"
    if head[:4] == b"\x7fELF":
        machine = int.from_bytes(head[18:20], "little")
        return f"file is ELF {_ELF_MACHINES.get(machine, hex(machine))}; {host}"
    if head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        cpu = int.from_bytes(head[4:8], "little")
        return f"file is Mach-O {_MACHO_CPUS.get(cpu, hex(cpu))}; {host}"
    if head[:4] == b"\xca\xfe\xba\xbe":
        return f"file is a Mach-O universal binary; {host}"
    if head[:2] == b"MZ":
        return f"file is a Windows PE executable; {host}"
    if head[:2] == b"#!":
        return f"file is a script ({head[:20].decode('ascii', 'replace').strip()}); {host}"
    return f"file is not a recognised executable format; {host}"


def verify_executable(path, timeout: Optional[float] = None) -> Tuple[bool, str]:
    """Check that "path" is a runnable LCModel binary. Returns (ok, reason).

    Runs the candidate with an empty control file on stdin and looks for LCModel's own
    diagnostic output. Cheap (a few ms once warm), writes nothing, and cannot hang.

    The return code is deliberately ignored: LCModel is Fortran and its fatal error paths
    end in a bare STOP, which exits 0. Keying on the exit status would accept /bin/true
    and reject nothing.
    """
    if os.environ.get("LCMODEL_SKIP_VERIFY"):
        return True, "skipped via LCMODEL_SKIP_VERIFY"

    path = Path(path).expanduser().resolve()   # the probe runs in a temporary directory
    try:
        st = path.stat()
        key = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    except OSError as err:
        return False, f"cannot stat: {err}"
    if key in _VERIFIED:
        return _VERIFIED[key]

    result = _probe(path, _VERIFY_TIMEOUT if timeout is None else timeout)
    _VERIFIED[key] = result
    return result


def _probe(path: Path, timeout: float) -> Tuple[bool, str]:
    # Three independent guards against hanging: stdin at EOF immediately (LCModel reads
    # its namelist until EOF and would otherwise block forever), a hard timeout, and a
    # throwaway working directory for any stray output.
    with tempfile.TemporaryDirectory() as cwd:
        try:
            proc = subprocess.run(
                [str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return False, f"did not exit within {timeout:.0f}s"
        except OSError as err:
            # Exec format error, bad CPU type, missing loader, permission denied.
            return False, f"cannot execute ({err}); {_describe_binary(path)}"

    out = proc.stdout or b""
    if any(marker in out for marker in _HEALTH_MARKERS):
        return True, "ok"
    if proc.returncode < 0:
        return False, (f"killed by signal {-proc.returncode} (binary may require CPU "
                       f"features this machine lacks); {_describe_binary(path)}")
    return False, (f"ran (exit {proc.returncode}) but produced no LCModel diagnostic; "
                   f"first output was {out[:120]!r}")


#******************#
#   download path  #
#******************#
def _download(url: str, dest: Path):
    print(f"[lcmodel_wrapper] Downloading LCModel binary from {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "lcmodel_wrapper"})
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def _extract(archive: Path, comp: str, out_name: str, staged: Path):
    if comp == "zip":
        with zipfile.ZipFile(archive) as zf:
            members = [m for m in zf.namelist() if not m.endswith("/")]
            # pick the executable-looking member
            member = next(
                (m for m in members if os.path.basename(m) == out_name),
                members[0] if members else None,
            )
            if member is None:
                raise RuntimeError(f"Empty archive: {archive.name}")
            with zf.open(member) as src, open(staged, "wb") as dst:
                shutil.copyfileobj(src, dst)
    elif comp == "xz":
        with lzma.open(archive) as src, open(staged, "wb") as dst:
            shutil.copyfileobj(src, dst)
    else:
        shutil.copy2(archive, staged)


def _fetch_and_install(url: str, comp: str, out_name: str, cache: Path,
                       sha256_url: Optional[str] = None) -> Path:
    """Download one archive, optionally verify it, extract it and install it."""
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        tmp = Path(tmp)
        archive = tmp / os.path.basename(url)
        staged = tmp / _exec_name()
        _download(url, archive)

        if sha256_url is not None:
            sidecar = tmp / (archive.name + ".sha256")
            _download(sha256_url, sidecar)
            expected = sidecar.read_text().split()[0].lower()
            actual = hashlib.sha256(archive.read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError(f"sha256 mismatch for {url}: got {actual}, "
                                   f"expected {expected}")

        _extract(archive, comp, out_name, staged)
        return _install(staged, cache)


def _download_binary(cache: Path) -> Optional[Path]:
    last_err = None
    for key in _platform_keys():
        rel, comp, out_name = _BINARIES[key]
        url = f"{_RAW_BASE}/{rel}"
        try:
            return _fetch_and_install(url, comp, out_name, cache)
        except Exception as err:   # try the next candidate (e.g. M1 fallback)
            last_err = err
            print(f"[lcmodel_wrapper] Could not fetch {url}: {err}")

    if last_err is not None:
        print(f"[lcmodel_wrapper] Binary download failed: {last_err}")
    return None


def _release_assets() -> List[str]:
    """Release asset names applicable to this host, in preference order, deduplicated."""
    names: List[str] = []
    for key in _platform_keys():
        asset = _RELEASE_ASSETS.get(key)
        if asset is not None and asset not in names:
            names.append(asset)
    return names


def _download_release_binary(cache: Path) -> Optional[Path]:
    """Fetch the CI-built binary attached to a PyLCModel release.

    Unlike the upstream files these carry a checksum sidecar, and it is enforced: a
    release asset is a plain URL anyone could re-upload, so the hash is what ties the
    file to the CI run that built and tested it.
    """
    assets = _release_assets()
    if not assets:
        return None
    base = _release_base()
    last_err = None
    for asset in assets:
        url = f"{base}/{asset}"
        try:
            return _fetch_and_install(url, "xz", _exec_name(), cache,
                                      sha256_url=f"{url}.sha256")
        except Exception as err:
            last_err = err
            print(f"[lcmodel_wrapper] Could not fetch {url}: {err}")

    if last_err is not None:
        print(f"[lcmodel_wrapper] Release binary download failed: {last_err}")
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
        # LCModel.f INCLUDEs six other files; they must sit beside it or every INCLUDE
        # fails at the first line of the compile.
        for name in _SOURCE_FILES:
            try:
                _download(f"{_SOURCE_BASE}/{name}", tmp / name)
            except Exception as err:
                print(f"[lcmodel_wrapper] Could not download LCModel source {name}: {err}")
                return None

        system = platform.system().lower()
        staged = tmp / _exec_name()
        print("[lcmodel_wrapper] Compiling LCModel from source (this takes a few minutes)")
        try:
            if system == "windows":
                # Upstream documents a single-step compile on Windows.
                subprocess.run(
                    ["gfortran", "-ffpe-summary=none", "-std=legacy", "-O3",
                     "LCModel.f", "-o", str(staged)],
                    check=True, cwd=tmp,
                )
            else:
                # Flag set from the upstream Makefile / README, for both Linux and macOS.
                subprocess.run(
                    ["gfortran", "-c", "-fno-backslash", "-fno-f2c", "-O3",
                     "-fall-intrinsics", "-std=legacy", "-Wuninitialized",
                     "-ffpe-summary=none", "LCModel.f", "-o", "LCModel.o"],
                    check=True, cwd=tmp,
                )
                subprocess.run(
                    ["gfortran", "LCModel.o", "-o", str(staged)],
                    check=True, cwd=tmp,
                )
        except subprocess.CalledProcessError as err:
            print(f"[lcmodel_wrapper] LCModel build failed: {err}")
            return None

        if not staged.is_file():
            return None
        return _install(staged, cache)


#*******************#
#   container path  #
#*******************#
# The launcher is a two-line script that calls lcmodel_wrapper.container with the engine
# and image baked in; every mount and path decision lives in that module. From the
# wrapper's side the script is just another LCModel binary - control file on stdin,
# outputs at the paths named in it - and verify_executable() probes it like an ELF.
def _container_cli() -> Optional[str]:
    for name in ("docker", "podman"):
        if shutil.which(name):
            return name
    return None


def _engine(cli: str, *args: str, timeout: float) -> Tuple[int, str]:
    """Run a container-engine command. Returns (exit code, last stderr line); the code
    is -1 when the command could not run or time out."""
    try:
        proc = subprocess.run([cli, *args], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, f"'{cli} {args[0]}' did not finish within {timeout:.0f}s"
    except OSError as err:
        return -1, f"cannot run '{cli}': {err}"
    lines = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    return proc.returncode, lines[-1] if lines else f"'{cli} {args[0]}' exited {proc.returncode}"


def _container_ready(cli: str, timeout: float = 30.0) -> Tuple[bool, str]:
    """Is the engine actually usable - daemon running, socket reachable, permissions ok?"""
    code, why = _engine(cli, "info", timeout=timeout)
    return code == 0, "ok" if code == 0 else why


def is_container_shim(path) -> bool:
    """True if "path" is a launcher generated here rather than a native LCModel binary."""
    return Path(path).name in _SHIM_NAMES


def _write_shim(cache: Path, cli: str, image: str) -> Path:
    # "-c" rather than "-m lcmodel_wrapper.container": the package __init__ already
    # imports that module, and runpy warns when asked to execute an imported module.
    entry = "import sys; from lcmodel_wrapper.container import main; sys.exit(main())"
    python = sys.executable
    if os.name == "nt":
        script = f'@"{python}" -c "{entry}" --cli {cli} --image {image}\r\n'
    else:
        script = (f"#!/bin/sh\nexec {shlex.quote(python)} -c {shlex.quote(entry)} "
                  f"--cli {shlex.quote(cli)} --image {shlex.quote(image)}\n")

    target = cache / _SHIM_NAME
    fd, tmp = tempfile.mkstemp(dir=cache, prefix=".shim-")
    with os.fdopen(fd, "w", newline="") as fh:
        fh.write(script)
    _make_executable(Path(tmp))
    os.replace(tmp, target)
    return target


def _container_shim(cache: Path) -> Optional[Path]:
    if os.environ.get("LCMODEL_NO_DOCKER"):
        return None
    cli = _container_cli()
    if cli is None:
        print("[lcmodel_wrapper] Cannot use a container: neither 'docker' nor 'podman' "
              "found on PATH.")
        return None
    ok, why = _container_ready(cli)
    if not ok:
        print(f"[lcmodel_wrapper] Container engine '{cli}' is not usable: {why}")
        return None

    image = _container_image()
    # already in the local store (pulled earlier, or built by hand): no network needed,
    # which keeps this rung working offline
    if _engine(cli, "image", "inspect", image, timeout=30)[0] != 0:
        print(f"[lcmodel_wrapper] Pulling LCModel container image {image}")
        code, why = _engine(cli, "pull", image, timeout=_PULL_TIMEOUT)
        if code != 0:
            print(f"[lcmodel_wrapper] Could not pull {image}: {why} "
                  f"(LCMODEL_PULL_TIMEOUT raises the {_PULL_TIMEOUT:.0f}s bound)")
            return None

    return _write_shim(cache, cli, image)


#*********************#
#   cache management  #
#*********************#
def _cached_binary(cache: Path) -> Optional[Path]:
    cached = cache / _exec_name()
    if cached.is_file():
        return cached

    # A shim left by an earlier resolution means the native sources all failed on this
    # machine last time; prefer it over re-downloading and re-rejecting them. If the
    # engine is simply not running right now, leave the shim in place and report
    # "unavailable" instead of quarantining a perfectly good script.
    shim = cache / _SHIM_NAME
    if shim.is_file() and not os.environ.get("LCMODEL_NO_DOCKER"):
        cli = _container_cli()
        if cli is None:
            return None
        ok, why = _container_ready(cli)
        if not ok:
            print(f"[lcmodel_wrapper] Cached container shim skipped: {why}")
            return None
        return shim
    return None


def _migrate_legacy_cache(cache: Path):
    """Adopt a pre-existing binary from the old, architecture-agnostic cache layout.

    Earlier versions cached at "<root>/lcmodel". Move that into the per-architecture
    directory rather than making everyone re-download 100+ MB - but only if it actually
    runs here, since the old layout is exactly how a wrong-architecture binary got stuck.
    """
    legacy = _cache_root() / _exec_name()
    if not legacy.is_file() or (cache / _exec_name()).is_file():
        return
    ok, why = verify_executable(legacy)
    if ok:
        try:
            os.replace(legacy, cache / _exec_name())
            print(f"[lcmodel_wrapper] Adopted cached LCModel binary into {cache}")
        except OSError:
            pass
    else:
        print(f"[lcmodel_wrapper] Discarding cached binary from the old cache layout: {why}")
        _quarantine(legacy, reason="legacy")


#*********************#
#   public resolver   #
#*********************#
def resolve_executable(path2exec: Optional[str] = None,
                       allow_download: bool = True,
                       allow_build: bool = True,
                       cache_dir: Optional[str] = None,
                       verify: bool = True,
                       allow_docker: bool = True) -> str:
    """Resolve a usable LCModel executable and return its absolute path.

    Resolution order: explicit path -> cached -> upstream download -> release download
    -> container shim -> build. Every candidate is run once before being accepted, and
    anything that fails is quarantined so the next source gets a turn instead of being
    shadowed by a broken file.

    The container rung sits before the source build because pulling a small image is
    seconds where compiling LCModel.f is minutes and needs a toolchain - but after the
    native downloads, because a native binary needs no engine running.

    Raises "RuntimeError" if no executable can be obtained.
    """
    # 1. explicit user path - verified, but never silently replaced. The environment
    #    variable is the same thing for code that does not pass the argument (CI
    #    pointing the test suite at a freshly built binary, a cluster-wide install).
    path2exec = path2exec or os.environ.get("LCMODEL_EXEC")
    if path2exec:
        p = Path(path2exec).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"path2exec does not exist: {p}")
        try:
            _make_executable(p)
        except PermissionError:
            pass
        if verify:
            ok, why = verify_executable(p)
            if not ok:
                if why.startswith(("cannot execute", "killed by signal")):
                    raise RuntimeError(
                        f"path2exec is not a runnable LCModel binary: {p}\n  {why}"
                    )
                # It ran but did not identify itself - could legitimately be a wrapper
                # script, so defer to the user who named it explicitly.
                print(f"[lcmodel_wrapper] Warning: {p} did not identify itself as LCModel "
                      f"({why}); using it anyway because it was passed explicitly.")
        return str(p.resolve())

    cache = Path(cache_dir) if cache_dir else _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    if cache_dir is None:
        _migrate_legacy_cache(cache)

    providers: List[Tuple[str, Callable[[], Optional[Path]]]] = [
        ("cached", lambda: _cached_binary(cache)),
    ]
    if allow_download:
        providers.append(("download", lambda: _download_binary(cache)))
        providers.append(("release", lambda: _download_release_binary(cache)))
    if allow_docker:
        providers.append(("container", lambda: _container_shim(cache)))
    if allow_build:
        providers.append(("build", lambda: _build_from_source(cache)))

    failures = []
    for name, provide in providers:
        try:
            got = provide()
        except Exception as err:
            failures.append(f"{name}: {err}")
            continue
        if got is None:
            failures.append(f"{name}: unavailable")
            continue
        if verify:
            ok, why = verify_executable(got)
            if not ok:
                print(f"[lcmodel_wrapper] Rejected LCModel binary from '{name}': {why}")
                _quarantine(Path(got), reason=name)
                failures.append(f"{name}: {why}")
                continue
        return str(Path(got).resolve())

    raise RuntimeError(_resolution_error(failures, allow_download, allow_docker, allow_build))


def _resolution_error(failures: List[str], allow_download: bool, allow_docker: bool,
                      allow_build: bool) -> str:
    detail = "\n".join(f"    - {f}" for f in failures) or "    - no sources were tried"
    engine = _container_cli()
    return (
        "Could not resolve a working LCModel executable.\n"
        f"  platform: {platform.system()} {platform.machine()}\n"
        f"  cache:    {_cache_dir()}\n"
        "  tried:\n"
        f"{detail}\n"
        "Options:\n"
        "  - pass path2exec='/path/to/lcmodel' to PyLCModel,\n"
        "  - ensure internet access so the matching binary can be downloaded from\n"
        f"    https://github.com/schorschinho/LCModel or the {_release_tag()} release of\n"
        f"    https://github.com/{_RELEASE_REPO},\n"
        "  - install and start Docker (or podman) so the LCModel container image\n"
        f"    {_container_image()} can be used,\n"
        "  - or install 'gfortran' so it can be built from source.\n"
        f"(detail: download={allow_download}, docker={allow_docker}, build={allow_build}, "
        f"engine={engine or 'no'}, gfortran={'yes' if shutil.which('gfortran') else 'no'})"
    )
