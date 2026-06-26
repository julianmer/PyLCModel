####################################################################################################
#                                            convert.py                                            #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 26/06/26                                                                                #
#                                                                                                  #
# Purpose: Best-effort conversion of common MRS basis-set formats to the LCModel ".basis" format,  #
#          inspired by the MRS Basis Set Conversion Toolbox                                        #
#          (https://github.com/igweckay/MRS-Basis-Set-Conversion-Toolbox). Supported inputs:       #
#          an existing ".basis" file (pass-through), a jMRUI/AQSES/QUEST ".txt" folder, an         #
#          FSL-MRS ".json" folder, an LCModel ".RAW" folder, and an Osprey/FID-A ".mat" struct.    #
#          The output is NOT independently validated against LCModel makebasis -- verify your      #
#          fits.                                                                                    #
#                                                                                                  #
####################################################################################################

import json
import os
import re
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

import numpy as np

from .io import read_jmrui_txt, jmrui_metadata




#************************#
#   .basis writer        #
#************************#
def _fmt_block(values: np.ndarray) -> str:
    """Format a flat float array as Fortran "(6E13.5)" lines."""
    out = []
    for i in range(0, len(values), 6):
        chunk = values[i:i + 6]
        out.append("".join(f"{v:13.5E}" for v in chunk))
    return "\n".join(out)


def write_basis(out_path: str, names: List[str], fids: List[np.ndarray],
                dwell: float, central_freq: float, echo_time: float = -1.0) -> str:
    """Write metabolite FIDs to an LCModel ".basis" file (experimental)."""
    n_points = len(fids[0])
    with open(out_path, "w") as fh:
        fh.write(" $SEQPAR\n")
        fh.write(" FWHMBA = -1.,\n")
        fh.write(f" HZPPPM = {central_freq:.6f},\n")
        fh.write(f" ECHOT = {echo_time},\n")
        fh.write(" SEQ = ' '\n")
        fh.write(" $END\n")
        fh.write(" $BASIS1\n")
        fh.write(" IDBASI = 'lcmodel_wrapper',\n")
        fh.write(" FMTBAS = '(6E13.5)',\n")
        fh.write(f" BADELT = {dwell:.12f},\n")
        fh.write(f" NDATAB = {n_points}\n")
        fh.write(" $END\n")

        for name, fid in zip(names, fids):
            fh.write(" $BASIS\n")
            fh.write(f" ID = '{name}',\n")
            fh.write(f" METABO = '{name}',\n")
            fh.write(" CONC = 1.,\n")
            fh.write(" TRAMP = 1.,\n")
            fh.write(" VOLUME = 1.,\n")
            fh.write(" ISHIFT = 0\n")
            fh.write(" $END\n")
            interleaved = np.empty(2 * n_points, dtype=np.float64)
            interleaved[0::2] = np.real(fid)
            interleaved[1::2] = np.imag(fid)
            fh.write(_fmt_block(interleaved) + "\n")
    return out_path


#*****************************#
#   parsed-basis container     #
#*****************************#
class ParsedBasis(NamedTuple):
    names: List[str]
    fids: List[np.ndarray]
    dwell: Optional[float]
    central: Optional[float]
    echot: Optional[float]


def _stack(parsed: ParsedBasis) -> None:
    """Validate a ParsedBasis in place (consistent, non-empty point counts)."""
    if not parsed.fids:
        raise ValueError("No metabolite FIDs were found in the input.")
    n = parsed.fids[0].size
    if n == 0:
        raise ValueError("The first metabolite FID is empty.")
    for name, fid in zip(parsed.names, parsed.fids):
        if fid.size != n:
            raise ValueError(
                f"Inconsistent point count for '{name}': {fid.size} vs {n}."
            )


#*****************************#
#   jMRUI / AQSES / QUEST      #
#*****************************#
def _read_jmrui_folder(folder: str) -> ParsedBasis:
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".txt"))
    if not files:
        raise ValueError(f"No .txt files found in jMRUI folder: {folder}")

    names, fids = [], []
    dwell = central = echot = None
    for f in files:
        fid, meta = read_jmrui_txt(os.path.join(folder, f))
        if fid.size == 0:
            continue
        d, c, e = jmrui_metadata(meta)
        dwell = dwell if dwell is not None else d
        central = central if central is not None else c
        echot = echot if echot is not None else e
        names.append(os.path.splitext(f)[0])
        fids.append(fid)
    return ParsedBasis(names, fids, dwell, central, echot)


