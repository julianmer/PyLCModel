####################################################################################################
#                                           test_io.py                                             #
####################################################################################################
#                                                                                                  #
# Purpose: Reading LCModel ".RAW" files as LCModel writes them, and loading them in the same       #
#          orientation as every other input, so that "conj" (on by default) suits them all.        #
#                                                                                                  #
####################################################################################################

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from lcmodel_wrapper import io

_CHALLENGE = Path(__file__).resolve().parent.parent / "example_data" / "2016_fitting_challenge"


#****************#
#   .raw files   #
#****************#
def test_a_raw_header_may_close_on_a_value_line(tmp_path):
    """As the challenge writes them: '$END' after the last header value, and four
    (real, imag) pairs per line."""
    path = tmp_path / "x.RAW"
    path.write_text(" $NMID ID='x', FMTDAT='(8E13.5)'\n TRAMP=1, VOLUME=1 $END\n"
                    "  1.0E+00  2.0E+00  3.0E+00  4.0E+00  5.0E+00  6.0E+00  7.0E+00  8.0E+00\n"
                    " -1.0E+00 -2.0E+00\n")
    assert np.array_equal(io.from_raw(path), [1 + 2j, 3 + 4j, 5 + 6j, 7 + 8j, -1 - 2j])


def test_only_what_follows_the_header_is_samples(tmp_path):
    """A header line without '=' is no sample, a lone '/' ends a namelist as '$END' does,
    and Fortran's D exponents and NaN read as numbers."""
    path = tmp_path / "x.RAW"
    path.write_text(" $SEQPAR\n Echo 30 ms\n ECHOT = 30.\n $END\n &NMID ID='x'\n /\n"
                    "  1.0D+00 2.0D+00 NAN 4.0E+00\n")
    fid, keys = io.read_raw(path)
    assert fid[0] == 1 + 2j and np.isnan(fid[1].real) and fid[1].imag == 4
    assert keys["ECHOT"] == "30."


def test_an_odd_count_of_values_is_refused(tmp_path):
    path = tmp_path / "x.RAW"
    path.write_text(" $NMID\n $END\n 1.0 2.0 3.0\n")
    with pytest.raises(ValueError, match="pairs"):
        io.read_raw(path)


def test_a_raw_written_here_reads_back(tmp_path):
    fid = np.exp(-np.arange(16) / 4.0) * np.exp(1j * np.arange(16))
    io.to_raw(fid, tmp_path / "x.raw")
    assert np.allclose(io.from_raw(tmp_path / "x.raw"), fid, rtol=1e-5)


#*****************#
#   orientation   #
#*****************#
@pytest.mark.skipif(not (_CHALLENGE / "datasets_LCModel" / "dataset1.RAW").is_file(),
                    reason="challenge data not checked out (git submodule update --init)")
def test_a_raw_loads_like_the_same_spectrum_in_jmrui_text():
    """jMRUI text stores the conjugate of LCModel's .RAW; loading turns the .RAW round, so
    the default conj hands LCModel each file as it would read the .RAW itself."""
    jmrui = io.load_signals(str(_CHALLENGE / "datasets_JMRUI" / "dataset1_WS.txt"))
    raw = io.load_signals(str(_CHALLENGE / "datasets_LCModel" / "dataset1.RAW"))
    assert raw.fids.shape == (1, 2048)
    assert np.allclose(jmrui.fids, raw.fids, rtol=1e-4, atol=1e-3)
