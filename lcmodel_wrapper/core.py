####################################################################################################
#                                            core.py                                               #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 26/06/26                                                                                #
#                                                                                                  #
# Purpose: Python wrapper for the LCModel least-squares spectral fitting tool. Handles binary      #
#          resolution, flexible input (NumPy / NIfTI-MRS, time or frequency domain), control-file  #
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

from . import binaries, container, control as control_mod, coord as coord_mod, io
from .basis import read_basis
from .convert import ensure_basis


#**************************************************************************************************#
#                                       LCModel run failure                                        #
#**************************************************************************************************#
class LCModelError(RuntimeError):
    """Raised when an LCModel run does not produce the expected output.

    LCModel is Fortran and its fatal error paths end in a bare STOP, which exits 0, so
    the exit status carries no information. Failure is detected by the absence of the
    ".coord" file and reported with whatever LCModel printed.
    """


def _wait_for_file(path, timeout, output=""):
    """Wait for LCModel to produce "path", or raise with its diagnostics.

    By the time this is called the process has already been reaped, so the file normally
    exists immediately; the wait only covers metadata lag on network filesystems.
    """
    deadline = time.monotonic() + timeout
    while not os.path.exists(path):
        if time.monotonic() > deadline:
            detail = output.strip() or "(LCModel produced no output)"
            raise LCModelError(
                f"LCModel did not write {os.path.basename(path)} within {timeout:g}s.\n"
                f"LCModel said:\n{detail}"
            )
        time.sleep(1e-3)


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
        (cache -> download -> release download -> container -> build).
    domain : {"time", "freq"}
        Domain of the input data passed at fit time. Defaults to "time" (FIDs).
    sample_points, bandwidth, central_freq : optional
        Override values otherwise read from the basis set.
    allow_download, allow_docker, allow_build : bool
        Permit automatic binary download / container use / compilation during
        resolution. With a container (docker or podman), LCModel runs from an image
        and only sees the working directory and your home directory - keep the basis
        set and any absolute save_path under one of those.
    convert_basis : bool
        Convert path2basis to ".basis" if it is not already (experimental).
    basis_format : str, optional
        Force the source basis format for conversion ("jmrui", "fsl", "raw", "mat");
        auto-detected when omitted.
    timeout : float
        Seconds to allow a single LCModel run before killing it.
    io_timeout : float
        Seconds to wait for the ".coord" output after LCModel exits. Only covers metadata
        lag on network filesystems; raises "LCModelError" with LCModel's diagnostics when
        the fit produced nothing.
    """

    def __init__(self, path2basis, control=None, multiprocessing=False, ppmlim=(0.5, 4.2),
                 conj=True, ignore="default", save_path="", path2exec=None,
                 domain="time", sample_points=None, bandwidth=None, central_freq=None,
                 allow_download=True, allow_build=True, convert_basis=False,
                 basis_format=None, timeout=900, io_timeout=10, allow_docker=True,
                 **kwargs):

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
        self.timeout = timeout
        self.io_timeout = io_timeout
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
            allow_docker=allow_docker,
        )
        self._containerised = binaries.is_container_shim(self.path2exec)
        if self._containerised and not container.can_see(path2basis):
            raise ValueError(
                f"LCModel is running from a container, which can only see the working "
                f"directory and your home directory (on Windows: their drives); the basis "
                f"set is outside both: {os.path.abspath(path2basis)}. Move or copy it "
                f"under one of those."
            )

        ignore = control_mod.resolve_ignore(ignore)

        # what the caller pinned, so that parameters later read off the data never
        # override an explicit choice
        self._explicit = {name for name, value in
                          (("sample_points", sample_points), ("bandwidth", bandwidth),
                           ("central_freq", central_freq)) if value is not None}
        self._templated = control is not None
        self._ignore = ignore

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
        """Fit and return (concentrations, CRLBs), both in basis-set order.

        See "report" for everything else LCModel worked out along the way.
        """
        reports = self.report(x, x_ref, frac, x0)
        concs, crlbs = zip(*(self._aligned(r) for r in reports))
        return np.array(concs), np.array(crlbs)

    #***************************#
    #   full LCModel report     #
    #***************************#
    def report(self, x, x_ref=None, frac=None, x0=None):
        """Fit and return everything LCModel reported, one entry per spectrum.

        Each entry is what "coord.read_coord" produces with meta on:
        (metabolites, concentrations, %SD, /Cr+PCr ratios, FWHM, S/N, shift, phase).
        "forward" keeps only the first three; the creatine ratios and the quality
        metrics are here, and they are what make one fit comparable with another.
        """
        assert x0 is None, "Initial values not supported... (please set x0=None)"
        return self.lcmodel_minimize(x, x_ref, frac)

    #*******************************#
    #   take parameters from data   #
    #*******************************#
    def _adopt_acquisition(self, signals, n_points):
        """Take the acquisition parameters from the data when it carries them.

        Until data arrives these can only come from the basis set, which is often
        simulated at a different resolution: the challenge basis has 4096 points where
        its spectra have 2048. LCModel reads nunfil, deltat and hzpppm from the control
        file and they describe the *data*, so the control is rebuilt when the two differ.
        Anything the caller passed explicitly to __init__ still wins.
        """
        wanted = {"sample_points": n_points}
        if signals.dwell:
            wanted["bandwidth"] = 1.0 / signals.dwell
        if signals.central_freq:
            wanted["central_freq"] = signals.central_freq

        changed = {k: v for k, v in wanted.items()
                   if k not in self._explicit and getattr(self, k) != v}
        if not changed:
            return

        for key, value in changed.items():
            setattr(self, key, value)
        if self._templated:
            return          # a caller-supplied control file is left exactly as given

        self.control = control_mod.build_control(
            self.path2basis, self.sample_points, self.bandwidth, self.central_freq,
            ppmlim=self.ppmlim, ignore=self._ignore,
        )

    #********************#
    #   LCModel fitting   #
    #********************#
    def lcmodel_minimize(self, x, x_ref=None, frac=None):
        # load + normalize input to time-domain FIDs
        signals = io.load_signals(x, domain=self.domain)
        fids = signals.fids

        self._adopt_acquisition(signals, fids.shape[-1])

        if self.conj:
            fids = np.conjugate(fids)

        water = None
        if x_ref is not None:
            water = io.load_signals(x_ref, domain="time").fids
            if self.conj:
                water = np.conjugate(water)
            # one reference per spectrum, which is how they are acquired. Reusing a
            # single reference across a batch is not a convenience worth having: the
            # batch is usually separate acquisitions, and scaling them all by one
            # subject's water would be wrong without being visibly wrong.
            if water.shape[0] != fids.shape[0]:
                raise ValueError(
                    f"{water.shape[0]} water references for {fids.shape[0]} spectra; "
                    f"supply one per spectrum."
                )

        # create working directory. An absolute save_path is used as given; a relative
        # one stays relative to the current directory, as before.
        if self.save_path in ("", None):
            path = os.path.join(os.getcwd(), "tmp") + os.sep
        else:
            path = os.path.join(os.getcwd(), self.save_path) + os.sep
        if not os.path.exists(path):
            os.makedirs(path)
        if self._containerised and not container.can_see(path):
            raise ValueError(
                f"LCModel is running from a container, which can only see the working "
                f"directory and your home directory; save_path is outside both: {path}"
            )

        if self.multiprocessing:
            tasks = [(fids[i], water, frac, i, path) for i in range(fids.shape[0])]
            with mp.Pool(None) as pool:
                reports = list(pool.starmap(self.lcm_forward, tasks))
        else:
            reports = [self.lcm_forward(fid, water, frac, i, path)
                       for i, fid in enumerate(fids)]

        if self.save_path in ("", None):
            shutil.rmtree(path, ignore_errors=True)
        else:
            with open(f"{path + os.sep}control", "w") as file:
                file.write("\n".join(self.control))

        return reports

    #*************************#
    #   run LCModel wrapper   #
    #*************************#
    def lcm_forward(self, fid, h2o=None, frac=None, idx=0, path=None):
        if path is None:
            path = os.path.join(os.getcwd(), "tmp")
        # normalised here so that a path without a trailing separator works: the .raw
        # was written to path + os.sep while the .coord was read from bare path
        path = os.path.join(path, "")
        assert fid.shape[0] == self.sample_points, \
            "Number of points in FID does not match sample points!"

        io.to_raw(fid, f"{path}temp{idx}.raw")

        if h2o is not None:
            io.to_raw(h2o[idx], f"{path}temp{idx}.h2o")
            self.control = control_mod.set_key(self.control, "dows", "T")

        if frac is not None:
            wconc = (43300 * frac[idx]["GM"] + 35880 * frac[idx]["WM"] +
                     55556 * frac[idx]["CSF"]) / (1 - frac[idx]["CSF"])
            self.control = control_mod.set_key(self.control, "wconc", int(wconc))

        output = self.initiate(f"{path}temp{idx}.raw")

        coord_path = f"{path}temp{idx}.coord"
        _wait_for_file(coord_path, self.io_timeout, output)

        return coord_mod.read_coord(coord_path, meta=True)

    #************************#
    #   align to the basis   #
    #************************#
    def _aligned(self, report):
        """A parsed report as (concentrations, CRLBs) in basis-set order.

        LCModel omits a metabolite it could not quantify, so the table is not the basis
        set: aligning it keeps every fit in a batch the same width and the same order.
        A missing concentration reads as zero and its CRLB as 999, LCModel's own way of
        saying "no information".
        """
        metabs, concs, crlbs = report[0], report[1], report[2]
        return ([concs[metabs.index(m)] if m in metabs else 0.0 for m in self.basis.names],
                [crlbs[metabs.index(m)] if m in metabs else 999.0 for m in self.basis.names])

    #******************************#
    #   initiate routine on .raw   #
    #******************************#
    def initiate(self, file_path):
        """Run LCModel on a ".raw" file. Returns whatever LCModel printed."""
        self.control = control_mod.set_key(self.control, "filraw", f"'{file_path}'")
        self.control = control_mod.set_key(self.control, "filps", f"'{file_path[:-4]}.ps'")
        self.control = control_mod.set_key(self.control, "filcoo", f"'{file_path[:-4]}.coord'")
        self.control = control_mod.set_key(self.control, "filh2o", f"'{file_path[:-4]}.h2o'")

        # Only the copy piped to LCModel is rewritten for a container; the outputs are
        # still read back at the host paths kept in self.control.
        lines = container.translate_control(self.control) if self._containerised else self.control
        msg = "\n".join(lines).encode("utf-8")
        proc = subprocess.Popen(
            [self.path2exec],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout_value, stderr_value = proc.communicate(msg, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise LCModelError(
                f"LCModel did not finish within {self.timeout:g}s on "
                f"{os.path.basename(file_path)}."
            )

        output = "".join(
            v.decode("utf-8", errors="ignore")
            for v in (stdout_value, stderr_value) if v
        )
        if output:
            print(output)
        return output

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
