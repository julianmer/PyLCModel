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

LCModel is **not** shipped in the wheel. On first use it is found in this order, and the first one that works is cached:

1. `path2exec` you pass to `PyLCModel`,
2. the community binary for your OS/architecture from [schorschinho/LCModel](https://github.com/schorschinho/LCModel),
3. the binary built by this repository's CI for the installed version ([releases](https://github.com/julianmer/PyLCModel/releases); Linux x86_64/aarch64 and macOS arm64/x86_64, all statically linked),
4. the container image `ghcr.io/julianmer/lcmodel`, if Docker or podman is running,
5. a build from source with `gfortran`.

Each candidate is run once before it is accepted, so a binary that cannot run on your machine is skipped rather than cached. Useful switches: `allow_download`, `allow_docker`, `allow_build` on `PyLCModel`, and the `LCMODEL_EXEC` / `LCMODEL_CACHE_DIR` environment variables.

With the container, LCModel sees your working directory and your home directory; keep the basis set and any `save_path` under one of them. The image also works on its own:
```bash
docker run --rm -i -v "$PWD:$PWD" -w "$PWD" ghcr.io/julianmer/lcmodel < control.file
```

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