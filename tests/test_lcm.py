####################################################################################################
#                                           test_lcm.py                                            #
####################################################################################################
#                                                                                                  #
# Purpose: End-to-end test of the PyLCModel wrapper by fitting MRS data from the ISMRM 2016        #
#          fitting challenge (jMRUI datasets + .basis) and comparing against the ground truth.     #
#                                                                                                  #
#          The same run doubles as the acceptance test for every LCModel binary CI builds: point   #
#          LCMODEL_EXEC at a binary (or at the container launcher) and the fit must reproduce the  #
#          reference error. LCModel exits 0 even on fatal errors, so this - not a return code -    #
#          is what proves a build works.                                                           #
#                                                                                                  #
####################################################################################################

import os
import re
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from lcmodel_wrapper import PyLCModel, io

_REPO = Path(__file__).resolve().parent.parent
_CHALLENGE = _REPO / "example_data" / "2016_fitting_challenge"
_TRUTH = _REPO / "example_data" / "2016_fitting_challenge_gts"

# Mean absolute concentration error over the first five datasets, from the native
# macOS arm64 build. Every build must land here; a miscompiled binary either raises
# (no .coord) or is off by orders of magnitude, so the tolerance can stay tight.
REFERENCE_MAE = 1.609
TOLERANCE = 0.02


#*************#
#   loading   #
#*************#
def load_EXCEL_conc(path2conc: Path):
    """Load ISMRM-2016 ground-truth concentrations -> sorted {metabolite: concentration}."""
    import pandas as pd
    truth = {"Ace": 0.0}  # Ace is only partially present
    df = pd.read_excel(str(path2conc), header=17)
    for met, val in zip(df["Metabolites"], df["concentration"]):
        if not isinstance(met, str):
            break
        truth[met] = val
    if "MMBL" in truth:
        truth["Mac"] = truth.pop("MMBL")
    return dict(sorted(truth.items()))


#***********#
#   fitting #
#***********#
def challenge_mae(test_size: int = 5) -> float:
    """Fit the first "test_size" challenge datasets and return the MAE against the truth."""
    basis = _CHALLENGE / "basisset_LCModel" / "press3T_30ms.BASIS"
    data_dir = _CHALLENGE / "datasets_JMRUI"

    lcm = PyLCModel(path2basis=str(basis), sample_points=2048, domain="time")
    n_metabs = lcm.basis.n_metabs

    # pair ground truths with challenge datasets by dataset number
    conc_by_num = {
        int(re.search(r"dataset(\d+)", p.stem).group(1)): p
        for p in _TRUTH.iterdir() if p.suffix in (".xlsx", ".xls")
    }
    numbers = sorted(conc_by_num)[:test_size]

    truth = [load_EXCEL_conc(conc_by_num[n]) for n in numbers]
    concs = np.array([[t.get(m, 0.0) for m in lcm.basis.names] for t in truth])[:, :n_metabs]

    # water-suppressed (WS) and water reference (nWS) FIDs, time domain
    data = np.array([io.read_jmrui(str(data_dir / f"dataset{n}_WS.txt")).fids[0] for n in numbers])
    water = np.array([io.read_jmrui(str(data_dir / f"dataset{n}_nWS.txt")).fids[0] for n in numbers])

    thetas, _ = lcm(data, water)
    return float(lcm.concsLoss(concs, thetas, type="ae").mean())


# an uninitialised submodule is an empty directory, so look for its content
@pytest.mark.skipif(not (_CHALLENGE / "basisset_LCModel" / "press3T_30ms.BASIS").is_file()
                    or not any(_TRUTH.glob("*.xlsx")),
                    reason="challenge data not checked out (git submodule update --init)")
def test_challenge_fit_matches_reference():
    mae = challenge_mae()
    assert abs(mae - REFERENCE_MAE) < TOLERANCE, f"MAE {mae:.4f} vs reference {REFERENCE_MAE}"


if __name__ == "__main__":
    print("MAE:", challenge_mae())
