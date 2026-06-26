####################################################################################################
#                                            coord.py                                              #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 26/06/26                                                                                #
#                                                                                                  #
# Purpose: Parsers for LCModel ".coord" output files: the concentration / CRLB table, basic QC      #
#          metrics, and the fitted spectral series (data, fit, baseline, ppm axis).                  #
#                                                                                                  #
####################################################################################################

import re

import numpy as np


#*****************************#
#   load LCModel coord data   #
#*****************************#
def read_coord(path, coord=True, meta=True):
    """Read an LCModel ".coord" file.

    Returns the concentration table (metabolites, concentrations, %SD/CRLBs, /ref ratios)
    and/or the misc. QC metrics (FWHM, S/N, shift, phase) depending on "coord"/"meta".
    """
    metabs, concs, crlbs, tcr = [], [], [], []
    fwhm, snr, shift, phase = None, None, None, None

    with open(path, "r") as file:
        concReader = 0
        miscReader = 0

        for line in file:
            if "lines in following concentration table" in line:
                concReader = int(line.split(" lines")[0])
            elif concReader > 0:  # read concentration table
                concReader -= 1
                values = line.split()

                if values[0] == "Conc.":   # header row
                    continue
                else:
                    try:  # sometimes the fields are fused together with '+'
                        m = values[3]
                        c = float(values[2])
                    except (IndexError, ValueError):
                        if "E+" in values[2]:  # catch scientific notation
                            c = values[2].split("E+")
                            m = str(c[1].split("+")[1:])
                            c = float(c[0] + "e+" + c[1].split("+")[0])
                        else:
                            if len(values[2].split("+")) > 1:
                                m = str(values[2].split("+")[1:])
                                c = float(values[2].split("+")[0])
                            elif len(values[2].split("-")) > 1:
                                m = str(values[2].split("-")[1:])
                                c = float(values[2].split("-")[0])
                            else:
                                raise ValueError(f"Could not parse {values}")

                    metabs.append(m)
                    concs.append(float(values[0]))
                    crlbs.append(int(values[1][:-1]))
                    tcr.append(c)
                    continue

            if "lines in following misc. output table" in line:
                miscReader = int(line.split(" lines")[0])
            elif miscReader > 0:  # read misc. output table
                miscReader -= 1
                values = line.split()

                if "FWHM" in values:
                    fwhm = float(values[2])
                    snr = float(values[-1].split("=")[-1])
                elif "shift" in values:
                    if values[3] == "ppm":
                        shift = float(values[2][1:])  # negative fuses with '='
                    else:
                        shift = float(values[3])
                elif "Ph" in values:
                    phase = float(values[1])

    if coord and meta:
        return metabs, concs, crlbs, tcr, fwhm, snr, shift, phase
    elif coord:
        return metabs, concs, crlbs, tcr
    elif meta:
        return fwhm, snr, shift, phase


#**************************************#
#   load LCModel fit from coord data   #
#**************************************#
def read_fit(path):
    """Read the fitted spectral series from an LCModel ".coord" file.

    Returns a dict with keys "ppm", "data", "completeFit" and "baseline".
    Source: https://gist.github.com/alexcraven/3db2c09f14ec489a31df81dc7b5a0f9c
    """
    series_type = None
    series_data = {}

    with open(path) as f:
        vals = []

        for line in f:
            prev_series_type = series_type
            if re.match(".*[0-9]+ points on ppm-axis = NY.*", line):
                series_type = "ppm"
            elif re.match(".*NY phased data points follow.*", line):
                series_type = "data"
            elif re.match(".*NY points of the fit to the data follow.*", line):
                series_type = "completeFit"
            elif re.match(".*NY background values follow.*", line):
                series_type = "baseline"
            elif re.match(".*lines in following.*", line):
                series_type = None
            elif re.match("[ ]+[a-zA-Z0-9]+[ ]+Conc. = [-+.E0-9]+$", line):
                series_type = None

            if prev_series_type != series_type:  # start/end of chunk
                if len(vals) > 0:
                    series_data[prev_series_type] = np.array(vals)
                    vals = []
            else:
                if series_type:
                    for x in re.finditer(r"([-+.E0-9]+)[ \t]*", line):
                        v = x.group(1)
                        try:
                            vals.append(float(v))
                        except ValueError:
                            pass
    return series_data
