####################################################################################################
#                                           pylcmodel.py                                           #
####################################################################################################
#                                                                                                  #
# Authors: J. P. Merkofer (j.p.merkofer@tue.nl)                                                    #
#                                                                                                  #
# Created: 20/06/24                                                                                #
#                                                                                                  #
# Purpose: Python wrapper for the LCModel optimization framework for least-squares fitting         #
#          of spectra.                                                                             #
#                                                                                                  #
####################################################################################################


#*************#
#   imports   #
#*************#
import multiprocessing
import numpy as np
import os
import platform
import re
import shutil
import subprocess
import time

from fsl_mrs.utils import mrs_io

from scipy.optimize import minimize



#**************************************************************************************************#
#                                          Class PyLCModel                                         #
#**************************************************************************************************#
#                                                                                                  #
# The framework wrapper for the LCModel fitting tool.                                              #
#                                                                                                  #
#**************************************************************************************************#
class PyLCModel():
    def __init__(self, path2basis, control=None, multiprocessing=False, ppmlim=(0.5, 4.2),
                 conj=True, ignore='default', save_path='', path2exec=None, sample_points=None,
                 bandwidth=None, **kwargs):

        self.basisFSL = mrs_io.read_basis(path2basis)
        self.control = control
        self.multiprocessing = multiprocessing
        self.save_path = save_path
        self.conj = conj
        if sample_points is None: self.sample_points = self.basisFSL.original_points
        else: self.sample_points = sample_points
        if bandwidth is None: self.bandwidth = self.basisFSL.original_bw
        else: self.bandwidth = bandwidth

        if path2exec is None:   # infer path to LCModel executable (and os system)
            print(f'Warning -- path2exec not specified, trying to use internal binaries...')
            print(f'           It is recommended to provide an own executable for LCModel!')
            if os.name == 'nt':
                self.path2exec = (os.path.dirname(os.path.realpath(__file__)) + os.sep + 'lcmodel'
                                  + os.sep + 'executables' + os.sep + 'win' + os.sep + 'win10'
                                  + os.sep + 'LCModel.exe')
                if platform.release() != '10':   # warning if not win10
                    print(f'Warning -- LCModel binaries are for Windows 10, but you are using '
                          f'{platform.release()}!')
            elif os.name == 'posix':
                if platform.system() == 'Darwin':
                    self.path2exec = (f'{os.path.dirname(os.path.realpath(__file__))}'
                                      f'/lcmodel/executables/mac/lcmodel')
                    os.chmod(self.path2exec, 0o755)  # make executable
                elif platform.system() == 'Linux':
                    self.path2exec = (f'{os.path.dirname(os.path.realpath(__file__))}'
                                      f'/lcmodel/executables/linux/lcmodel')
                    os.chmod(self.path2exec, 0o755)  # make executable
        else: self.path2exec = path2exec

        # ignore metabolites
        if isinstance(ignore, str):
            if ignore.lower() == 'default': ignore = ['Lip13a', 'Lip13b', 'Lip09', 'Lip20',
                                                      'MM09', 'MM12', 'MM14', 'MM17', 'MM20',
                                                      '-CrCH2', 'CrCH2']
            elif ignore.lower() == 'none': ignore = []
            else: raise ValueError('Unknown preset string... Please use one of the predefined '
                                   'or provide a list of metabolite names!')
        elif not isinstance(ignore, list):
            raise ValueError('Ignore must be a list of metabolite names or a string!')

        # TODO: convert basis to LCModel format (if necessary)
        if not path2basis.lower().endswith('.basis'):
            raise ValueError('Basis file must be in .basis format! (For now... Sorry!)')

        # parse control file
        if control is not None:
            control = open(control, 'r').read()
            self.control = control.split('\n')

            # overwrite basis
            for i, line in enumerate(self.control):
                if line.startswith('filbas='):
                    if not line[1:-1].split(os.sep)[-1] == path2basis.split(os.sep)[-1]:
                        print(f'Warning -- overwriting filbas in control file with'
                              f' {os.path.abspath(path2basis)}')
                    self.control[i] = f"filbas='{os.path.abspath(path2basis)}'"

            # adjust ppm limits
            for i, line in enumerate(self.control):
                if line.startswith('ppmst='):
                    if not line.split('=')[1] == str(ppmlim[1]):   # warning if limits aren't equal
                        print(f'Warning -- overwriting ppmst in control file with {ppmlim[1]}')
                    self.control[i] = f'ppmst={ppmlim[1]}'
                if line.startswith('ppmend='):
                    if not line.split('=')[1] == str(ppmlim[0]):   # warning if limits aren't equal
                        print(f'Warning -- overwriting ppmend in control file with {ppmlim[0]}')
                    self.control[i] = f'ppmend={ppmlim[0]}'

            # overwrite ignore metabolites
            for i, line in enumerate(self.control):
                if line.startswith('nomit='):
                    if not line.split('=')[1] == str(len(ignore)):   # warning if ignore isn't equal
                        print(f'Warning -- overwriting nomit in control file with '
                              f'{len(ignore)} for {ignore}...')
                    self.control[i] = f'nomit={len(ignore)}'
                    for j, met in enumerate(ignore):
                        self.control.insert(i+j+1, f'chomit({j+1})=\'{met}\'')
                    break

        else:
            lines = []
            lines.append(f"$LCMODL")
            lines.append(f"nunfil={self.sample_points}")  # data points
            lines.append(f"deltat={1. / self.bandwidth}")  # dwell time
            lines.append(f"hzpppm={self.basisFSL.cf}")  # field strength in MHz
            lines.append(f"ppmst={ppmlim[1]}")
            lines.append(f"ppmend={ppmlim[0]}")

            lines.append(f"dows=F")   # 'T' <-> do water scaling
            lines.append(f"neach=99")   # number of metabolites to plot fit individually

            lines.append(f"filbas='{os.path.abspath(path2basis)}'")

            lines.append(f"filraw='example.raw'")
            lines.append(f"filps='example.ps'")
            lines.append(f"filcoo='example.coord'")
            lines.append(f"filh2o='example.h2o'")

            lines.append(f"lcoord=9")  # 0 <-> surpress creation of coord file, 9 <-> don't surpress
            lines.append(f"nomit={len(ignore)}")
            for i, met in enumerate(ignore):
                lines.append(f"chomit({i+1})=\'{met}\'")
            lines.append(f"namrel='Cr+PCr'")

            # lines.append(f"nratio=0")   # number of soft constraints (default 12, see manual)
            # lines.append(f"sddegz=6")   # 6 <-> eddy current correction
            # lines.append(f"dkntmn=0.5")   # limit knot spacing of baseline (max 1/3 of ppm range)
            lines.append(f"$END")

            self.control = lines


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
            def err(w):
                w = np.clip(w, 0, None)
                return np.abs(t[i] - w * t_hat[i]).mean()

            w[i] = minimize(err, w[i], bounds=[(0, None)]).x
        return w[..., np.newaxis]


    #****************************#
    #   loss on concentrations   #
    #****************************#
    def concsLoss(self, t, t_hat, type='ae'):
        t = t[:, :self.basisFSL.n_metabs]
        t_hat = t_hat[:, :self.basisFSL.n_metabs]

        if type == 'ae':  # absolute error
            return np.abs(t - t_hat)
        else:
            raise ValueError('Unknown loss type... Please use one of the predefined!')


    #*********************#
    #   input to output   #
    #*********************#
    def forward(self, x, x_ref=None, frac=None, x0=None):
        assert x0 is None, 'Initial values not supported... (please set x0=None)'

        theta = self.lcmodel_minimize(x, x_ref, frac)
        return theta


    #*********************#
    #   LCModel fitting   #
    #*********************#
    def lcmodel_minimize(self, x, x_ref=None, frac=None):
        thetas, crlbs = [], []
        x = x[:, 0] + 1j * x[:, 1]
        fids = np.fft.ifft(x, axis=-1)   # to time domain
        if self.conj:
            fids = np.conjugate(fids)   # conjugate if necessary
            if x_ref is not None: x_ref = np.conjugate(x_ref)

        # create temporary directory
        if self.save_path == '' or self.save_path is None:
            path = os.getcwd() + os.sep + 'tmp' + os.sep
        else:
            path = os.getcwd() + os.sep + self.save_path + os.sep
        if not os.path.exists(path): os.makedirs(path)

        # run
        if self.multiprocessing:   # multi threading
            tasks = [(fids[i], x_ref, frac, i, path) for i in range(fids.shape[0])]
            with multiprocessing.Pool(None) as pool:
                thetas, crlbs = zip(*pool.starmap(self.lcm_forward, tasks))

        else:  # loop
            for i, fid in enumerate(fids):
                theta, crlb = self.lcm_forward(fid, x_ref, frac, i, path)
                thetas.append(theta)
                crlbs.append(crlb)

        # remove temporary folder
        if self.save_path == '' or self.save_path is None:
            shutil.rmtree(path, ignore_errors=True)
        else:
            # ... or save control file to save path
            with open(f'{path + os.sep}control', 'w') as file:
                file.write('\n'.join(self.control))

        return np.array(thetas), np.array(crlbs)


    #************************#
    #   write to .raw file   #
    #************************#
    def to_raw(self, fid, file_path, header=" $NMID\n  id='', fmtdat='(2E15.6)'\n $END\n"):
        with open(file_path, 'w') as file:
            file.write(header)
            for num in fid:
                file.write(f"  {num.real: .6E} {num.imag: .6E}\n")


    #*************************#
    #   read from .raw file   #
    #*************************#
    def from_raw(self, path):
        with open(path, 'r') as f:
            lines = f.readlines()
            for i, line in enumerate(lines):
                if line.split()[0] == '$END': break
            fid = [complex(float(line.split()[0]),
                           float(line.split()[1])) for line in lines[i+1:]]
        return np.array(fid)


    #*************************#
    #   run LCModel wrapper   #
    #*************************#
    def lcm_forward(self, fid, h2o=None, frac=None, idx=0, path=os.getcwd() + os.sep + 'tmp' + os.sep):
        # transform to a .raw file
        assert fid.shape[0] == self.sample_points, \
            'Number of points in FID does not match sample points!'
        self.to_raw(fid, f'{path + os.sep}temp{idx}.raw')

        # transform to a .h2o file
        if h2o is not None:
            self.to_raw(h2o[idx], f'{path + os.sep}temp{idx}.h2o')

            # write control file
            for i, line in enumerate(self.control):
                if line.startswith('dows='): self.control[i] = 'dows=T'

        # tissue correction
        if frac is not None:
            wconc = (43300 * frac[idx]['GM'] + 35880 * frac[idx]['WM'] + 
                     55556 * frac[idx]['CSF']) / (1 - frac[idx]['CSF'])

            # write control file
            for i, line in enumerate(self.control):
                if line.startswith('wconc='): self.control[i] = f'wconc={int(wconc)}'

        # run LCModel
        self.initiate(f'{path + os.sep}temp{idx}.raw')

        # wait for .coord file
        while not os.path.exists(f'{path + os.sep}temp{idx}.coord'): time.sleep(1e-3)   # 1ms

        # read .coord file
        metabs, concs, crlbs, tcr = self.read_LCModel_coord(f'{path}temp{idx}.coord',
                                                            meta=False)
        # sort concentrations by basis names
        concs = [concs[metabs.index(met)] if met in metabs else 0.0
                 for met in self.basisFSL._names]
        crlbs = [crlbs[metabs.index(met)] if met in metabs else 999.0
                 for met in self.basisFSL._names]
        return concs, crlbs


    #******************************#
    #   initiate routine on .raw   #
    #******************************#
    def initiate(self, file_path):
        # write control file
        for i, line in enumerate(self.control):
            if line.startswith('filraw='): self.control[i] = f'filraw=\'{file_path}\''
            if line.startswith('filps='): self.control[i] = f'filps=\'{file_path[:-4]}.ps\''
            if line.startswith('filcoo='): self.control[i] = f'filcoo=\'{file_path[:-4]}.coord\''
            if line.startswith('filh2o='): self.control[i] = f'filh2o=\'{file_path[:-4]}.h2o\''

        msg = '\n'.join(self.control)
        msg = msg.encode('utf-8')

        # run LCModel
        proc = subprocess.Popen(
            [self.path2exec, ],
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        stdout_value, stderr_value = proc.communicate(msg)

        # error handling
        if not (stdout_value == b'' or stdout_value is None): print(stdout_value)
        if not (stderr_value == b'' or stderr_value is None): print(stderr_value)


    #**************************#
    #   setter for save path   #
    #**************************#
    def set_save_path(self, path):
        self.save_path = path

    #*****************************#
    #   load LCModel coord data   #
    #*****************************#
    def read_LCModel_coord(self, path, coord=True, meta=True):
        metabs, concs, crlbs, tcr = [], [], [], []
        fwhm, snr, shift, phase = None, None, None, None

        # go through file and extract all info
        with open(path, 'r') as file:
            concReader = 0
            miscReader = 0

            for line in file:
                if 'lines in following concentration table' in line:
                    concReader = int(line.split(' lines')[0])
                elif concReader > 0:  # read concentration table
                    concReader -= 1
                    values = line.split()

                    # check if in header of table
                    if values[0] == 'Conc.':
                        continue
                    else:
                        try:  # sometimes the fields are fused together with '+'
                            m = values[3]
                            c = float(values[2])
                        except:
                            if 'E+' in values[2]:  # catch scientific notation
                                c = values[2].split('E+')
                                m = str(c[1].split('+')[1:])
                                c = float(c[0] + 'e+' + c[1].split('+')[0])
                            else:
                                if len(values[2].split('+')) > 1:
                                    m = str(values[2].split('+')[1:])
                                    c = float(values[2].split('+')[0])
                                elif len(values[2].split('-')) > 1:
                                    m = str(values[2].split('-')[1:])
                                    c = float(values[2].split('-')[0])
                                else:
                                    raise ValueError(f'Could not parse {values}')

                        # append to data
                        metabs.append(m)
                        concs.append(float(values[0]))
                        crlbs.append(int(values[1][:-1]))
                        tcr.append(c)
                        continue

                if 'lines in following misc. output table' in line:
                    miscReader = int(line.split(' lines')[0])
                elif miscReader > 0:  # read misc. output table
                    miscReader -= 1
                    values = line.split()

                    # extract info
                    if 'FWHM' in values:
                        fwhm = float(values[2])
                        snr = float(values[-1].split('=')[-1])
                    elif 'shift' in values:
                        if values[3] == 'ppm':
                            shift = float(values[2][1:])  # negative fuses with '='
                        else:
                            shift = float(values[3])
                    elif 'Ph' in values:
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
    def read_LCModel_fit(self, path):
        # Source: https://gist.github.com/alexcraven/3db2c09f14ec489a31df81dc7b5a0f9c

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
                    # completeFit implies baseline+fit
                elif re.match(".*NY background values follow.*", line):
                    series_type = "baseline"
                elif re.match(".*lines in following.*", line):
                    series_type = None
                elif re.match("[ ]+[a-zA-Z0-9]+[ ]+Conc. = [-+.E0-9]+$", line):
                    series_type = None

                if prev_series_type != series_type:  # start/end of chunk...
                    if len(vals) > 0:
                        series_data[prev_series_type] = np.array(vals)
                        vals = []
                else:
                    if series_type:
                        for x in re.finditer(r"([-+.E0-9]+)[ \t]*", line):
                            v = x.group(1)
                            try:
                                v = float(v)
                                vals.append(v)
                            except ValueError:
                                print("Error parsing line: %s" % (line,))
                                print(v)
        return series_data