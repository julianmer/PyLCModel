####################################################################################################
#                                        test_binaries.py                                          #
####################################################################################################
#                                                                                                  #
# Purpose: Tests for LCModel executable resolution - the health probe and platform detection.      #
#                                                                                                  #
#          These need no LCModel binary and no network. The one test that does use a real binary   #
#          skips itself when none is cached.                                                       #
#                                                                                                  #
####################################################################################################

import os
import platform
import shutil
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, _REPO)

from lcmodel_wrapper import binaries, container


#*************#
#   helpers   #
#*************#
def _make_elf(path, e_machine):
    """Write a minimal ELF64 header with the given e_machine, marked executable."""
    hdr = bytearray(64)
    hdr[0:4] = b"\x7fELF"
    hdr[4], hdr[5], hdr[6] = 2, 1, 1          # 64-bit, little endian, version 1
    hdr[16:18] = (2).to_bytes(2, "little")     # ET_EXEC
    hdr[18:20] = e_machine.to_bytes(2, "little")
    path.write_bytes(bytes(hdr))
    os.chmod(path, 0o755)
    return path


def _cached_binary():
    path = binaries._cache_dir() / binaries._exec_name()
    if path.is_file():
        return path
    legacy = binaries._cache_root() / binaries._exec_name()
    return legacy if legacy.is_file() else None


#**********************#
#   the health probe   #
#**********************#
def test_probe_rejects_a_program_that_exits_zero(tmp_path):
    """The whole point of the probe: LCModel STOPs with exit 0 on fatal errors, so a
    return-code check would accept anything that runs. Content is the only signal."""
    ok, why = binaries.verify_executable(sys.executable)
    assert ok is False
    assert "no LCModel diagnostic" in why


def test_probe_rejects_wrong_architecture(tmp_path):
    """The ARM-Linux failure: an x86_64 ELF downloaded onto an aarch64 host."""
    ok, why = binaries.verify_executable(_make_elf(tmp_path / "lcmodel", 0x3E))
    assert ok is False
    assert "cannot execute" in why
    assert "x86-64" in why          # names the mismatch instead of "Exec format error"
    assert platform.machine() in why or platform.system() in why


def test_probe_describes_aarch64_too(tmp_path):
    ok, why = binaries.verify_executable(_make_elf(tmp_path / "lcmodel", 0xB7))
    assert ok is False
    assert "AArch64" in why


def test_probe_rejects_non_executable_content(tmp_path):
    path = tmp_path / "lcmodel"
    path.write_text("this is not a binary\n")
    os.chmod(path, 0o755)
    ok, why = binaries.verify_executable(path)
    assert ok is False


def test_probe_times_out_instead_of_hanging(tmp_path):
    """LCModel reads its namelist until EOF, so a probe that gets stdin wrong blocks
    forever. Verify the timeout actually fires."""
    if os.name == "nt":
        pytest.skip("POSIX shell script")
    path = tmp_path / "slowpoke"
    path.write_text("#!/bin/sh\nsleep 30\n")
    os.chmod(path, 0o755)
    ok, why = binaries.verify_executable(path, timeout=1.0)
    assert ok is False
    assert "did not exit" in why


def test_probe_reports_missing_file(tmp_path):
    ok, why = binaries.verify_executable(tmp_path / "does-not-exist")
    assert ok is False
    assert "cannot stat" in why


def test_probe_memoizes(tmp_path):
    path = _make_elf(tmp_path / "lcmodel", 0x3E)
    first = binaries.verify_executable(path)
    assert binaries.verify_executable(path) == first
    st = path.stat()
    assert (str(path.resolve()), st.st_mtime_ns, st.st_size) in binaries._VERIFIED


def test_probe_honours_skip_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LCMODEL_SKIP_VERIFY", "1")
    ok, why = binaries.verify_executable(_make_elf(tmp_path / "lcmodel", 0x3E))
    assert ok is True
    assert "skipped" in why


@pytest.mark.skipif(_cached_binary() is None, reason="no cached LCModel binary")
def test_probe_accepts_the_real_binary():
    ok, why = binaries.verify_executable(_cached_binary())
    assert ok is True, why


