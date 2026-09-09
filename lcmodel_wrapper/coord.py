####################################################################################################
#                                            coord.py                                              #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 26/06/26                                                                                #
#                                                                                                  #
# Purpose: Parsers for LCModel ".coord" output files: the concentration / CRLB table, basic QC     #
#          metrics, and the fitted spectral series (data, fit, baseline, ppm axis).                #
#                                                                                                  #
####################################################################################################

import re

import numpy as np

_NUMBER = r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?"

# One row of the concentration table: "  7.20E-08 166% 3.9E-02 Ala". The columns are
# fixed-width, so a wide ratio runs straight into the metabolite name ("0.659Cr+PCr");
# the regex therefore allows no whitespace between the two.
_CONC_ROW = re.compile(rf"^\s*({_NUMBER})\s+(\d+)%\s+({_NUMBER})\s*(\S+)\s*$")

# Markers that open a block of numbers in the fitted-series part of the file.
_SERIES = (
    ("ppm",         re.compile(r"points on ppm-axis = NY")),
    ("data",        re.compile(r"NY phased data points follow")),
    ("completeFit", re.compile(r"NY points of the fit to the data follow")),
    ("baseline",    re.compile(r"NY background values follow")),
)
_SERIES_END = re.compile(r"lines in following|^[ ]+[a-zA-Z0-9]+[ ]+Conc\. = [-+.E0-9]+$")


#*****************************#
#   load LCModel coord data   #
#*****************************#
def read_coord(path, coord=True, meta=True):
    """Read an LCModel ".coord" file.

    Returns the concentration table (metabolites, concentrations, %SD/CRLBs, /ref ratios)
    and/or the misc. QC metrics (FWHM, S/N, shift, phase) depending on "coord"/"meta".
    """
    metabs, concs, crlbs, tcr = [], [], [], []
    fwhm = snr = shift = phase = None

    with open(path, "r") as fh:
        conc_rows = misc_rows = 0
        for line in fh:
            if "lines in following concentration table" in line:
                conc_rows = int(line.split(" lines")[0])
            elif conc_rows > 0:
                conc_rows -= 1
                if line.split()[:1] == ["Conc."]:   # header row
                    continue
                m = _CONC_ROW.match(line)
                if m is None:
                    raise ValueError(f"Could not parse concentration row: {line.strip()!r}")
                concs.append(float(m.group(1)))
                crlbs.append(int(m.group(2)))
                tcr.append(float(m.group(3)))
                metabs.append(m.group(4))
            elif "lines in following misc. output table" in line:
                misc_rows = int(line.split(" lines")[0])
            elif misc_rows > 0:
                misc_rows -= 1
                values = line.split()
                if "FWHM" in values:
                    fwhm = float(values[2])
                    snr = float(values[-1].split("=")[-1])
                elif "shift" in values:
                    # a negative shift fuses with the "=": "shift =-0.012 ppm"
                    shift = float(values[2][1:]) if values[3] == "ppm" else float(values[3])
                elif "Ph" in values:
                    phase = float(values[1])

    if coord and meta:
        return metabs, concs, crlbs, tcr, fwhm, snr, shift, phase
    if coord:
        return metabs, concs, crlbs, tcr
    return fwhm, snr, shift, phase


#**************************************#
#   load LCModel fit from coord data   #
#**************************************#
def read_fit(path):
    """Read the fitted spectral series from an LCModel ".coord" file.

    Returns a dict with keys "ppm", "data", "completeFit" and "baseline".
    Source: https://gist.github.com/alexcraven/3db2c09f14ec489a31df81dc7b5a0f9c
    """
    series = {}
    current, values = None, []

    def flush():
        if current and values:
            series[current] = np.array(values)

    with open(path) as fh:
        for line in fh:
            new = next((key for key, pat in _SERIES if pat.search(line)), current)
            if _SERIES_END.search(line):
                new = None
            if new != current:
                flush()
                current, values = new, []
            elif current:
                for token in re.findall(r"[-+.E0-9]+", line):
                    try:
                        values.append(float(token))
                    except ValueError:
                        pass
    flush()
    return series
