####################################################################################################
#                                          container.py                                            #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 09/09/26                                                                                #
#                                                                                                  #
# Purpose: Run LCModel from a container image as if it were a native executable.                  #
#                                                                                                  #
#          The wrapper talks to LCModel in exactly one way: control file on stdin, output files    #
#          at the absolute paths named in that control file. So a launcher that forwards stdin     #
#          into "docker run" and makes the host paths valid inside the container is, from the      #
#          wrapper's point of view, just another LCModel binary - it is even health-checked by     #
#          the same probe. binaries.py caches a two-line script that calls this module's main().   #
#                                                                                                  #
#          Linux/macOS: the working directory and the home directory are bind-mounted at           #
#          identical paths, so the control file needs no translation.                              #
#          Windows:     a Linux container cannot have a path "C:\...", so each drive is mounted    #
#                       at /host/<LETTER> and the wrapper rewrites the file paths in the control   #
#                       file to match (translate_control). Because the wrapper writes every path   #
#                       in that file itself, that is the whole of the translation.                 #
#                                                                                                  #
####################################################################################################

import argparse
import ntpath
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# Where the Windows launcher mounts each drive: C:\Users\me -> /host/C/Users/me
WIN_MOUNT_ROOT = "/host"


#*********************#
#   path translation  #
#*********************#
def windows_to_container(path: str) -> str:
    r"""C:\Users\me\x.basis -> /host/C/Users/me/x.basis. Pure, so it is testable anywhere."""
    drive, rest = ntpath.splitdrive(ntpath.normpath(path))
    if len(drive) != 2 or drive[1] != ":":
        raise ValueError(f"{path}: only drive-letter paths are visible inside the container "
                         f"(UNC paths are not mounted)")
    return f"{WIN_MOUNT_ROOT}/{drive[0].upper()}{rest.replace(chr(92), '/')}"


def to_container(path) -> str:
    """A host path as LCModel sees it inside the container: identity on POSIX (identical
    mounts), drive letter mapped under /host on Windows."""
    if os.name == "nt":
        return windows_to_container(os.path.abspath(str(path)))
    return str(path)


def translate_control(lines: List[str],
                      mapper: Callable[[str], str] = to_container) -> List[str]:
    """Rewrite every file path in a control file for the container.

    LCModel's file keys all start with "fil" (filbas, filraw, filps, filcoo, filh2o,
    filtab, filcsv, ...). Values are quoted; the quoting is preserved. Returns a new
    list - the wrapper keeps reading its outputs at the host paths in the original.
    """
    out = []
    for line in lines:
        key, sep, value = line.partition("=")
        if not sep or not key.strip().lower().startswith("fil"):
            out.append(line)
            continue
        v = value.strip()
        quoted = len(v) >= 2 and v[0] in ("'", '"') and v[-1] == v[0]
        inner = v[1:-1] if quoted else v
        out.append(f"{key}={v[0]}{mapper(inner)}{v[0]}" if quoted else f"{key}={mapper(inner)}")
    return out


#**********************#
#   mount computation  #
#**********************#
def posix_mounts(cwd: str, home: Optional[str]) -> List[Tuple[str, str]]:
    """(host, container) pairs: the working directory, its physical path if that differs
    (a symlinked cwd is reachable under both spellings), and the home directory unless
    the cwd already lies inside it - docker refuses duplicate mount points."""
    mounts = [(cwd, cwd)]
    phys = os.path.realpath(cwd)
    if phys != cwd:
        mounts.append((phys, phys))
    if home and not (cwd == home or cwd.startswith(home.rstrip(os.sep) + os.sep)):
        mounts.append((home, home))
    return mounts


def windows_mounts(cwd: str, home: Optional[str]) -> List[Tuple[str, str]]:
    """The drive of the working directory, plus the drive of the home directory."""
    drives = [ntpath.splitdrive(cwd)[0].upper()]
    if home:
        d = ntpath.splitdrive(home)[0].upper()
        if d and d not in drives:
            drives.append(d)
    return [(f"{d}\\", f"{WIN_MOUNT_ROOT}/{d[0]}") for d in drives if len(d) == 2]


def can_see(path) -> bool:
    """Would the launcher's mounts make "path" visible inside the container? Lets the
    wrapper fail early with a clear message instead of LCModel reporting a missing file."""
    cwd, home = os.getcwd(), str(Path.home())
    if os.name == "nt":
        drive = ntpath.splitdrive(os.path.abspath(str(path)))[0].upper()
        return any(host.startswith(drive) for host, _ in windows_mounts(cwd, home))
    p = Path(path).resolve()
    roots = {Path(host).resolve() for host, _ in posix_mounts(cwd, home)}
    roots.add(Path(os.environ.get("PWD", cwd)).resolve())
    return any(p == root or root in p.parents for root in roots)


#********************#
#   engine command   #
#********************#
def run_command(cli: str, image: str, cwd: Optional[str] = None,
                home: Optional[str] = None, windows: Optional[bool] = None,
                uid: Optional[Tuple[int, int]] = None) -> List[str]:
    """The full "docker run ..." argument list. Pure given its inputs."""
    cwd = cwd or os.getcwd()
    home = home if home is not None else str(Path.home())
    windows = os.name == "nt" if windows is None else windows

    cmd = [cli, "run", "--rm", "-i"]
    if windows:
        mounts = windows_mounts(cwd, home)
        workdir = windows_to_container(cwd)
    else:
        mounts = posix_mounts(cwd, home)
        workdir = cwd
        # output files must come back owned by the caller, not root
        if cli == "podman":
            cmd.append("--userns=keep-id")
        elif uid is not None:
            cmd += ["-u", f"{uid[0]}:{uid[1]}"]
    for host, inside in mounts:
        cmd += ["-v", f"{host}:{inside}"]
    cmd += ["-w", workdir, image]
    return cmd


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run LCModel from a container image.")
    parser.add_argument("--cli", required=True, help="docker or podman")
    parser.add_argument("--image", required=True)
    args = parser.parse_args(argv)

    image = os.environ.get("LCMODEL_DOCKER_IMAGE", args.image)
    uid = (os.getuid(), os.getgid()) if hasattr(os, "getuid") else None
    cmd = run_command(args.cli, image, uid=uid)
    if os.name == "nt":
        return subprocess.call(cmd)
    os.execvp(cmd[0], cmd)   # stdin, stdout and exit status pass straight through
    return 1                 # not reached


if __name__ == "__main__":
    sys.exit(main())