def test_probe_accepts_a_relative_path(tmp_path, monkeypatch):
    """The probe runs the candidate in a temporary directory, so a path relative to the
    caller's directory (a relative cache_dir or LCMODEL_EXEC) has to be resolved first."""
    real = os.environ.get("LCMODEL_EXEC") or _cached_binary()
    if not real:
        pytest.skip("no LCModel binary to copy")
    (tmp_path / "bin").mkdir()
    shutil.copy2(real, tmp_path / "bin" / "lcmodel")
    monkeypatch.chdir(tmp_path)
    ok, why = binaries.verify_executable(os.path.join("bin", "lcmodel"))
    assert ok is True, why


#************************#
#   platform detection   #
#************************#
@pytest.mark.parametrize("system, machine, expected", [
    ("Linux",   "x86_64",  ["linux-x86_64"]),
    ("Linux",   "AMD64",   ["linux-x86_64"]),
    ("Linux",   "aarch64", ["linux-aarch64"]),
    ("Linux",   "arm64",   ["linux-aarch64"]),
    ("Linux",   "armv7l",  []),                 # falls through to the source build
    ("Darwin",  "arm64",   ["darwin-arm64", "darwin-arm64-monterey", "darwin-x86_64"]),
    ("Darwin",  "x86_64",  ["darwin-x86_64"]),
    ("Windows", "AMD64",   ["windows-amd64"]),
    ("Windows", "ARM64",   ["windows-amd64"]),  # emulates x64
    ("FreeBSD", "amd64",   []),
])
def test_platform_keys(monkeypatch, system, machine, expected):
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    assert binaries._platform_keys() == expected


