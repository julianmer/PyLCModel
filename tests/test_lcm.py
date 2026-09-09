####################################################################################################
#                                           test_lcm.py                                            #
####################################################################################################
#                                                                                                  #
# Purpose: End-to-end test of the PyLCModel wrapper by fitting MRS data from the ISMRM 2016        #
#          fitting challenge (jMRUI datasets + .basis), without any fsl_mrs dependency.            #
#                                                                                                  #
####################################################################################################

import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from lcmodel_wrapper import PyLCModel
from lcmodel_wrapper import io


#*************#
#   loading   #
#*************#
def load_EXCEL_conc(path2conc: Path):
    """Load ISMRM-2016 ground-truth concentrations -> sorted {metabolite: concentration}."""
    truth = {"Ace": 0.0}  # Ace is only partially present
    df = pd.read_excel(str(path2conc), header=17)
    for met, val in zip(df["Metabolites"], df["concentration"]):
        if not isinstance(met, str):
            break
        truth[met] = val
    if "MMBL" in truth:
        truth["Mac"] = truth.pop("MMBL")
    return dict(sorted(truth.items()))


#*********#
#   main  #
#*********#
def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    example_data = os.path.join(repo_root, "example_data")
    challenge = os.path.join(example_data, "2016_fitting_challenge")

    config = {
        "path2basis": os.path.join(challenge, "basisset_LCModel", "press3T_30ms.BASIS"),
        "path2concs": os.path.join(example_data, "2016_fitting_challenge_gts"),
        "path2data": os.path.join(challenge, "datasets_JMRUI"),
        "path2save": None,
        "test_size": 5,
        "sample_points": 2048,
    }

    if not os.path.exists(config["path2basis"]):
        raise FileNotFoundError(
            "ISMRM 2016 fitting challenge data not found. Initialize the submodule with:\n"
            "  git submodule update --init"
        )
    for key in ("path2concs", "path2data"):
        if not os.path.exists(config[key]):
            raise FileNotFoundError(f"{key} not found: {config[key]}")

    # initialize model (binary is resolved automatically; data is time-domain FIDs)
    lcm = PyLCModel(
        path2basis=config["path2basis"],
        sample_points=config["sample_points"],
        domain="time",
    )
    basis_names = lcm.basis.names
    n_metabs = lcm.basis.n_metabs

    # pair ground truths with challenge datasets by dataset number
    conc_by_num = {
        int(re.search(r"dataset(\d+)", p.stem).group(1)): p
        for p in Path(config["path2concs"]).iterdir()
        if p.suffix in (".xlsx", ".xls")
    }
    numbers = sorted(conc_by_num)[: config["test_size"]]

    # ground truth
    concs_list = [load_EXCEL_conc(conc_by_num[n]) for n in numbers]
    concs = np.array([[c.get(met, 0.0) for met in basis_names] for c in concs_list])[:, :n_metabs]

    # data: jMRUI FIDs (time domain), water-suppressed (WS) and water reference (nWS)
    data_dir = Path(config["path2data"])
    data = np.array(
        [io.read_jmrui(str(data_dir / f"dataset{n}_WS.txt")).fids[0] for n in numbers]
    )
    water = np.array(
        [io.read_jmrui(str(data_dir / f"dataset{n}_nWS.txt")).fids[0] for n in numbers]
    )

    # fit
    lcm.set_save_path(config["path2save"])
    thetas, uncs = lcm(data, water)

    loss = lcm.concsLoss(concs, thetas, type="ae")
    print("MAE:", float(loss.mean()))


if __name__ == "__main__":
    main()