#*****************************#
#   FSL-MRS .json folder       #
#*****************************#
def _read_fsl_folder(folder: str) -> ParsedBasis:
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".json"))
    if not files:
        raise ValueError(f"No .json files found in FSL-MRS folder: {folder}")

    names, fids = [], []
    dwell = central = None
    for f in files:
        with open(os.path.join(folder, f), "r") as fh:
            obj = json.load(fh)
        block = obj.get("basis", obj)
        if "basis_re" not in block:
            continue
        # FSL-MRS stores the conjugated FID; conjugate back to the canonical FID.
        fid = (np.asarray(block["basis_re"], dtype=np.float64)
               + 1j * np.asarray(block["basis_im"], dtype=np.float64)).conj()
        names.append(block.get("basis_name") or os.path.splitext(f)[0])
        fids.append(fid)
        if dwell is None:
            dwell = block.get("basis_dwell")
        if central is None:
            central = block.get("basis_centre")   # already in MHz
    return ParsedBasis(names, fids, dwell, central, None)


#*****************************#
#   LCModel .RAW folder        #
#*****************************#
_NUM_RE = re.compile(r"[-+]?\d*\.?\d+(?:[eEdD][-+]?\d+)?")


def _read_raw_file(path: str) -> Tuple[np.ndarray, Dict[str, str]]:
    keys: Dict[str, str] = {}
    rows = []
    with open(path, "r", errors="ignore") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("$"):
                continue
            if "=" in s:
                key, _, val = s.partition("=")
                keys[key.strip().upper()] = val.strip().rstrip(",").strip().strip("'")
                continue
            nums = _NUM_RE.findall(s.replace("D", "E").replace("d", "e"))
            if len(nums) >= 2:
                rows.append(float(nums[0]) + 1j * float(nums[1]))
    return np.asarray(rows, dtype=np.complex128), keys


def _read_raw_folder(folder: str) -> ParsedBasis:
    files = sorted(
        f for f in os.listdir(folder) if f.lower().endswith((".raw", ".basis_raw"))
    )
    if not files:
        raise ValueError(f"No .RAW files found in LCModel raw folder: {folder}")

    names, fids = [], []
    dwell = central = echot = None
    for f in files:
        fid, keys = _read_raw_file(os.path.join(folder, f))
        if fid.size == 0:
            continue
        names.append(keys.get("METABO") or keys.get("ID") or os.path.splitext(f)[0])
        fids.append(fid)
        if dwell is None and "BADELT" in keys:
            dwell = float(keys["BADELT"])
        if central is None and "HZPPPM" in keys:
            central = float(keys["HZPPPM"])
        if echot is None and "ECHOT" in keys:
            echot = float(keys["ECHOT"])
    return ParsedBasis(names, fids, dwell, central, echot)


#*****************************#
#   Osprey / FID-A .mat        #
#*****************************#
def _mat_attr(struct, *candidates):
    for name in candidates:
        if hasattr(struct, name):
            return getattr(struct, name)
    return None


def _read_mat(path: str) -> ParsedBasis:
    try:
        from scipy.io import loadmat
    except ImportError as exc:   # pragma: no cover
        raise ImportError("Reading .mat basis sets requires scipy.") from exc
    try:
        mat = loadmat(path, squeeze_me=True, struct_as_record=False)
    except NotImplementedError as exc:   # MATLAB v7.3 (HDF5)
        raise ValueError(
            "MATLAB v7.3 .mat files are not supported; re-save as v7 in MATLAB "
            "(save(..., '-v7')) or use the MRS Basis Set Conversion Toolbox."
        ) from exc

    struct = mat.get("BASIS")
    if struct is None:
        struct = next(
            (v for k, v in mat.items()
             if not k.startswith("__") and hasattr(v, "_fieldnames")),
            None,
        )
    if struct is None:
        raise ValueError(f"No basis struct found in {path}.")

    raw_fids = _mat_attr(struct, "fids", "data")
    if raw_fids is None:
        raise ValueError(f"Could not find FID data in {path}.")
    fids_arr = np.asarray(raw_fids, dtype=np.complex128)
    if fids_arr.ndim == 1:
        fids_arr = fids_arr[:, None]
    # orient as (n_points, n_metabs)
    if fids_arr.shape[0] < fids_arr.shape[1]:
        fids_arr = fids_arr.T
    fids = [fids_arr[:, i] for i in range(fids_arr.shape[1])]

    raw_names = _mat_attr(struct, "names", "name")
    names = [str(n).strip() for n in np.atleast_1d(raw_names)] if raw_names is not None \
        else [f"metab{i}" for i in range(len(fids))]

    dwell = _mat_attr(struct, "dwelltime", "dwell")
    if dwell is None:
        sw = _mat_attr(struct, "spectralwidth", "sw")
        dwell = (1.0 / float(sw)) if sw else None
    central = _mat_attr(struct, "txfrq", "Bo_freq")
    if central is not None:
        central = float(central) / 1e6
    else:
        bo = _mat_attr(struct, "Bo", "B0")
        central = float(bo) * 42.5774 if bo else None
    echot = _mat_attr(struct, "te", "echotime")
    return ParsedBasis(
        names[:len(fids)], fids,
        float(dwell) if dwell else None,
        float(central) if central else None,
        float(echot) if echot else None,
    )


