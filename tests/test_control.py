####################################################################################################
#                                         test_control.py                                          #
####################################################################################################
#                                                                                                  #
# Purpose: Extra control parameters: written in LCModel's namelist syntax, and kept whichever      #
#          way the control is made, including when it is rebuilt once the data is known.           #
#                                                                                                  #
####################################################################################################

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from lcmodel_wrapper import PyLCModel, binaries
from lcmodel_wrapper.control import build_control, set_params

_BASIS = (Path(__file__).resolve().parent.parent / "example_data" / "2016_fitting_challenge"
          / "basisset_LCModel" / "press3T_30ms.BASIS")


#************#
#   syntax   #
#************#
def test_params_are_written_as_the_namelist_expects():
    control = set_params(build_control("x.basis", 2048, 4000.0, 123.2),
                         {"nuse1": 2, "chuse1(1)": "NAA", "dows": True, "namrel": "'Cr'"})

    assert "nuse1=2" in control and "chuse1(1)='NAA'" in control
    assert "dows=T" in control and "namrel='Cr'" in control
    assert control[-1] == "$END"


#*************#
#   rebuilt   #
#*************#
def _binary():
    try:
        return binaries.resolve_executable(allow_download=False, allow_build=False)
    except Exception:                          # noqa: BLE001 - any failure means absent
        return None


@pytest.mark.skipif(not _BASIS.exists() or _binary() is None,
                    reason="needs the challenge basis and a cached LCModel binary")
def test_params_survive_the_control_being_rebuilt():
    """The control is rebuilt from the data at fit time; the params must outlive that."""
    engine = PyLCModel(str(_BASIS), params={"chuse1(1)": "NAA"},
                       allow_download=False, allow_build=False, allow_docker=False)

    assert "chuse1(1)='NAA'" in engine.control
    engine.sample_points += 1                  # as a different acquisition would
    assert "chuse1(1)='NAA'" in engine._build_control()
