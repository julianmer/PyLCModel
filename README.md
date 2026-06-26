<p align="center">
  <img src="https://raw.githubusercontent.com/julianmer/PyLCModel/main/assets/logo.png" alt="PyLCModel" width="320">
</p>

# PyLCModel

[![PyPI version](https://badge.fury.io/py/lcmodel-wrapper.svg)](https://badge.fury.io/py/lcmodel-wrapper)

**PyLCModel** is a lightweight Python wrapper that streamlines the use of [LCModel](https://s-provencher.com/lcmodel.shtml) for least-squares spectral fitting in MRS. It automates control-file generation, handles flexible data input, manages the LCModel executable for you, and parses the output (with single- and multi-core processing).

---

## Features

- **Zero-setup binaries** — the LCModel executable is resolved automatically (download, build from source, or your own path); nothing is bundled in the wheel.
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

NIfTI-MRS support is an optional extra:
```bash
pip install "lcmodel-wrapper[nifti]"
```

### From Source
```bash
git clone https://github.com/julianmer/PyLCModel.git
cd PyLCModel
pip install -e .
```

Core dependencies are intentionally minimal (`numpy`, `scipy`). `nibabel` is only needed for NIfTI-MRS.

---

## How the LCModel binary is handled

The LCModel program is **not** part of this package and is **not** shipped in the wheel. On first use, the binary is resolved in this order:

1. an explicit `path2exec="/path/to/lcmodel"` you pass to `PyLCModel`,
2. a previously cached download/build (under `~/.cache/lcmodel_wrapper`, or `%LOCALAPPDATA%` on Windows; override with `LCMODEL_CACHE_DIR`),
3. a download of the matching binary for your OS/architecture from [schorschinho/LCModel](https://github.com/schorschinho/LCModel),
4. a build from the LCModel Fortran source via `gfortran` (source fetched on demand).

There is no git submodule and no bundled `lcmodel/` folder — keeping both the repository and the PyPI wheel small.

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
> Basis conversion is **experimental** and not validated. For a dedicated, more complete tool (including MATLAB v7.3 and prior formats), see the [MRS Basis Set Conversion Toolbox](https://github.com/igweckay/MRS-Basis-Set-Conversion-Toolbox).

---

## Licensing

This wrapper (the Python code) is released under the **Apache License 2.0** (see [LICENSE](LICENSE)).

**LCModel itself is a separate program** by Dr. Stephen Provencher, distributed under the **BSD 3-Clause License** (see [LICENSE.lcmodel](LICENSE.lcmodel)). This package does not bundle LCModel; when it downloads, builds, or runs the LCModel executable, that BSD-3-Clause license and the attributions in [NOTICE](NOTICE) apply. See the [LCModel home page](https://s-provencher.com/lcmodel.shtml) for details.

---

## Acknowledgements

- LCModel binaries: [schorschinho/LCModel](https://github.com/schorschinho/LCModel) (Georg Oeltzschner and contributors)
- Basis conversion reference: [MRS Basis Set Conversion Toolbox](https://github.com/igweckay/MRS-Basis-Set-Conversion-Toolbox) (Kay Igwe)
- NIfTI-MRS: [spec2nii](https://github.com/wtclarke/spec2nii), [NIfTI-MRS Pyhton tools](https://github.com/wtclarke/nifti_mrs_tools) (Will Clarke)
