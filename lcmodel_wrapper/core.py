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
        (cache -> download -> release download -> container -> build); the LCMODEL_EXEC
        environment variable is equivalent to passing it.
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
                 allow_download=True, allow_docker=True, allow_build=True,
                 convert_basis=False, basis_format=None, timeout=900, io_timeout=10):

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

        self.path2exec = binaries.resolve_executable(
            path2exec=path2exec, allow_download=allow_download, allow_build=allow_build,
            allow_docker=allow_docker,
        )
        self._containerised = binaries.is_container_shim(self.path2exec)
        self._check_visible(path2basis, "the basis set")

        # what the caller pinned, so that parameters later read off the data never
        # override an explicit choice
        self._explicit = {name for name, value in
                          (("sample_points", sample_points), ("bandwidth", bandwidth),
                           ("central_freq", central_freq)) if value is not None}
        self._templated = control is not None
        self._ignore = control_mod.resolve_ignore(ignore)

        if control is not None:
            self.control = control_mod.load_control(control, path2basis, ppmlim, self._ignore)
        else:
            self.control = self._build_control()

    #*************#
    #   helpers   #
    #*************#
    def _build_control(self):
        return control_mod.build_control(
            self.path2basis, self.sample_points, self.bandwidth, self.central_freq,
            ppmlim=self.ppmlim, ignore=self._ignore,
        )

    def _workdir(self):
        """Directory for LCModel's input and output files, with a trailing separator.

        An absolute save_path is used as given, a relative one is relative to the current
        directory, and an empty one means a throwaway "tmp" folder there.
        """
        return os.path.join(os.path.abspath(self.save_path or "tmp"), "")

    def _check_visible(self, path, what):
        if self._containerised and not container.can_see(path):
            raise ValueError(
                f"LCModel runs from a container, which only sees the working directory and "
                f"your home directory (on Windows: their drives); {what} is outside both: "
                f"{os.path.abspath(path)}"
            )

    #**********************#
    #   forward function   #
    #**********************#
    def __call__(self, *args, **kwargs):
        return self.forward(*args, **kwargs)

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
        if x0 is not None:
            raise ValueError("Initial values are not supported (x0 must be None).")
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
        for key, value in changed.items():
            setattr(self, key, value)
        if changed and not self._templated:   # a caller-supplied control file is left as given
            self.control = self._build_control()

    #********************#
    #   LCModel fitting   #
    #********************#
    def lcmodel_minimize(self, x, x_ref=None, frac=None):
        signals = io.load_signals(x, domain=self.domain)
        self._adopt_acquisition(signals, signals.fids.shape[-1])
        fids = np.conjugate(signals.fids) if self.conj else signals.fids

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

        path = self._workdir()
        os.makedirs(path, exist_ok=True)
        self._check_visible(path, "save_path")

        tasks = [(fid, water, frac, i, path) for i, fid in enumerate(fids)]
        if self.multiprocessing:
            with mp.Pool() as pool:
                reports = pool.starmap(self.lcm_forward, tasks)
        else:
            reports = [self.lcm_forward(*task) for task in tasks]

        if self.save_path:
            with open(os.path.join(path, "control"), "w") as fh:
                fh.write("\n".join(self.control))
        else:
            shutil.rmtree(path, ignore_errors=True)
        return reports

    #*************************#
    #   run LCModel wrapper   #
    #*************************#
    def lcm_forward(self, fid, h2o=None, frac=None, idx=0, path=None):
        if fid.shape[0] != self.sample_points:
            raise ValueError(f"FID has {fid.shape[0]} points but sample_points is "
                             f"{self.sample_points}.")
        stem = os.path.join(path or self._workdir(), f"temp{idx}")

        io.to_raw(fid, f"{stem}.raw")
        if h2o is not None:
            io.to_raw(h2o[idx], f"{stem}.h2o")
            control_mod.set_key(self.control, "dows", "T")
        if frac is not None:
            wconc = (43300 * frac[idx]["GM"] + 35880 * frac[idx]["WM"] +
                     55556 * frac[idx]["CSF"]) / (1 - frac[idx]["CSF"])
            control_mod.set_key(self.control, "wconc", int(wconc))

        output = self.initiate(f"{stem}.raw")
        _wait_for_file(f"{stem}.coord", self.io_timeout, output)
        return coord_mod.read_coord(f"{stem}.coord", meta=True)

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
        conc, crlb = dict(zip(metabs, concs)), dict(zip(metabs, crlbs))
        return ([conc.get(m, 0.0) for m in self.basis.names],
                [crlb.get(m, 999.0) for m in self.basis.names])

    #******************************#
    #   initiate routine on .raw   #
    #******************************#
    def initiate(self, file_path):
        """Run LCModel on a ".raw" file. Returns whatever LCModel printed."""
        stem = os.path.splitext(file_path)[0]
        for key, value in (("filraw", file_path), ("filps", f"{stem}.ps"),
                           ("filcoo", f"{stem}.coord"), ("filh2o", f"{stem}.h2o")):
            control_mod.set_key(self.control, key, f"'{value}'")

        # Only the copy piped to LCModel is rewritten for a container; the outputs are
        # still read back at the host paths kept in self.control.
        lines = container.translate_control(self.control) if self._containerised else self.control
        try:
            proc = subprocess.run([self.path2exec], input="\n".join(lines).encode("utf-8"),
                                  capture_output=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise LCModelError(f"LCModel did not finish within {self.timeout:g}s on "
                               f"{os.path.basename(file_path)}.") from None

        output = (proc.stdout + proc.stderr).decode("utf-8", errors="ignore")
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

    def to_raw(self, fid, file_path, header=io.RAW_HEADER):
        return io.to_raw(fid, file_path, header=header)

    def from_raw(self, path):
        return io.from_raw(path)