def test_arm_linux_no_longer_gets_an_x86_binary(monkeypatch):
    """Regression: any Linux used to map to linux-x86_64, so aarch64 downloaded an
    x86_64 ELF, cached it, and failed with 'Exec format error' on every later run."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "aarch64")
    assert "linux-x86_64" not in binaries._platform_keys()


def test_every_registry_entry_is_reachable(monkeypatch):
    reachable = set()
    for system, machine in [("Linux", "x86_64"), ("Linux", "aarch64"), ("Darwin", "arm64"),
                            ("Darwin", "x86_64"), ("Windows", "AMD64")]:
        monkeypatch.setattr(platform, "system", lambda s=system: s)
        monkeypatch.setattr(platform, "machine", lambda m=machine: m)
        reachable.update(binaries._platform_keys())
    assert set(binaries._BINARIES) == reachable


#***********************#
#   release fallback    #
#***********************#
@pytest.mark.parametrize("system, machine, expected", [
    ("Linux",   "x86_64",  ["lcmodel-linux-x86_64.xz"]),
    ("Linux",   "aarch64", ["lcmodel-linux-aarch64.xz"]),        # the gap upstream leaves
    ("Darwin",  "arm64",   ["lcmodel-macos-arm64.xz", "lcmodel-macos-x86_64.xz"]),
    ("Darwin",  "x86_64",  ["lcmodel-macos-x86_64.xz"]),
    ("Windows", "AMD64",   []),                                   # upstream's exe suffices
    ("FreeBSD", "amd64",   []),
])
def test_release_assets_per_platform(monkeypatch, system, machine, expected):
    """Apple silicon lists the Intel asset too (Rosetta), but never the same asset twice
    even though two registry keys (sequoia + monterey) map onto it."""
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    assert binaries._release_assets() == expected


def test_release_tag_follows_the_package_version(monkeypatch):
    """A wheel pulls the artifacts built alongside it: release and image are tagged
    "v<version>", the same tag that marks the PyPI release."""
    from lcmodel_wrapper import __version__
    monkeypatch.delenv("LCMODEL_RELEASE_TAG", raising=False)
    monkeypatch.delenv("LCMODEL_DOCKER_IMAGE", raising=False)
    assert binaries._release_tag() == f"v{__version__}"
    assert binaries._container_image() == f"ghcr.io/julianmer/lcmodel:v{__version__}"


def test_release_tag_is_overridable(monkeypatch):
    monkeypatch.setenv("LCMODEL_RELEASE_TAG", "v9.9.9")
    assert binaries._release_base().endswith("/releases/download/v9.9.9")
    assert binaries._container_image().endswith(":v9.9.9")
    monkeypatch.setenv("LCMODEL_DOCKER_IMAGE", "example.org/me/lcmodel:dev")
    assert binaries._container_image() == "example.org/me/lcmodel:dev"


def test_release_download_rejects_a_bad_checksum(monkeypatch, tmp_path):
    """A release asset is a plain URL anyone could re-upload; the sidecar hash is what
    ties it to the CI run that built it, so a mismatch must not install."""
    import lzma

    def fake_download(url, dest):
        if url.endswith(".sha256"):
            dest.write_text("0" * 64 + "  lcmodel-linux-x86_64.xz\n")
        else:
            dest.write_bytes(lzma.compress(b"\x7fELF not really"))

    monkeypatch.setattr(binaries, "_download", fake_download)
    monkeypatch.setattr(binaries, "_release_assets", lambda: ["lcmodel-linux-x86_64.xz"])
    assert binaries._download_release_binary(tmp_path) is None
    assert not (tmp_path / binaries._exec_name()).exists()


#**********************#
#   container shim     #
#**********************#
def _fake_engine(tmp_path, body):
    """A stand-in for the docker CLI: a script that records its arguments."""
    if os.name == "nt":
        pytest.skip("POSIX shell script")
    engine = tmp_path / "fake-docker"
    engine.write_text("#!/bin/sh\n" + body)
    os.chmod(engine, 0o755)
    return engine


def test_shim_is_recognised_and_executable(tmp_path):
    if os.name == "nt":
        pytest.skip("shim is POSIX only")
    shim = binaries._write_shim(tmp_path, "docker", "example.org/lcmodel:v1")
    assert binaries.is_container_shim(shim)
    assert not binaries.is_container_shim(tmp_path / "lcmodel")
    assert os.access(shim, os.X_OK)
    text = shim.read_text()
    assert "example.org/lcmodel:v1" in text
    assert text.startswith("#!/bin/sh")
    assert "lcmodel_wrapper.container import main" in text
    assert binaries.is_container_shim(tmp_path / "lcmodel-container.cmd")   # Windows name


def test_shim_mounts_cwd_at_the_same_path(tmp_path, monkeypatch):
    """The whole trick: the control file carries absolute host paths, so the working
    directory must appear inside the container under exactly the same path."""
    import subprocess
    log = tmp_path / "args.log"
    engine = _fake_engine(tmp_path, f'printf "%s\\n" "$@" > "{log}"\n')
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "elsewhere" / "project"
    work.mkdir(parents=True)

    shim = binaries._write_shim(tmp_path, str(engine), "img:v1")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PYTHONPATH", _REPO)
    monkeypatch.delenv("LCMODEL_DOCKER_IMAGE", raising=False)
    subprocess.run([str(shim)], cwd=work, check=True, stdin=subprocess.DEVNULL)

    args = log.read_text().splitlines()
    cwd = os.path.realpath(work)
    assert args[:3] == ["run", "--rm", "-i"]
    assert args[-1] == "img:v1"
    i = args.index("-w")
    assert args[i + 1] in (str(work), cwd)
    mounts = [args[j + 1] for j, a in enumerate(args) if a == "-v"]
    assert any(m in (f"{work}:{work}", f"{cwd}:{cwd}") for m in mounts)
    # working directory outside $HOME: the home directory is mounted as well, since
    # that is where the basis set usually lives
    assert f"{home}:{home}" in mounts


def test_shim_does_not_mount_home_twice(tmp_path, monkeypatch):
    """Docker refuses duplicate mount points, so a working directory under $HOME must
    not add a second, overlapping mount of $HOME."""
    import subprocess
    log = tmp_path / "args.log"
    engine = _fake_engine(tmp_path, f'printf "%s\\n" "$@" > "{log}"\n')
    home = tmp_path / "home"
    work = home / "project"
    work.mkdir(parents=True)

    shim = binaries._write_shim(tmp_path, str(engine), "img:v1")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PYTHONPATH", _REPO)
    subprocess.run([str(shim)], cwd=work, check=True, stdin=subprocess.DEVNULL)

    mounts = [a for a in log.read_text().splitlines() if a.startswith(str(home) + ":")]
    assert mounts == []


def test_shim_passes_the_health_probe_when_the_container_answers(tmp_path, monkeypatch):
    """The shim is verified by the very same probe as a native binary, so a container
    that prints LCModel's diagnostic is accepted and one that stays silent is not."""
    monkeypatch.setenv("PYTHONPATH", _REPO)
    good_dir, silent_dir = tmp_path / "good", tmp_path / "silent"
    good_dir.mkdir()
    silent_dir.mkdir()
    good = _fake_engine(good_dir, 'echo " MYCONT ..."\n')
    silent = _fake_engine(silent_dir, 'exit 0\n')

    ok, why = binaries.verify_executable(binaries._write_shim(good_dir, str(good), "img"))
    assert ok is True, why
    ok, why = binaries.verify_executable(binaries._write_shim(silent_dir, str(silent), "img"))
    assert ok is False


