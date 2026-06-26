####################################################################################################
#                                            basis.py                                              #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 26/06/26                                                                                #
#                                                                                                  #
# Purpose: Lightweight parser for LCModel ".basis" files. Replaces the previous dependency on       #
#          fsl_mrs for reading basis metadata. It extracts only what the wrapper needs to build    #
#          control files and to align fitted concentrations: the field strength (HZPPPM), dwell       #
#          time (BADELT), number of points (NDATAB) and the ordered metabolite names (METABO).     #
#                                                                                                  #
####################################################################################################

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional


#*************************#
#   basis data container  #
#*************************#
@dataclass
class LCModelBasis:
    """Metadata parsed from an LCModel ".basis" file."""

    path: str
    central_freq: Optional[float] = None   # HZPPPM, field strength in MHz
    dwell: Optional[float] = None           # BADELT, dwell time in seconds
    n_points: Optional[int] = None          # NDATAB, number of complex points
    echo_time: Optional[float] = None       # ECHOT, echo time
    names: List[str] = field(default_factory=list)   # METABO entries, in file order

    @property
    def bandwidth(self) -> Optional[float]:
        """Spectral bandwidth in Hz (1 / dwell)."""
        if self.dwell in (None, 0):
            return None
        return 1.0 / self.dwell

    @property
    def n_metabs(self) -> int:
        return len(self.names)


#***********************************#
#   parse a single namelist value   #
#***********************************#
def _find_scalar(text: str, key: str):
    """Return the first numeric scalar assigned to "key" (e.g. "HZPPPM = 123.26")."""
    m = re.search(rf"{key}\s*=\s*([-+0-9.eEdD]+)", text)
    if not m:
        return None
    raw = m.group(1).replace("D", "E").replace("d", "e")
    try:
        return float(raw)
    except ValueError:
        return None


#******************#
#   read a basis   #
#******************#
def read_basis(path: str) -> LCModelBasis:
    """Parse an LCModel ".basis" file and return an LCModelBasis.

    Only the header namelists and the "METABO" names are read; the (large) numeric
    basis vectors are skipped for speed.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Basis file not found: {path}")

    basis = LCModelBasis(path=os.path.abspath(path))

    with open(path, "r", errors="ignore") as fh:
        header_lines = []
        for line in fh:
            header_lines.append(line)
            # "METABO" lines are sparse; keep scanning the whole file for them.
            # Match exactly "METABO =" (not "METABO_CONTAM" / "METABO_SINGLET").
            m = re.match(r"\s*METABO\s*=\s*'(.*?)'", line)
            if m:
                basis.names.append(m.group(1).strip())

        header = "".join(header_lines[:200])   # scalars live near the top

    basis.central_freq = _find_scalar(header, "HZPPPM")
    basis.dwell = _find_scalar(header, "BADELT")
    basis.echo_time = _find_scalar(header, "ECHOT")
    ndatab = _find_scalar(header, "NDATAB")
    basis.n_points = int(ndatab) if ndatab is not None else None

    if not basis.names:
        raise ValueError(
            f"No METABO entries found in basis file: {path}. "
            "Is this a valid LCModel .basis file?"
        )
    return basis
