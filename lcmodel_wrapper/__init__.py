"""PyLCModel - a lightweight Python wrapper for the LCModel MRS fitting tool.

LCModel itself is a separate program by Dr. Stephen Provencher, distributed under the
BSD 3-Clause License (see LICENSE.lcmodel). This package only wraps it.
"""

from ._version import __version__
from .core import PyLCModel, LCModelError
from .basis import read_basis, LCModelBasis
from .binaries import verify_executable
from .convert import ensure_basis, convert_to_basis, detect_format
from .io import from_nifti_mrs, load_signals
from . import binaries, container, io, control, coord, convert

__all__ = [
    "PyLCModel",
    "LCModelError",
    "verify_executable",
    "read_basis",
    "LCModelBasis",
    "ensure_basis",
    "convert_to_basis",
    "detect_format",
    "from_nifti_mrs",
    "load_signals",
    "binaries",
    "container",
    "io",
    "control",
    "coord",
    "convert",
]