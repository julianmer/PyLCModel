####################################################################################################
#                                              io.py                                               #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 26/06/26                                                                                #
#                                                                                                  #
# Purpose: Flexible input handling for PyLCModel. Accepts NumPy arrays (complex FIDs, or           #
#          real/imag stacked spectra) in the time or frequency domain, NIfTI-MRS files (read       #
#          via the "nifti-mrs" package when available, falling back to nibabel), jMRUI text        #
#          files and LCModel ".RAW" files. Plus helpers to write/read the LCModel ".RAW"           #
#          format used to feed the executable.                                                     #
#                                                                                                  #
####################################################################################################

import json
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

import numpy as np

RAW_HEADER = " $NMID\n  id='', fmtdat='(2E15.6)'\n $END\n"


#*************************#
#   loaded-signal bundle  #
#*************************#
@dataclass
class Signals:
    """A batch of time-domain FIDs plus optional acquisition metadata."""

    fids: np.ndarray                      # complex, shape (batch, n_points)
    dwell: Optional[float] = None         # seconds
    central_freq: Optional[float] = None  # MHz


def _stack(sigs: List[Signals], what: str) -> Signals:
    """Concatenate a list of Signals along the batch axis; metadata from the first."""
    if not sigs:
        raise ValueError(f"No {what} to load.")
    n_points = sigs[0].fids.shape[-1]
    for s in sigs:
        if s.fids.shape[-1] != n_points:
            raise ValueError(f"{what} have mismatched point counts: "
                             f"{n_points} vs {s.fids.shape[-1]}.")
    return Signals(fids=np.concatenate([s.fids for s in sigs], axis=0),
                   dwell=sigs[0].dwell, central_freq=sigs[0].central_freq)


#*******************************#
#   numpy shape normalization   #
#*******************************#
def _normalize_array(arr: np.ndarray) -> np.ndarray:
    """Return a complex array of shape (batch, n_points) from a variety of layouts."""
    arr = np.asarray(arr)

    if np.iscomplexobj(arr):
        if arr.ndim == 1:
            return arr[np.newaxis, :]
        if arr.ndim == 2:
            return arr
        raise ValueError(f"Unsupported complex array shape: {arr.shape}")

    # real-valued: interpret a length-2 axis as (real, imag)
    if arr.ndim == 1:
        # purely real signal -> imaginary part 0
        return (arr + 0j)[np.newaxis, :]
    if arr.ndim == 2:
        if arr.shape[0] == 2:                       # (2, n_points)
            return (arr[0] + 1j * arr[1])[np.newaxis, :]
        if arr.shape[1] == 2:                       # (n_points, 2)
            return (arr[:, 0] + 1j * arr[:, 1])[np.newaxis, :]
        return (arr + 0j)                           # (batch, n_points), real only
    if arr.ndim == 3 and arr.shape[1] == 2:         # (batch, 2, n_points)
        return arr[:, 0] + 1j * arr[:, 1]
    if arr.ndim == 3 and arr.shape[2] == 2:         # (batch, n_points, 2)
        return arr[..., 0] + 1j * arr[..., 1]
    raise ValueError(f"Unsupported array shape for MRS data: {arr.shape}")


#**********************#
#   NIfTI-MRS reader   #
#**********************#
def read_nifti_mrs(path: Union[str, Sequence[str]]) -> Signals:
    """Read one or more NIfTI-MRS files into time-domain FIDs.

    "path" may be a single file path or a list/tuple of paths. When a list is given,
    every file is read and the FIDs are stacked along the batch axis (all files must
    share the same number of points; "dwell" and "central_freq" come from the first).

    Reading prefers the dedicated "nifti-mrs" package (correct dwell-time unit handling
    and the NIfTI-MRS -> FSL conjugation convention). If it is not installed, it falls
    back to parsing the file directly with "nibabel".
    """
    if isinstance(path, (list, tuple)):
        return _stack([_read_single_nifti_mrs(p) for p in path], "NIfTI-MRS files")
    return _read_single_nifti_mrs(path)