def test_container_can_see_only_cwd_and_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    work = tmp_path / "work"
    other = tmp_path / "other"
    for d in (home, work, other):
        d.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(work)
    monkeypatch.setenv("PWD", str(work))
    assert container.can_see(work / "tmp" / "temp0.raw")
    assert container.can_see(home / "basis" / "x.basis")
    assert container.can_see(home)
    assert not container.can_see(other / "x.basis")


def test_cached_shim_is_skipped_not_quarantined_when_engine_is_down(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("shim is POSIX only")
    shim = binaries._write_shim(tmp_path, "docker", "img")
    monkeypatch.setattr(binaries, "_container_cli", lambda: "docker")
    monkeypatch.setattr(binaries, "_container_ready", lambda cli, timeout=30.0: (False, "down"))
    assert binaries._cached_binary(tmp_path) is None
    assert shim.is_file()                     # still there for next time
    monkeypatch.setattr(binaries, "_container_ready", lambda cli, timeout=30.0: (True, "ok"))
    assert binaries._cached_binary(tmp_path) == shim


def test_native_cache_wins_over_the_shim(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("shim is POSIX only")
    binaries._write_shim(tmp_path, "docker", "img")
    native = _make_elf(tmp_path / binaries._exec_name(), 0x3E)
    assert binaries._cached_binary(tmp_path) == native


@pytest.mark.parametrize("host, inside", [
    (r"C:\Users\me\proj\tmp\temp0.raw", "/host/C/Users/me/proj/tmp/temp0.raw"),
    (r"c:\Users\me\basis.BASIS",        "/host/C/Users/me/basis.BASIS"),   # drive upper-cased
    (r"D:\data\x.raw",                  "/host/D/data/x.raw"),
    ("C:\\",                            "/host/C/"),
])
def test_windows_paths_map_under_host(host, inside):
    assert container.windows_to_container(host) == inside


def test_windows_unc_paths_are_refused():
    with pytest.raises(ValueError, match="UNC"):
        container.windows_to_container(r"\\server\share\x.raw")


def test_control_file_paths_are_translated_and_nothing_else():
    """Only file keys ("fil*") are rewritten, quoting is preserved, and the input list
    is left untouched because the wrapper keeps reading outputs at the host paths."""
    control = [
        " $LCMODL",
        "nunfil=2048",                                  # "fil" inside a key, not a prefix
        "filbas='C:\\Users\\me\\press.BASIS'",
        'filraw="C:\\Users\\me\\proj\\tmp\\temp0.raw"',
        "filcoo='C:\\Users\\me\\proj\\tmp\\temp0.coord'",
        "title='C:\\not\\a\\file\\key'",
        " $END",
    ]
    before = list(control)
    out = container.translate_control(control, mapper=container.windows_to_container)
    assert control == before
    assert out[0] == " $LCMODL" and out[1] == "nunfil=2048" and out[6] == " $END"
    assert out[2] == "filbas='/host/C/Users/me/press.BASIS'"
    assert out[3] == 'filraw="/host/C/Users/me/proj/tmp/temp0.raw"'
    assert out[4] == "filcoo='/host/C/Users/me/proj/tmp/temp0.coord'"
    assert out[5] == "title='C:\\not\\a\\file\\key'"


def test_translation_is_identity_on_posix():
    if os.name == "nt":
        pytest.skip("identity mounts are the POSIX design")
    control = ["filbas='/home/me/press.basis'", "filraw='/home/me/tmp/temp0.raw'"]
    assert container.translate_control(control) == control


def test_run_command_windows_mounts_drives_under_host():
    """The Windows launcher cannot run here, but the command it would issue can be
    checked: drives of the cwd and home mounted at /host/<LETTER>, cwd translated."""
    cmd = container.run_command("docker", "img:v1", cwd=r"D:\\proj\\run", home=r"C:\\Users\\me",
                                windows=True)
    assert cmd[:4] == ["docker", "run", "--rm", "-i"]
    assert "-u" not in cmd                                   # no uid mapping on Windows
    mounts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-v"]
    assert mounts == ["D:\\:/host/D", "C:\\:/host/C"]
    assert cmd[cmd.index("-w") + 1] == "/host/D/proj/run"
    assert cmd[-1] == "img:v1"


def test_run_command_posix_uses_identical_paths():
    cmd = container.run_command("docker", "img:v1", cwd="/data/run", home="/home/me",
                                windows=False, uid=(1000, 1000))
    assert cmd[cmd.index("-u") + 1] == "1000:1000"
    mounts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-v"]
    assert "/data/run:/data/run" in mounts and "/home/me:/home/me" in mounts
    assert cmd[cmd.index("-w") + 1] == "/data/run"
    # podman: uid mapping via user namespace instead of -u
    cmd = container.run_command("podman", "img", cwd="/home/me/run", home="/home/me",
                                windows=False, uid=(1000, 1000))
    assert "--userns=keep-id" in cmd and "-u" not in cmd
    assert [c for c in cmd if c.startswith("/home/me:")] == []   # cwd inside home: no 2nd mount


def test_container_rung_is_opt_out(monkeypatch, tmp_path):
    monkeypatch.setenv("LCMODEL_NO_DOCKER", "1")
    assert binaries._container_shim(tmp_path) is None
    monkeypatch.delenv("LCMODEL_NO_DOCKER")
    monkeypatch.setattr(binaries, "_container_cli", lambda: None)
    assert binaries._container_shim(tmp_path) is None


#*************************#
#   source build inputs   #
#*************************#
def test_source_list_covers_every_include():
    """LCModel.f INCLUDEs six other files; downloading only LCModel.f (as the wrapper
    used to) fails at the first INCLUDE."""
    assert "LCModel.f" in binaries._SOURCE_FILES
    for inc in ("lcmodel.inc", "lipid-1.inc", "liver-1.inc", "muscle-1.inc",
                "nml_lcmodel.inc", "nml_lcmodl.inc"):
        assert inc in binaries._SOURCE_FILES


#******************#
#   cache layout   #
#******************#
def test_cache_dir_is_architecture_keyed(monkeypatch, tmp_path):
    monkeypatch.setenv("LCMODEL_CACHE_DIR", str(tmp_path))
    cache = binaries._cache_dir()
    assert cache.parent == tmp_path
    assert platform.system().lower() in cache.name
    assert binaries._normalized_machine() in cache.name


def test_quarantine_moves_rather_than_deletes(monkeypatch, tmp_path):
    monkeypatch.setenv("LCMODEL_CACHE_DIR", str(tmp_path))
    bad = _make_elf(tmp_path / "lcmodel", 0x3E)
    dest = binaries._quarantine(bad, reason="download")
    assert dest is not None and dest.is_file()
    assert not bad.exists()


def test_resolve_reports_every_source_it_tried(monkeypatch, tmp_path):
    monkeypatch.setenv("LCMODEL_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("LCMODEL_EXEC", raising=False)   # an explicit binary would be used as is
    with pytest.raises(RuntimeError) as excinfo:
        binaries.resolve_executable(allow_download=False, allow_build=False,
                                    allow_docker=False)
    message = str(excinfo.value)
    assert "tried:" in message
    assert "cached" in message
    assert "docker=False" in message


def test_resolve_rejects_an_unrunnable_explicit_path(tmp_path):
    bad = _make_elf(tmp_path / "mylcmodel", 0x3E)
    with pytest.raises(RuntimeError, match="not a runnable LCModel binary"):
        binaries.resolve_executable(path2exec=str(bad))


def test_resolve_still_rejects_a_missing_explicit_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        binaries.resolve_executable(path2exec=str(tmp_path / "nope"))
