####################################################################################################
#                                           test_lcm.py                                            #
####################################################################################################
#                                                                                                  #
# Purpose: End-to-end test of the PyLCModel wrapper by fitting MRS data from the ISMRM 2016        #
#          fitting challenge (jMRUI datasets + .basis), without any fsl_mrs dependency.            #
#                                                                                                  #
####################################################################################################

import os
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

    config = {
        "path2basis": os.path.join(example_data, "press3T_30ms.BASIS"),
        "path2concs": os.path.join(example_data, "ground_truth"),
        "path2data": os.path.join(example_data, "datasets_JMRUI_WS"),
        "path2water": os.path.join(example_data, "datasets_JMRUI_nWS"),
        "path2save": None,
        "test_size": 5,
        "sample_points": 2048,
    }

    for key in ("path2basis", "path2concs", "path2data"):
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

    # ground truth
    conc_files = sorted(
        p for p in Path(config["path2concs"]).iterdir() if p.suffix in (".xlsx", ".xls")
    )[: config["test_size"]]
    concs_list = [load_EXCEL_conc(p) for p in conc_files]
    concs = np.array([[c.get(met, 0.0) for met in basis_names] for c in concs_list])[:, :n_metabs]

    # data: jMRUI FIDs (time domain)
    data_files = sorted(p for p in Path(config["path2data"]).iterdir() if p.is_file())[
        : config["test_size"]
    ]
    data = np.array([io.read_jmrui(str(p)).fids[0] for p in data_files])

    # optional water references
    water = None
    if config.get("path2water") and Path(config["path2water"]).is_dir():
        water_files = sorted(Path(config["path2water"]).iterdir())[: config["test_size"]]
        if water_files:
            water = np.array([io.read_jmrui(str(p)).fids[0] for p in water_files])

    # fit
    lcm.set_save_path(config["path2save"])
    thetas, uncs = lcm(data, water)

    if water is None:
        thetas = lcm.optimalReference(concs, thetas) * thetas

    loss = lcm.concsLoss(concs, thetas, type="ae")
    print("MAE:", float(loss.mean()))


if __name__ == "__main__":
    main()
