####################################################################################################
#                                              io.py                                               #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 26/06/26                                                                                #
#                                                                                                  #
# Purpose: Flexible input handling for PyLCModel. Accepts NumPy arrays (complex FIDs, or           #
#          real/imag stacked spectra) in the time or frequency domain, NIfTI-MRS files (read        #
#          via the "nifti-mrs" package when available, falling back to nibabel), jMRUI text        #
#          files and LCModel ".RAW" files. Plus helpers to write/read the LCModel ".RAW"             #
#          format used to feed the executable.                                                     #
#                                                                                                  #
####################################################################################################

import os
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple, Union

import numpy as np


#*************************#
#   loaded-signal bundle  #
#*************************#
@dataclass
class Signals:
    """A batch of time-domain FIDs plus optional acquisition metadata."""

    fids: np.ndarray                      # complex, shape (batch, n_points)
    dwell: Optional[float] = None         # seconds
    central_freq: Optional[float] = None  # MHz


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

    "path" may be a single file path or a list/tuple of paths. When a list is
    given, every file is read and the FIDs are stacked along the batch axis
    (all files must share the same number of points; "dwell" and "central_freq"
    are taken from the first file).

    Reading prefers the dedicated "nifti-mrs" package (correct dwell-time unit
    handling and the NIfTI-MRS -> FSL conjugation convention). If it is not
    installed, it falls back to parsing the file directly with "nibabel".
    """
    if isinstance(path, (list, tuple)):
        if len(path) == 0:
            raise ValueError("read_nifti_mrs received an empty list of paths.")
        sigs = [_read_single_nifti_mrs(p) for p in path]
        n_points = sigs[0].fids.shape[-1]
        for p, s in zip(path, sigs):
            if s.fids.shape[-1] != n_points:
                raise ValueError(
                    f"NIfTI-MRS files have mismatched point counts: "
                    f"{n_points} vs {s.fids.shape[-1]} ({p})."
                )
        fids = np.concatenate([s.fids for s in sigs], axis=0)
        return Signals(fids=fids, dwell=sigs[0].dwell, central_freq=sigs[0].central_freq)
    return _read_single_nifti_mrs(path)


def _read_single_nifti_mrs(path: str) -> Signals:
    """Read a single NIfTI-MRS file into a Signals object."""
    try:
        from nifti_mrs.nifti_mrs import NIFTI_MRS
    except Exception:
        return _read_nifti_mrs_nibabel(path)

    nmrs = NIFTI_MRS(path)
    data = np.asarray(nmrs[:])                 # complex, spectral axis at index 3
    if not np.iscomplexobj(data):
        data = data.astype(np.complex64)

    n_points = data.shape[3]
    moved = np.moveaxis(data, 3, -1)           # spectral axis -> last
    fids = moved.reshape(-1, n_points)

    dwell = float(nmrs.dwelltime) if nmrs.dwelltime is not None else None
    central_freq = None
    sf = nmrs.spectrometer_frequency
    if sf is not None and len(sf) > 0:
        central_freq = float(sf[0])
    return Signals(fids=fids, dwell=dwell, central_freq=central_freq)


def _read_nifti_mrs_nibabel(path: str) -> Signals:
    """Fallback NIfTI-MRS reader using nibabel directly.

    NIfTI-MRS stores complex FIDs with the spectral dimension along axis 3 and the
    dwell time in "pixdim[4]". Spatial / higher dimensions are flattened to the batch.
    Note: the dwell time is read as-is (assumed seconds) and no conjugation convention
    is applied; install the "nifti-mrs" package for standard-compliant reading.
    """
    import nibabel as nib

    img = nib.load(path)
    data = np.asanyarray(img.dataobj)
    if not np.iscomplexobj(data):
        data = data.astype(np.complex64)

    if data.ndim < 4:
        raise ValueError(
            f"NIfTI-MRS data is expected to be >=4D (got {data.ndim}D, shape {data.shape})."
        )

    n_points = data.shape[3]
    # move spectral axis to the end, flatten everything else to a batch dimension
    moved = np.moveaxis(data, 3, -1)
    fids = moved.reshape(-1, n_points)

    dwell = None
    try:
        dwell = float(img.header["pixdim"][4])
    except Exception:
        dwell = None

    central_freq = _nifti_spectrometer_freq(img)
    return Signals(fids=fids, dwell=dwell, central_freq=central_freq)


def _nifti_spectrometer_freq(img) -> Optional[float]:
    """Extract SpectrometerFrequency (MHz) from the NIfTI-MRS JSON header extension."""
    import json

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
            if s.lower().startswith("signal") or s.lower().startswith("name"):
                continue
            parts = s.replace(",", " ").split()
            try:
                re_v = float(parts[0])
                im_v = float(parts[1]) if len(parts) > 1 else 0.0
            except (ValueError, IndexError):
                continue
            fid_rows.append(re_v + 1j * im_v)
    return np.asarray(fid_rows, dtype=np.complex128), meta


def jmrui_metadata(meta: dict):
    """Return (dwell_seconds, central_freq_MHz, echo_time) from jMRUI header fields."""
    dwell = None
    if "SamplingInterval" in meta:   # milliseconds in jMRUI
        try:
            dwell = float(meta["SamplingInterval"]) * 1e-3
        except ValueError:
            dwell = None
    central = None
    if "TransmitterFrequency" in meta:   # Hz -> MHz
        try:
            central = float(meta["TransmitterFrequency"]) / 1e6
        except ValueError:
            central = None
    return dwell, central, None


def read_jmrui(path: str) -> Signals:
    """Read a single jMRUI ".txt" FID file into a Signals object."""
    fid, meta = read_jmrui_txt(path)
    dwell, central, _ = jmrui_metadata(meta)
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
    elif isinstance(data, (list, tuple)) and len(data) > 0 and all(
        isinstance(p, str) and p.lower().endswith((".nii", ".nii.gz")) for p in data
    ):
        sig = read_nifti_mrs(list(data))
    else:
        sig = Signals(fids=_normalize_array(data))

    if domain == "freq":
        sig.fids = np.fft.ifft(sig.fids, axis=-1)

    if dwell is not None:
        sig.dwell = dwell
    if central_freq is not None:
        sig.central_freq = central_freq
    return sig


#***********************#
#   write to .RAW file   #
#***********************#
def to_raw(fid, file_path, header=" $NMID\n  id='', fmtdat='(2E15.6)'\n $END\n"):
    with open(file_path, "w") as file:
        file.write(header)
        for num in fid:
            file.write(f"  {num.real: .6E} {num.imag: .6E}\n")


#************************#
#   read from .RAW file   #
#************************#
def from_raw(path) -> np.ndarray:
    with open(path, "r") as f:
        lines = f.readlines()
        for i, line in enumerate(lines):
            if line.split()[0] == "$END":
                break
        fid = [complex(float(line.split()[0]), float(line.split()[1]))
               for line in lines[i + 1:] if len(line.split()) >= 2]
    return np.array(fid)