def _is_nifti_mrs(obj) -> bool:
    """Whether obj is a NIfTI-MRS object, or a batched wrapper of them.

    Recognised by what it exposes rather than by its type: FSL-MRS subclasses NIFTI_MRS
    and NIfTI-MRS+ wraps a list of them, and neither is importable from here.
    """
    if hasattr(obj, "list") and hasattr(obj, "dwelltime"):
        return True
    return all(hasattr(obj, a) for a in
               ("dwelltime", "spectrometer_frequency", "__getitem__", "shape"))


def from_nifti_mrs(nmrs) -> Signals:
    """Build Signals from an already-loaded NIfTI-MRS object.

    Accepts a "nifti_mrs.NIFTI_MRS", anything subclassing it (FSL-MRS extends it), or a
    batched wrapper exposing "list()", such as NIfTI-MRS+. Writing the object out and
    reading it back would lose nothing, but it would send data that is already in
    memory on a round trip through disk.
    """
    if hasattr(nmrs, "list"):                  # a batched wrapper, e.g. NIfTI-MRS+
        return _stack([from_nifti_mrs(one) for one in nmrs.list()], "NIfTI-MRS objects")
    return _signals_from_nifti_mrs(nmrs)


def _read_single_nifti_mrs(path: str) -> Signals:
    try:
        from nifti_mrs.nifti_mrs import NIFTI_MRS
    except Exception:
        return _read_nifti_mrs_nibabel(path)
    return _signals_from_nifti_mrs(NIFTI_MRS(path))


def _fids_from_nifti(data: np.ndarray) -> np.ndarray:
    """NIfTI-MRS keeps the spectral axis at index 3; flatten everything else to a batch."""
    if not np.iscomplexobj(data):
        data = data.astype(np.complex64)
    return np.moveaxis(data, 3, -1).reshape(-1, data.shape[3])


def _signals_from_nifti_mrs(nmrs) -> Signals:
    """Pull the FIDs and acquisition parameters out of a NIfTI-MRS object."""
    dwell = float(nmrs.dwelltime) if nmrs.dwelltime is not None else None
    sf = nmrs.spectrometer_frequency
    central_freq = float(sf[0]) if sf is not None and len(sf) > 0 else None
    return Signals(fids=_fids_from_nifti(np.asarray(nmrs[:])), dwell=dwell,
                   central_freq=central_freq)


def _read_nifti_mrs_nibabel(path: str) -> Signals:
    """Fallback NIfTI-MRS reader using nibabel directly.

    The dwell time is "pixdim[4]", read as-is (assumed seconds), and no conjugation
    convention is applied; install the "nifti-mrs" package for standard-compliant reading.
    """
    import nibabel as nib

    img = nib.load(path)
    data = np.asanyarray(img.dataobj)
    if data.ndim < 4:
        raise ValueError(
            f"NIfTI-MRS data is expected to be >=4D (got {data.ndim}D, shape {data.shape})."
        )
    try:
        dwell = float(img.header["pixdim"][4])
    except Exception:
        dwell = None
    return Signals(fids=_fids_from_nifti(data), dwell=dwell,
                   central_freq=_nifti_spectrometer_freq(img))


def _nifti_spectrometer_freq(img) -> Optional[float]:
    """Extract SpectrometerFrequency (MHz) from the NIfTI-MRS JSON header extension."""
    try:
        for ext in img.header.extensions:
            if getattr(ext, "get_code", lambda: None)() in (44, "44"):
                meta = json.loads(ext.get_content().decode("utf-8", errors="ignore"))
                freq = meta.get("SpectrometerFrequency")
                if isinstance(freq, (list, tuple)):
                    freq = freq[0]
                return float(freq) if freq is not None else None
    except Exception:
        return None
    return None


