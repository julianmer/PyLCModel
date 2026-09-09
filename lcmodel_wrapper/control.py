####################################################################################################
#                                            control.py                                            #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 26/06/26                                                                                #
#                                                                                                  #
# Purpose: Generation and adjustment of LCModel control files. A control file is a list of         #
#          "key=value" lines wrapped in "$LCMODL ... $END". See the LCModel manual for the full    #
#          parameter reference: http://s-provencher.com/pub/LCModel/manual/manual.pdf              #
#                                                                                                  #
####################################################################################################

import os
from typing import List, Tuple


DEFAULT_IGNORE = ["Lip13a", "Lip13b", "Lip09", "Lip20",
                  "MM09", "MM12", "MM14", "MM17", "MM20",
                  "-CrCH2", "CrCH2"]


#********************#
#   ignore presets   #
#********************#
def resolve_ignore(ignore) -> List[str]:
    if isinstance(ignore, str):
        if ignore.lower() == "default":
            return list(DEFAULT_IGNORE)
        if ignore.lower() == "none":
            return []
        raise ValueError(
            "Unknown ignore preset. Use 'default', 'none', or a list of metabolite names."
        )
    if isinstance(ignore, (list, tuple)):
        return list(ignore)
    raise ValueError("ignore must be a list of metabolite names or a preset string.")


def _omit_lines(ignore: List[str]) -> List[str]:
    return [f"nomit={len(ignore)}"] + [f"chomit({i + 1})='{m}'" for i, m in enumerate(ignore)]


#*************************#
#   build a control set   #
#*************************#
def build_control(path2basis: str, n_points: int, bandwidth: float, central_freq: float,
                  ppmlim: Tuple[float, float] = (0.5, 4.2), ignore=DEFAULT_IGNORE,
                  dows: bool = False) -> List[str]:
    """Create a default LCModel control file as a list of lines."""
    return [
        "$LCMODL",
        f"nunfil={n_points}",                 # data points
        f"deltat={1.0 / bandwidth}",          # dwell time
        f"hzpppm={central_freq}",             # field strength in MHz
        f"ppmst={ppmlim[1]}",
        f"ppmend={ppmlim[0]}",
        f"dows={'T' if dows else 'F'}",       # water scaling
        "neach=99",                           # plot each metabolite fit
        f"filbas='{os.path.abspath(path2basis)}'",
        "filraw='example.raw'",
        "filps='example.ps'",
        "filcoo='example.coord'",
        "filh2o='example.h2o'",
        "lcoord=9",                           # 9 -> write coord file
        *_omit_lines(resolve_ignore(ignore)),
        "namrel='Cr+PCr'",
        "$END",
    ]


#**************************#
#   load + override file   #
#**************************#
def load_control(control_path: str, path2basis: str, ppmlim: Tuple[float, float],
                 ignore=DEFAULT_IGNORE) -> List[str]:
    """Read an existing control file and override basis, ppm limits and ignored metabolites."""
    ignore = resolve_ignore(ignore)
    with open(control_path, "r") as fh:
        control = [line for line in fh.read().splitlines()
                   if not line.startswith(("chomit(", "nomit="))]

    set_key(control, "filbas", f"'{os.path.abspath(path2basis)}'")
    set_key(control, "ppmst", ppmlim[1])
    set_key(control, "ppmend", ppmlim[0])
    for line in _omit_lines(ignore):
        key, _, value = line.partition("=")
        set_key(control, key, value)
    return control


#********************************#
#   set a key in a control set   #
#********************************#
def set_key(control: List[str], key: str, value) -> List[str]:
    """Set "key=value" in place; insert before "$END" (or append) if the key is absent."""
    prefix = f"{key}="
    for i, line in enumerate(control):
        if line.startswith(prefix):
            control[i] = f"{key}={value}"
            return control
    end = next((i for i, line in enumerate(control) if line.strip() == "$END"), len(control))
    control.insert(end, f"{key}={value}")
    return control