#*****************************#
#   format detection           #
#*****************************#
_READERS: Dict[str, Callable[[str], ParsedBasis]] = {
    "jmrui": _read_jmrui_folder,
    "fsl": _read_fsl_folder,
    "raw": _read_raw_folder,
    "mat": _read_mat,
}

_ALIASES = {
    "aqses": "jmrui", "quest": "jmrui", "txt": "jmrui",
    "json": "fsl", "fsl-mrs": "fsl", "fslmrs": "fsl",
    "lcmodel": "raw", ".raw": "raw",
    "osprey": "mat", "fida": "mat", "fid-a": "mat", "inspector": "mat",
}


def detect_format(path: str) -> str:
    """Return the basis format key ("jmrui", "fsl", "raw", "mat") for "path"."""
    if os.path.isfile(path):
        if path.lower().endswith(".mat"):
            return "mat"
        raise ValueError(
            f"Cannot auto-detect a single-file basis format for '{path}'. "
            "Pass fmt= explicitly or provide a folder."
        )
    if os.path.isdir(path):
        lower = [f.lower() for f in os.listdir(path)]
        if any(f.endswith(".json") for f in lower):
            return "fsl"
        if any(f.endswith((".raw", ".basis_raw")) for f in lower):
            return "raw"
        if any(f.endswith(".txt") for f in lower):
            return "jmrui"
        raise ValueError(f"No recognised basis files found in folder: {path}")
    raise ValueError(f"Path does not exist: {path}")


#*****************************#
#   public entry              #
#*****************************#
def convert_to_basis(path: str, out_path: Optional[str] = None, fmt: Optional[str] = None,
                     dwell: Optional[float] = None, central_freq: Optional[float] = None,
                     echo_time: Optional[float] = None) -> str:
    """Convert a basis set in "path" to an LCModel ".basis" file.

    Parameters
    ----------
    path : str
        A jMRUI/AQSES/QUEST ".txt" folder, an FSL-MRS ".json" folder, an LCModel
        ".RAW" folder, or an Osprey/FID-A ".mat" file.
    out_path : str, optional
        Output ".basis" path (defaults next to the input).
    fmt : str, optional
        Force a format ("jmrui", "fsl", "raw", "mat", or an alias). Auto-detected
        when omitted.
    dwell, central_freq, echo_time : float, optional
        Fallback acquisition parameters used when the input format does not store them
        (e.g. bare LCModel ".RAW" files).
    """
    key = _ALIASES.get(fmt.lower(), fmt.lower()) if fmt else detect_format(path)
    if key not in _READERS:
        raise ValueError(f"Unknown basis format '{fmt}'. Choose from {sorted(_READERS)}.")

    parsed = _READERS[key](path)
    _stack(parsed)

    d = parsed.dwell if parsed.dwell is not None else dwell
    c = parsed.central if parsed.central is not None else central_freq
    e = parsed.echot if parsed.echot is not None else echo_time
    if d is None or c is None:
        raise ValueError(
            "Could not determine dwell time / central frequency from the input. "
            "Pass dwell= and central_freq= explicitly."
        )

    if out_path is None:
        base = path.rstrip("/\\")
        out_path = (os.path.splitext(base)[0] if os.path.isfile(base)
                    else os.path.join(base, "converted")) + ".basis"
    return write_basis(out_path, parsed.names, parsed.fids, d, c, e if e is not None else -1.0)


def ensure_basis(path: str, out_path: Optional[str] = None, fmt: Optional[str] = None,
                 dwell: Optional[float] = None, central_freq: Optional[float] = None) -> str:
    """Return a path to an LCModel ".basis" file, converting if necessary.

    An existing ".basis" file is returned unchanged; otherwise "path" is converted via
    convert_to_basis (auto-detecting the format unless fmt is given).
    """
    if os.path.isfile(path) and path.lower().endswith(".basis"):
        return path
    return convert_to_basis(
        path, out_path=out_path, fmt=fmt, dwell=dwell, central_freq=central_freq
    )