#***********************#
#   jMRUI text reader   #
#***********************#
def read_jmrui_txt(path: str) -> Tuple[np.ndarray, dict]:
    """Read a single jMRUI ".txt" file -> (complex FID, metadata dict)."""
    meta = {}
    fid_rows = []
    in_data = False
    with open(path, "r", errors="ignore") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            if not in_data:
                if ":" in s and not s[0].isdigit() and s[0] != "-":
                    key, _, val = s.partition(":")
                    meta[key.strip()] = val.strip()
                if s.lower().startswith("sig(real)") or "fft(real)" in s.lower():
                    in_data = True
                continue
            if s.lower().startswith(("signal", "name")):
                continue
            parts = s.replace(",", " ").split()
            try:
                re_v = float(parts[0])
                im_v = float(parts[1]) if len(parts) > 1 else 0.0
            except (ValueError, IndexError):
                continue
            fid_rows.append(re_v + 1j * im_v)
    return np.asarray(fid_rows, dtype=np.complex128), meta


def jmrui_metadata(meta: dict) -> Tuple[Optional[float], Optional[float]]:
    """Return (dwell_seconds, central_freq_MHz) from jMRUI header fields, if present."""
    def number(key, scale):
        try:
            return float(meta[key]) * scale
        except (KeyError, ValueError):
            return None
    return number("SamplingInterval", 1e-3), number("TransmitterFrequency", 1e-6)  # ms, Hz


def read_jmrui(path: str) -> Signals:
    """Read a single jMRUI ".txt" FID file into a Signals object."""
    fid, meta = read_jmrui_txt(path)
    dwell, central = jmrui_metadata(meta)
    return Signals(fids=fid[np.newaxis, :], dwell=dwell, central_freq=central)


#*****************#
#   main loader   #
#*****************#
def load_signals(data, domain: str = "time", dwell: Optional[float] = None,
                 central_freq: Optional[float] = None) -> Signals:
    """Load MRS data from a NumPy array, NIfTI-MRS file or ".RAW" file.

    "domain" describes the domain of the *input* ("time" for FIDs, "freq" for
    spectra). The returned signals are always time-domain FIDs.
    """
    if domain not in ("time", "freq"):
        raise ValueError("domain must be 'time' or 'freq'")

    if isinstance(data, str):
        lower = data.lower()
        if lower.endswith((".nii", ".nii.gz")):
            sig = read_nifti_mrs(data)
        elif lower.endswith(".txt"):
            sig = read_jmrui(data)
        elif lower.endswith((".raw", ".h2o")):
            sig = Signals(fids=from_raw(data)[np.newaxis, :])
        else:
            raise ValueError(f"Unsupported file type: {data}")
    elif isinstance(data, (list, tuple)) and data and all(
        isinstance(p, str) and p.lower().endswith((".nii", ".nii.gz")) for p in data
    ):
        sig = read_nifti_mrs(list(data))
    elif _is_nifti_mrs(data):
        # already loaded: keep its dwell time and central frequency rather than making
        # the caller re-supply what the object already knows
        sig = from_nifti_mrs(data)
    else:
        sig = Signals(fids=_normalize_array(data))

    if domain == "freq":
        sig.fids = np.fft.ifft(sig.fids, axis=-1)

    if dwell is not None:
        sig.dwell = dwell
    if central_freq is not None:
        sig.central_freq = central_freq
    return sig


#*************************#
#   LCModel .RAW files    #
#*************************#
def to_raw(fid, file_path, header=RAW_HEADER):
    with open(file_path, "w") as fh:
        fh.write(header)
        for num in fid:
            fh.write(f"  {num.real: .6E} {num.imag: .6E}\n")


def from_raw(path) -> np.ndarray:
    """Read the complex points that follow the "$END" of the header namelist."""
    fid = []
    in_data = False
    with open(path, "r") as fh:
        for line in fh:
            parts = line.split()
            if not in_data:
                in_data = parts[:1] == ["$END"]
            elif len(parts) >= 2:
                fid.append(complex(float(parts[0]), float(parts[1])))
    return np.array(fid)
