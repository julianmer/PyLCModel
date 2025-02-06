# PyLCModel

**PyLCModel** is a Python wrapper designed to streamline the use of LCModel for least-squares spectral fitting in MRS. It takes the complexity out of setting up LCModel by automating control file generation, handling data conversions, and managing execution (with support for both single and multi-core processing).

---

## Features

- **Easy Setup:** Quickly run LCModel with minimal code.
- **Automated Control Files:** Dynamically generates and adjusts LCModel control files to suit your data.
- **Multiprocessing Support:** Leverage multiple cores to accelerate batch processing.
- **Data Conversion:** Seamlessly converts to .raw for execution. (Coming soon: conversion to .basis)
- **Robust Output Parsing:** Extracts metabolite concentrations and fit uncertainties directly from LCModel output.

---

## Getting Started

```python
from frameworkLCM import FrameworkLCModel

# Initialize the LCModel wrapper with your basis set
lcm = FrameworkLCModel(path2basis='/path/to/your/basis_set.basis')

# Assuming 'data' is your MRS data in the frequency domain as a NumPy array
# Fit the data using LCModel
concentrations, crlbs = lcm(data)

# 'concentrations' now contains the fitted metabolite concentrations
# 'crlbs' contains the corresponding Cramér-Rao Lower Bounds
print("Fitted Metabolite Concentrations:", concentrations)
print("CRLBs:", crlbs)
```
