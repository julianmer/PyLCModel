####################################################################################################
#                                           test_lcm.py                                            #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 20/06/24                                                                                #
#                                                                                                  #
# Purpose: Tests the PyLCModel class by fitting MRS data from the ISMRM 2016 fitting challenge.    #
#                                                                                                  #
####################################################################################################


#*************#
#   imports   #
#*************#
import sys

import numpy as np
import pandas as pd

from fsl_mrs.utils import mrs_io

from pathlib import Path

# own
from lcmodel_wrapper import PyLCModel


#*************#
#   loading   #
#*************#
def load_EXCEL_conc(path2conc: Path):
    """
    Load a list of concentrations from an EXCEL file (ISMRM 2016 fitting challenge).
    Returns a sorted dict of metabolite -> concentration
    """
    truth = {"Ace": 0.0}  # initialize, Ace is only partially present

    df = pd.read_excel(str(path2conc), header=17)
    for i, met in enumerate(df["Metabolites"]):
        if not isinstance(met, str):
            break
        truth[met] = df["concentration"].iloc[i]

    # rename MMBL to Mac if present
    if "MMBL" in truth:
        truth["Mac"] = truth.pop("MMBL")
    return dict(sorted(truth.items()))


#*************#
#   imports   #
#*************#
def main():

    repo_root = Path(__file__).resolve().parents[1]
    example_data = repo_root / "example_data"

    config = {
        "path2basis": example_data / "press3T_30ms.BASIS",
        "path2concs": example_data / "ground_truth",
        "path2data": example_data / "datasets_JMRUI_WS",
        "path2water": example_data / "datasets_JMRUI_nWS",
        "path2save": None,
        "test_size": 10,
        "sample_points": 2048,
    }

    # quick existence checks
    if not config["path2basis"].exists():
        raise FileNotFoundError(
            f"Basis file not found: {config['path2basis']}\n"
            "Make sure example_data contains your BASIS file or update path."
        )

    if not config["path2concs"].is_dir():
        raise FileNotFoundError(
            f"Concentration folder not found: {config['path2concs']}"
        )

    if not config["path2data"].is_dir():
        raise FileNotFoundError(f"Data folder not found: {config['path2data']}")

    # initialize model
    lcm = PyLCModel(str(config["path2basis"]), sample_points=config["sample_points"])

    # load ground truth concentration files
    conc_files = sorted([p for p in (Path(config["path2concs"])).iterdir() if p.suffix in (".xlsx", ".xls")])[: config["test_size"]]
    if len(conc_files) == 0:
        raise RuntimeError("No concentration excel files found in path2concs")

    concs_list = [load_EXCEL_conc(p) for p in conc_files]

    # align to basis names
    try:
        basis_names = lcm.basisFSL._names
        n_metabs = lcm.basisFSL.n_metabs
    except Exception as e:
        raise AttributeError("Could not access basisFSL._names or n_metabs from LCM object") from e

    concs_aligned = [[c.get(met, 0.0) for met in basis_names] for c in concs_list]
    concs = np.array(concs_aligned)[:, :n_metabs]

    # load data
    data_files = sorted([p for p in Path(config["path2data"]).iterdir() if p.is_file()])[: config["test_size"]]
    if len(data_files) == 0:
        raise RuntimeError("No data files found in path2data")

    data = np.array([mrs_io.read_FID(str(p)).mrs().FID for p in data_files])

    # load water if available
    water = None
    if config.get("path2water") and Path(config["path2water"]).is_dir():
        water_files = sorted(list(Path(config["path2water"]).iterdir()))[: config["test_size"]]
        if len(water_files) > 0:
            water = np.array([mrs_io.read_FID(str(p)).mrs().FID for p in water_files])

    # to frequency domain (stack real and imaginary part)
    data = np.fft.fft(data, axis=-1)
    data = np.stack((data.real, data.imag), axis=1)

    # fit
    lcm.set_save_path(config["path2save"])
    thetas, uncs = lcm(data, water)  # data in freq. domain, shape (batch, 2, sample_points)

    # loss: if water not provided, apply optimalReference
    if water is None:
        thetas = lcm.optimalReference(concs, thetas) * thetas

    loss = lcm.concsLoss(concs, thetas, type="ae")
    print("MAE:", float(loss.mean()))


if __name__ == "__main__":
    main()