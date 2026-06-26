"""PyLCModel - a lightweight Python wrapper for the LCModel MRS fitting tool.

LCModel itself is a separate program by Dr. Stephen Provencher, distributed under the
BSD 3-Clause License (see LICENSE.lcmodel). This package only wraps it.
"""

from .core import PyLCModel
from .basis import read_basis, LCModelBasis
from .convert import ensure_basis, convert_to_basis, detect_format
from . import binaries, io, control, coord, convert

__all__ = [
    "PyLCModel",
    "read_basis",
    "LCModelBasis",
    "ensure_basis",
    "convert_to_basis",
    "detect_format",
    "binaries",
    "io",
    "control",
    "coord",
    "convert",
]