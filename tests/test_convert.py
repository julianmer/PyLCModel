####################################################################################################
#                                         test_convert.py                                          #
####################################################################################################
#                                                                                                  #
# Purpose: Basis conversion against LCModel's own MakeBasis output. The ISMRM 2016 fitting         #
#          challenge ships one basis set as per-metabolite LCModel .RAW files, as jMRUI text       #
#          files, and as the .BASIS that MakeBasis made from those .RAW files; converting either   #
#          of the first two must reproduce the data blocks of the third.                           #
#                                                                                                  #
####################################################################################################

import os
import re
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from lcmodel_wrapper import convert_to_basis

_REPO = Path(__file__).resolve().parent.parent
_CHALLENGE = _REPO / "example_data" / "2016_fitting_challenge"
_MAKEBASIS = _CHALLENGE / "basisset_LCModel" / "press3T_30ms.BASIS"

pytestmark = pytest.mark.skipif(not _MAKEBASIS.is_file(),
                                reason="challenge data missing (git submodule update --init)")


def read_basis_spectra(path) -> dict:
    """The data block of every metabolite in a ".basis" file, by metabolite name."""
    text = Path(path).read_text()
    spectra = {}
    for m in re.finditer(r"\$BASIS\s(.*?)\$END(.*?)(?=\$|\Z)", text, re.S):
        name = re.search(r"METABO\s*=\s*'([^']*)'", m.group(1)).group(1).strip()
        values = np.array(m.group(2).split(), dtype=float)
        spectra[name] = values[0::2] + 1j * values[1::2]
    return spectra


@pytest.mark.parametrize("fmt, folder", [("raw", "basisset_LCModel"),
                                         ("jmrui", "basisset_JMRUI")])
def test_conversion_matches_makebasis(tmp_path, fmt, folder):
    reference = read_basis_spectra(_MAKEBASIS)
    out = convert_to_basis(str(_CHALLENGE / folder), out_path=str(tmp_path / "converted.basis"),
                           fmt=fmt, dwell=2.5e-4, central_freq=123.261703)
    converted = read_basis_spectra(out)

    assert set(reference) <= set(converted)
    for name, spectrum in reference.items():
        assert converted[name].shape == spectrum.shape, name
        err = np.abs(converted[name] - spectrum).max() / np.abs(spectrum).max()
        assert err < 1e-3, f"{name}: {err:.2e}"
