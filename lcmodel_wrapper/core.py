####################################################################################################
#                                            core.py                                               #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 26/06/26                                                                                #
#                                                                                                  #
# Purpose: Python wrapper for the LCModel least-squares spectral fitting tool. Handles binary       #
#          resolution, flexible input (NumPy / NIfTI-MRS, time or frequency domain), control-file    #
#          generation and parsing of LCModel output.                                               #
#                                                                                                  #
# LCModel itself is a separate BSD-3-Clause program by Stephen Provencher (see LICENSE.lcmodel).   #
#                                                                                                  #
####################################################################################################

import multiprocessing as mp
import os
import shutil
import subprocess
import time

import numpy as np
from scipy.optimize import minimize

from . import binaries, control as control_mod, coord as coord_mod, io
from .basis import read_basis
from .convert import ensure_basis


#**************************************************************************************************#
#                                          Class PyLCModel                                         #
#**************************************************************************************************#
class PyLCModel:
    """Wrapper around the LCModel executable for batch MRS fitting.

    Parameters
    ----------
    path2basis : str
        Path to an LCModel ".basis" file, or (when convert_basis=True) a basis set in
        another format (jMRUI ".txt" folder, FSL-MRS ".json" folder, LCModel ".RAW"
        folder, or Osprey/FID-A ".mat") to convert.
    control : str, optional
        Path to an existing control file to use as a template.
    multiprocessing : bool
        Fit the batch across multiple processes.
    ppmlim : (float, float)
        Analysis window (ppmend, ppmst).
    conj : bool
        Conjugate the FIDs before fitting (convention dependent).
    ignore : str | list
        "default", "none" or a list of metabolite names to omit.
    save_path : str, optional
        Directory to keep intermediate files (otherwise a temporary one is used).
    path2exec : str, optional
        Explicit path to an LCModel executable. If omitted it is resolved automatically
        (cache -> download -> build).
    domain : {"time", "freq"}
        Domain of the input data passed at fit time. Defaults to "time" (FIDs).
    sample_points, bandwidth, central_freq : optional
        Override values otherwise read from the basis set.
    allow_download, allow_build : bool
        Permit automatic binary download / compilation during resolution.
    convert_basis : bool
        Convert path2basis to ".basis" if it is not already (experimental).
    basis_format : str, optional
        Force the source basis format for conversion ("jmrui", "fsl", "raw", "mat");
        auto-detected when omitted.
    """

    def __init__(self, path2basis, control=None, multiprocessing=False, ppmlim=(0.5, 4.2),
                 conj=True, ignore="default", save_path="", path2exec=None,
                 domain="time", sample_points=None, bandwidth=None, central_freq=None,
                 allow_download=True, allow_build=True, convert_basis=False,
                 basis_format=None, **kwargs):

        if convert_basis:
            conv_dwell = (1.0 / bandwidth) if bandwidth else None
            path2basis = ensure_basis(
                path2basis, fmt=basis_format, dwell=conv_dwell, central_freq=central_freq,
            )
        self.path2basis = path2basis
        self.basis = read_basis(path2basis)

        self.multiprocessing = multiprocessing
        self.save_path = save_path
        self.conj = conj
        if domain not in ("time", "freq"):
            raise ValueError("domain must be 'time' or 'freq'")
        self.domain = domain
        self.ppmlim = ppmlim

        self.sample_points = sample_points if sample_points is not None else self.basis.n_points
        self.bandwidth = bandwidth if bandwidth is not None else self.basis.bandwidth
        self.central_freq = central_freq if central_freq is not None else self.basis.central_freq

        if self.sample_points is None or self.bandwidth is None or self.central_freq is None:
            raise ValueError(
                "Could not determine sample_points / bandwidth / central_freq from the basis "
                "set. Please pass them explicitly."
            )

        # resolve the LCModel executable
        self.path2exec = binaries.resolve_executable(
            path2exec=path2exec, allow_download=allow_download, allow_build=allow_build,
        )

        ignore = control_mod.resolve_ignore(ignore)

        if control is not None:
            self.control = control_mod.load_control(control, path2basis, ppmlim, ignore)
        else:
            self.control = control_mod.build_control(
                path2basis, self.sample_points, self.bandwidth, self.central_freq,
                ppmlim=ppmlim, ignore=ignore,
            )

    #**********************#
    #   forward function   #
    #**********************#
    def __call__(self, *args, **kwargs):
        return self.forward(*args, **kwargs)

    #*************************#
    #   optimal referencing   #
    #*************************#
    def optimalReference(self, t, t_hat):
        w = np.ones(t.shape[0])
        for i in range(t.shape[0]):
            def err(wi):
                wi = np.clip(wi, 0, None)
                return np.abs(t[i] - wi * t_hat[i]).mean()

            w[i] = minimize(err, w[i], bounds=[(0, None)]).x
        return w[..., np.newaxis]

    #****************************#
    #   loss on concentrations   #
    #****************************#
    def concsLoss(self, t, t_hat, type="ae"):
        t = t[:, :self.basis.n_metabs]
        t_hat = t_hat[:, :self.basis.n_metabs]
        if type == "ae":
            return np.abs(t - t_hat)
        raise ValueError("Unknown loss type... Please use one of the predefined!")

    #*********************#
    #   input to output   #
    #*********************#
    def forward(self, x, x_ref=None, frac=None, x0=None):
        assert x0 is None, "Initial values not supported... (please set x0=None)"
        return self.lcmodel_minimize(x, x_ref, frac)

    #********************#
    #   LCModel fitting   #
    #********************#
    def lcmodel_minimize(self, x, x_ref=None, frac=None):
        # load + normalize input to time-domain FIDs
        signals = io.load_signals(x, domain=self.domain)
        fids = signals.fids

        if self.conj:
            fids = np.conjugate(fids)

        water = None
        if x_ref is not None:
            water = io.load_signals(x_ref, domain="time").fids
            if self.conj:
                water = np.conjugate(water)

        # create working directory
        if self.save_path in ("", None):
            path = os.getcwd() + os.sep + "tmp" + os.sep
        else:
            path = os.getcwd() + os.sep + self.save_path + os.sep
        if not os.path.exists(path):
            os.makedirs(path)

        if self.multiprocessing:
            tasks = [(fids[i], water, frac, i, path) for i in range(fids.shape[0])]
            with mp.Pool(None) as pool:
                thetas, crlbs = zip(*pool.starmap(self.lcm_forward, tasks))
        else:
            thetas, crlbs = [], []
            for i, fid in enumerate(fids):
                theta, crlb = self.lcm_forward(fid, water, frac, i, path)
                thetas.append(theta)
                crlbs.append(crlb)

        if self.save_path in ("", None):
            shutil.rmtree(path, ignore_errors=True)
        else:
            with open(f"{path + os.sep}control", "w") as file:
                file.write("\n".join(self.control))

        return np.array(thetas), np.array(crlbs)

    #*************************#
    #   run LCModel wrapper   #
    #*************************#
    def lcm_forward(self, fid, h2o=None, frac=None, idx=0, path=None):
        if path is None:
            path = os.getcwd() + os.sep + "tmp" + os.sep
        assert fid.shape[0] == self.sample_points, \
            "Number of points in FID does not match sample points!"

        io.to_raw(fid, f"{path + os.sep}temp{idx}.raw")

        if h2o is not None:
            io.to_raw(h2o[idx], f"{path + os.sep}temp{idx}.h2o")
            self.control = control_mod.set_key(self.control, "dows", "T")

        if frac is not None:
            wconc = (43300 * frac[idx]["GM"] + 35880 * frac[idx]["WM"] +
                     55556 * frac[idx]["CSF"]) / (1 - frac[idx]["CSF"])
            self.control = control_mod.set_key(self.control, "wconc", int(wconc))

        self.initiate(f"{path + os.sep}temp{idx}.raw")

        while not os.path.exists(f"{path + os.sep}temp{idx}.coord"):
            time.sleep(1e-3)

        metabs, concs, crlbs, tcr = coord_mod.read_coord(f"{path}temp{idx}.coord", meta=False)
        concs = [concs[metabs.index(met)] if met in metabs else 0.0
                 for met in self.basis.names]
        crlbs = [crlbs[metabs.index(met)] if met in metabs else 999.0
                 for met in self.basis.names]
        return concs, crlbs

    #******************************#
    #   initiate routine on .raw   #
    #******************************#
    def initiate(self, file_path):
        self.control = control_mod.set_key(self.control, "filraw", f"'{file_path}'")
        self.control = control_mod.set_key(self.control, "filps", f"'{file_path[:-4]}.ps'")
        self.control = control_mod.set_key(self.control, "filcoo", f"'{file_path[:-4]}.coord'")
        self.control = control_mod.set_key(self.control, "filh2o", f"'{file_path[:-4]}.h2o'")

        msg = "\n".join(self.control).encode("utf-8")
        proc = subprocess.Popen(
            [self.path2exec],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_value, stderr_value = proc.communicate(msg)
        if stdout_value:
            print(stdout_value.decode("utf-8", errors="ignore"))
        if stderr_value:
            print(stderr_value.decode("utf-8", errors="ignore"))

    #**************************#
    #   setter for save path   #
    #**************************#
    def set_save_path(self, path):
        self.save_path = path

    #*****************************#
    #   thin parsing delegators   #
    #*****************************#
    def read_LCModel_coord(self, path, coord=True, meta=True):
        return coord_mod.read_coord(path, coord=coord, meta=meta)

    def read_LCModel_fit(self, path):
        return coord_mod.read_fit(path)

    def to_raw(self, fid, file_path, header=" $NMID\n  id='', fmtdat='(2E15.6)'\n $END\n"):
        return io.to_raw(fid, file_path, header=header)

    def from_raw(self, path):
        return io.from_raw(path)
