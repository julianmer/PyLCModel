<div align="center">
  <img src="https://raw.githubusercontent.com/julianmer/PyLCModel/main/assets/logo_grey.png" alt="PyLCModel Logo" width="160"/>
  <h1 style="margin-top:-10px; margin-bottom: 5px;">PyLCModel</h1>
  <p style="margin-top: 0px;"><em>A lightweight Python wrapper for LCModel spectral fitting in MR spectroscopy</em></p>

  [![PyPI version](https://badge.fury.io/py/lcmodel-wrapper.svg)](https://pypi.org/project/lcmodel-wrapper/)
  [![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
  [![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
</div>

**PyLCModel** is a lightweight Python wrapper that streamlines the use of [LCModel](https://s-provencher.com/lcmodel.shtml) for least-squares spectral fitting in MRS. It automates control-file generation, handles flexible data input, manages the LCModel executable for you, and parses the output (with single- and multi-core processing).

---

## Features

- **Zero-setup binaries** — the LCModel executable is resolved automatically (download, container image, build from source, or your own path); nothing is bundled in the wheel.
- **Flexible input** — NumPy arrays, NIfTI-MRS, jMRUI text, and LCModel `.RAW`, in time or frequency domain.
- **Automated control files** — generated to match your data, or templated from an existing one.
- **Basis conversion (experimental)** — jMRUI, FSL-MRS, LCModel `.RAW`, and Osprey/FID-A basis sets to `.basis`.
- **Batch fitting** — single- or multi-core, with full output parsing (concentrations, CRLBs, QC, fitted series).

---

## Installation

### From PyPI
```bash
pip install lcmodel-wrapper
```

### From Source
```bash
git clone https://github.com/julianmer/PyLCModel.git
cd PyLCModel
pip install -e .
```
Add `--recursive` to the clone (or run `git submodule update --init`) to also fetch the
[ISMRM 2016 fitting challenge](https://www.ismrm.org/workshops/Spectroscopy16/mrs_fitting_challenge/)
example data used by the tests.

---

## How the LCModel binary is handled

The LCModel program is **not** part of this package and is **not** shipped in the wheel. On first use, the binary is resolved in this order:

1. an explicit `path2exec="/path/to/lcmodel"` you pass to `PyLCModel`,
2. a previously cached download/build (under `~/.cache/lcmodel_wrapper/<os>-<arch>/`, or `%LOCALAPPDATA%` on Windows; override the root with `LCMODEL_CACHE_DIR`),
3. a download of the matching binary for your OS/architecture from [schorschinho/LCModel](https://github.com/schorschinho/LCModel),
4. a download of the binary built by this repository's CI and attached to the [GitHub release](https://github.com/julianmer/PyLCModel/releases) matching the installed package version (Linux x86_64/aarch64 fully static, macOS arm64/x86_64 with libgfortran linked statically; each verified against its published SHA-256),
5. **a container** — if `docker` (or `podman`) is installed and running, the image `ghcr.io/julianmer/lcmodel` is pulled and a small launcher script is cached that runs LCModel from it (Linux, macOS, and Windows),
6. a build from the LCModel Fortran source via `gfortran` (source fetched on demand).

Every candidate is **run once before it is accepted** — LCModel is asked to identify itself, and anything that cannot execute or does not answer is moved to `<cache>/quarantine/` so the next source gets a turn. This is what stops a wrong-architecture download from being cached and served forever. Set `LCMODEL_SKIP_VERIFY=1` to bypass the check, or `LCMODEL_VERIFY_TIMEOUT` to change its 60 s bound.

The cache is keyed by architecture, so a home directory shared across a mixed-architecture cluster does not have nodes fighting over one file.

### Running from a container

The container is the one option that behaves identically everywhere: inside it LCModel is always the same statically linked Linux binary, so nothing depends on your macOS version, Homebrew, or which Apple-silicon generation you have (upstream's macOS builds are tied to the machine they were compiled on, which is why an M1 build does not run on an M4). Docker Desktop, OrbStack, Colima, or rootless podman all work.

On Linux and macOS the launcher bind-mounts your **working directory** and your **home directory** at the same paths inside the container, so the absolute paths in the control file need no translation. On Windows it mounts the **drives** holding those two at `/host/<LETTER>` and the wrapper rewrites the file paths in the control file to match (`C:\Users\me\x.basis` → `/host/C/Users/me/x.basis`); UNC paths are not supported. The one constraint: the basis set and any absolute `save_path` must live under the working or home directory (on Windows: on one of their drives); `PyLCModel` raises a clear error otherwise.

```python
lcmodel = PyLCModel(path2basis="~/basis/press_3t.basis")        # container used automatically if needed
lcmodel = PyLCModel(path2basis="...", allow_docker=False)        # never use a container
```

Environment knobs: `LCMODEL_NO_DOCKER=1` disables the rung, `LCMODEL_DOCKER_IMAGE` overrides the image (e.g. a locally built one), `LCMODEL_PULL_TIMEOUT` bounds the pull (default 900 s), and `LCMODEL_RELEASE_TAG` selects which release (and matching image tag) steps 4 and 5 use instead of the default `v<package version>`. Delete `<cache>/lcmodel-container` to make the resolver try the native sources again.

You can also use the image directly, without Python:
```bash
docker run --rm -i -v "$PWD:$PWD" -w "$PWD" ghcr.io/julianmer/lcmodel:latest < control.file
```

No LCModel code or binary is bundled — keeping both the repository and the PyPI wheel small. (The only git submodule in this repository is the optional example data under `example_data/`.)

---

## Getting Started

```python
from lcmodel_wrapper import PyLCModel

# Initialize the wrapper with your basis set (the LCModel binary is resolved automatically)
lcmodel = PyLCModel(path2basis="/path/to/your/basis_set.basis")

# `data` can be a NumPy array of FIDs (time domain), a NIfTI-MRS path, etc.
concentrations, crlbs = lcmodel(data)

print("Fitted Metabolite Concentrations:", concentrations)
print("CRLBs:", crlbs)
```

Frequency-domain input or a custom executable:
```python
lcmodel = PyLCModel(
    path2basis="/path/to/basis.basis",
    domain="freq",                 # pass spectra instead of FIDs
    path2exec="/path/to/lcmodel",  # optional: use your own binary
)
```

Experimental basis conversion (other formats -> `.basis`):
```python
# Auto-detect the source format (jMRUI/AQSES/QUEST .txt folder, FSL-MRS .json folder,
# LCModel .RAW folder, or Osprey/FID-A .mat):
lcmodel = PyLCModel(path2basis="/path/to/basis_folder", convert_basis=True)

# ...or force a format and supply parameters the source does not carry:
lcmodel = PyLCModel(
    path2basis="/path/to/raw_folder",
    convert_basis=True,
    basis_format="raw",          # "jmrui" | "fsl" | "raw" | "mat"
    bandwidth=4000, central_freq=123.25,
)

# Or convert directly without fitting:
from lcmodel_wrapper import convert_to_basis
convert_to_basis("/path/to/jmrui_folder", out_path="out.basis")
```
> Basis conversion is **experimental** and not validated. For a dedicated, more complete tool, see the [MRS Basis Set Conversion Toolbox](https://github.com/igweckay/MRS-Basis-Set-Conversion-Toolbox).

---

## Licensing

This wrapper (the Python code) is released under the **Apache License 2.0** (see [LICENSE](LICENSE)).

**LCModel itself is a separate program** by Dr. Stephen Provencher, distributed under the **BSD 3-Clause License** (see [LICENSE.lcmodel](LICENSE.lcmodel)). This package does not bundle LCModel; when it downloads, builds, or runs the LCModel executable, that BSD-3-Clause license and the attributions in [NOTICE](NOTICE) apply. See the [LCModel home page](https://s-provencher.com/lcmodel.shtml) for details.

---

## Acknowledgements

- LCModel source code: created and made [available](https://s-provencher.com/lcmodel.shtml) free of charge (Stephen Provencher).
- LCModel binaries: [schorschinho/LCModel](https://github.com/schorschinho/LCModel) (Georg Oeltzschner and contributors)
- Basis conversion reference: [MRS Basis Set Conversion Toolbox](https://github.com/igweckay/MRS-Basis-Set-Conversion-Toolbox) (Kay Igwe)
- NIfTI-MRS: [spec2nii](https://github.com/wtclarke/spec2nii), [NIfTI-MRS Python tools](https://github.com/wtclarke/nifti_mrs_tools) (Will Clarke)
- Example data: [ISMRM 2016 MRS Fitting Challenge](https://www.ismrm.org/workshops/Spectroscopy16/mrs_fitting_challenge/) (Małgorzata Marjańska, Dinesh Deelchand, Roland Kreis), mirrored via [wtclarke/mrs_fitting_challenge](https://github.com/wtclarke/mrs_fitting_challenge)

---

<div align="center">
  <sub>Built with ❤️ for the MRS community</sub>
</div>