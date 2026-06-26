####################################################################################################
#                                            control.py                                            #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 26/06/26                                                                                #
#                                                                                                  #
# Purpose: Generation and adjustment of LCModel control files. A control file is a list of           #
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


#*************************#
#   build a control set   #
#*************************#
def build_control(path2basis: str, n_points: int, bandwidth: float, central_freq: float,
                  ppmlim: Tuple[float, float] = (0.5, 4.2), ignore=DEFAULT_IGNORE,
                  dows: bool = False) -> List[str]:
    """Create a default LCModel control file as a list of lines."""
    ignore = resolve_ignore(ignore)
    lines = []
    lines.append("$LCMODL")
    lines.append(f"nunfil={n_points}")               # data points
    lines.append(f"deltat={1.0 / bandwidth}")        # dwell time
    lines.append(f"hzpppm={central_freq}")           # field strength in MHz
    lines.append(f"ppmst={ppmlim[1]}")
    lines.append(f"ppmend={ppmlim[0]}")

    lines.append(f"dows={'T' if dows else 'F'}")     # water scaling
    lines.append("neach=99")                          # plot each metabolite fit

    lines.append(f"filbas='{os.path.abspath(path2basis)}'")
    lines.append("filraw='example.raw'")
    lines.append("filps='example.ps'")
    lines.append("filcoo='example.coord'")
    lines.append("filh2o='example.h2o'")

    lines.append("lcoord=9")                           # 9 -> write coord file
    lines.append(f"nomit={len(ignore)}")
    for i, met in enumerate(ignore):
        lines.append(f"chomit({i + 1})='{met}'")
    lines.append("namrel='Cr+PCr'")
    lines.append("$END")
    return lines


#*************************#
#   load + override file   #
#*************************#
def load_control(control_path: str, path2basis: str, ppmlim: Tuple[float, float],
                 ignore=DEFAULT_IGNORE) -> List[str]:
    """Read an existing control file and override basis, ppm limits and ignored metabolites."""
    ignore = resolve_ignore(ignore)
    with open(control_path, "r") as fh:
        control = fh.read().split("\n")

    for i, line in enumerate(control):
        if line.startswith("filbas="):
            control[i] = f"filbas='{os.path.abspath(path2basis)}'"

    for i, line in enumerate(control):
        if line.startswith("ppmst="):
            control[i] = f"ppmst={ppmlim[1]}"
        if line.startswith("ppmend="):
            control[i] = f"ppmend={ppmlim[0]}"

    for i, line in enumerate(control):
        if line.startswith("nomit="):
            control[i] = f"nomit={len(ignore)}"
            for j, met in enumerate(ignore):
                control.insert(i + j + 1, f"chomit({j + 1})='{met}'")
            break
    return control


#********************************#
#   set a key in a control set   #
#********************************#
def set_key(control: List[str], key: str, value) -> List[str]:
    """Set "key=value" in place; append if the key is absent (before "$END")."""
    prefix = f"{key}="
    for i, line in enumerate(control):
        if line.startswith(prefix):
            control[i] = f"{key}={value}"
            return control
    # insert before $END if present, else append
    for i, line in enumerate(control):
        if line.strip() == "$END":
            control.insert(i, f"{key}={value}")
            return control
    control.append(f"{key}={value}")
    return control
