# PyLCModel

**PyLCModel** is a Python wrapper designed to streamline the use of LCModel for least-squares spectral fitting in MRS. It takes the complexity out of setting up LCModel by automating control file generation, handling data conversions, and managing execution (with support for both single and multi-core processing).

---

## Features

- **Easy Setup:** Quickly run LCModel with minimal code.
- **Automated Control Files:** Dynamically generates and adjusts LCModel control files to suit your data.
- **Multiprocessing Support:** Leverage multiple cores to accelerate batch processing.
- **Data Conversion:** Seamlessly converts to .raw for execution. (Coming soon: conversion to .basis)
- **Robust Output Parsing:** Extracts metabolite concentrations and CRLBs from LCModel output.

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

---

## Getting Started

```python
from lcmodel_wrapper import PyLCModel

# Initialize the LCModel wrapper with your basis set
lcmodel = PyLCModel(path2basis='/path/to/your/basis_set.basis')

# Assuming `data` is your MRS data in the frequency domain as a NumPy array
# Fit the data using LCModel
concentrations, crlbs = lcmodel(data)

# Print results
print("Fitted Metabolite Concentrations:", concentrations)
print("CRLBs:", crlbs)
```