import math
import json
import time as tm
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from constants import Constants

constant = Constants()

def c_cross(a, b):
    return np.array([
        a[1]*b[2] - a[2]*b[1],
        a[2]*b[0] - a[0]*b[2],
        a[0]*b[1] - a[1]*b[0]
    ], dtype=float)

def c_dot(a, b):
    return a[0]*b[0] + a[1]*b[1] + a[2]*b[2]
class Layer:
    def __init__(self, id_, start_mag, start_anisotropy, K, Ms, thickness):
        self.id = id_
        # magnetisation options
        if start_mag is not np.array:
            start_mag = np.array(start_mag, dtype=float)
        self.m = start_mag
        self.m = self.m / np.linalg.norm(self.m)
        self.m_history = []
        if start_anisotropy is not np.array:
            start_anisotropy = np.array(start_anisotropy, dtype=float)

        # other parameters section
        self.anisotropy = start_anisotropy
        self.K = K
        self.Ms = Ms
        self.thickness = thickness
        self.coupling = 1e-5
        self.demagnetisation_tensor = np.array(
            [[0.000, 0, 0], [0, 0.00, 0], [0, 0, 0.93]], dtype=float)

        self.dipole_tensor = np.array(
            [[0.0173, 0.0, 0.0], [0.0, 0.0173, 0.0], [0.0, 0.0, -0.0345]],
            dtype=float)

        # utility function
        self.params = ['m', 'anisotropy', 'Hext', 'coupling']
        self.Hext = np.array([0.0, 0.0, 0.0])
        self.Hext_const = np.array([0.0, 0.0, 0.0])
        self.update_external_field = None
        self.update_anisotropy = None
        self.update_coupling = None
        self.dmdt = np.array([0., 0., 0.])
        self.log = []
        self.junction_result = None 

    def to_dict(self):
        d = {}
        for param in [
                'id', 'anisotropy', 'K', 'm', 'coupling', 'thickness', 'Hext',
                'Hext_const', 'demagnetisation_tensor', 'dipole_tensor', 'Ms'
        ]:
            param_val = getattr(self, param)
            if type(param_val) is np.ndarray:
                param_val = param_val.tolist()
            d[param] = param_val
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(d['id'], d['m'], d['anisotropy'], d['K'], d['Ms'],
                   d['thickness'])

    def Heff(self, time, coupled_layers):
        if self.update_anisotropy:
            self.anisotropy = self.update_anisotropy(time)
        if self.update_external_field:
            self.Hext = self.update_external_field(self.Hext, time)
        if self.update_coupling:
            self.coupling = self.update_coupling(time)

        heff = \
            self.Hext_const + self.Hext + self.calculate_anisotropy() +\
            self.calculate_demagnetisation_field() +\
            self.calculate_interlayer_exchange_coupling_field(coupled_layers) +\
            self.calculate_dipole_interaction()
        return heff

    def set_global_external_field_value(self, hval):
        self.Hext_const = hval

    def calculate_dipole_interaction(self):
        return -1. * self.dipole_tensor @ np.array([0, 0, 1.])

    def calculate_tensor_interaction(self, tensor):
        return -1. * tensor @ self.m * self.Ms

    def calculate_anisotropy(self):
        nom = 2 * self.K * c_dot(self.m, self.anisotropy) * self.anisotropy
        return nom / (constant.MAGNETIC_PERMEABILITY * self.Ms)

    """
    # this is the old formula that yields non-zero field 
    # for parallel spins
    def calculate_interlayer_exchange_coupling(self, time, coupled_layers):
        heff_iec = np.array([0, 0., 0], dtype=float)
        if not coupled_layers:
            return heff_iec
        for layer in coupled_layers:
            heff_iec += self.coupling*layer.m / \
                (constant.MAGNETIC_PERMEABILITY*self.Ms*self.thickness)
        return heff_iec
    """

    def calculate_interlayer_exchange_coupling_field(self, coupled_layers):
        heff_iec = np.array([0, 0., 0], dtype=float)
        if not coupled_layers:
            return heff_iec
        for layer in coupled_layers:
            heff_iec += self.coupling*(layer.m - self.m) / \
                (constant.MAGNETIC_PERMEABILITY*self.Ms*self.thickness)
        return heff_iec

    def calculate_demagnetisation_field(self):
        return -1. * self.demagnetisation_tensor @ self.m * self.Ms

    def llg(self, time, m, coupled_layers):
        heff = self.Heff(time, coupled_layers)
        dmdt = -1.0*constant.GYRO * \
            c_cross(m, heff) - 1.0*constant.GYRO * \
            constant.DAMPING*c_cross(m, c_cross(m, heff))
        return dmdt

    def llg_STT(self, time, m, coupled_layers, spin_polarized_layer):
        """
        :param spin_polarized_layer is a vector of a layer in which 
            the current is polarised
        """
        heff = self.Heff(time, coupled_layers)
        aj = (constant.HBAR * self.current_density *
              self.spin_polarisation_efficiency) / (
                  2 * constant.ELECTRON_CHARGE *
                  constant.MAGNETIC_PERMEABILITY * self.Ms * self.thickness)
        bj = np.array([0., 0., 0.])
        p_cross = c_cross(m, spin_polarized_layer)
        stt_term = constant.GYRO * (aj * c_cross(m, p_cross) + bj * p_cross)

        dmdt = -1.0*constant.GYRO * \
            c_cross(m, heff) - 1.0*constant.GYRO * \
            constant.DAMPING*c_cross(m, c_cross(m, heff)) +\
            stt_term
        return dmdt

    def rk4_step(self, time, time_step, coupled_layers=None):
        m = self.m
        k1 = time_step * self.llg(time, m, coupled_layers)
        k2 = time_step * self.llg(time + 0.5 * time_step, m + 0.5 * k1,
                                  coupled_layers)
        k3 = time_step * self.llg(time + 0.5 * time_step, m + 0.5 * k2,
                                  coupled_layers)
        k4 = time_step * self.llg(time + time_step, m + k3, coupled_layers)
        m = m + (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
        m = m / np.linalg.norm(m)
        # update m
        self.m = m


class Junction():
    def __init__(self, _id, layers, couplings, persist=False):
        self.id = _id
        self.layers = layers
        self.layers_id = {layer.id: layer for layer in layers}
        self.couplings = couplings
        self.log = []
        self.params = ['m', 'anisotropy', 'Hext']
        self.persist = persist  # read file only or memory leftover?

        self.R_labs = []
        self.Rp = 100
        self.Rap = 200
        self.avg_RpRap = 0.5 * (self.Rap - self.Rp)
        self.snapshot = self.to_dict()

    def overwrite_snapshot(self):
        self.snapshot = self.to_dict()

    def to_json(self, filename):
        json.dump(self.to_dict(), open(filename, 'w'), indent=4)

    @classmethod
    def from_json(cls, filename, persist=False):
        d = json.load(open(filename, 'r'))
        return Junction.from_dict(d, persist)

    @classmethod
    def from_dict(cls, d, persist=False):
        layers = [Layer.from_dict(layer) for layer in d['layers']]
        return cls(d['id'], layers, d['couplings'], persist=persist)

    def to_dict(self):
        d = {}
        layers_dict = [layer.to_dict() for layer in self.layers]
        d['layers'] = layers_dict
        for param in ['id', 'couplings', 'Rap', 'Rp']:
            d[param] = getattr(self, param)
        return d

    def magnetoresistance(self, costheta):
        """
        :param theta
            the angle between two magnetisation layers
        """
        return self.Rp + self.avg_RpRap * (1. - costheta)

    def log_layer_parameters(self, time, R):
        value_list = [(time / 1e-9), *R]  # nanoseconds
        for param in self.params:
            for layer in self.layers:
                pval = getattr(layer, param)
                value_list.extend(pval)
        self.log.append(value_list)

    def set_junction_global_external_field(self, constant_field_value):
        for layer in self.layers:
            layer.Hext_const = constant_field_value

    def restart(self):
        # just revert to initial parameters
        # that will involve reading the saved file and overrite the state
        # restore the snapshot
        self.R_labs = []
        self.log = []
        for key in self.snapshot:
            if key != 'layers':
                setattr(self, key, self.snapshot[key])
        self.layers = [
            Layer.from_dict(layer_dict)
            for layer_dict in self.snapshot['layers']
        ]

    def run_simulation(self, stop_time, dt=1e-12, time_step=1e-13):
        # LLG#
        if not self.layers:
            raise ValueError("No layers in junction!")
        print("Kicking off the simulation!")
        tstart = tm.time()
        if self.couplings:
            for t in np.arange(0, stop_time, dt):
                time = t
                stopping_cond = (t + dt - time_step)
                while time < stopping_cond:
                    for i, layer in enumerate(self.layers):
                        coupled_layers = [
                            self.layers[j] for j in self.couplings[i]
                        ]
                        layer.rk4_step(time, time_step, coupled_layers)
                    time += time_step
                self.log_layer_parameters(t)
        else:
            iterations = int(stop_time / time_step)

            # just for labels
            for layer1 in self.layers:
                for layer2 in self.layers[1:]:
                    if layer1.id != layer2.id:
                        self.R_labs.append(f'R_{layer1.id}_{layer2.id}')
            # start the simulation
            for i in range(0, iterations):
                t = i * time_step
                for i, layer in enumerate(self.layers):
                    layer.rk4_step(t, time_step)
                # calculate magnetoresistance
                R = []
                for layer1 in self.layers:
                    for layer2 in self.layers[1:]:
                        if layer1.id != layer2.id:
                            cosTheta = c_dot(layer1.m, layer2.m)
                            R.append(self.magnetoresistance(cosTheta))
                self.log_layer_parameters(t, R)

        tend = tm.time()
        print(
            f"Simulation has finished in {tend-tstart}s. Writting the results..."
        )
        cols = []
        for param in self.params:
            for layer in self.layers:
                for suffix in ['x', 'y', 'z']:
                    cols.append(param + '_' + suffix + '_' + layer.id)
        if self.persist:
            print("Result available under junction_result variable")
            self.junction_result = pd.DataFrame(
                data=self.log, columns=['time', *self.R_labs,
                                        *cols])
            # self.junction_result.to_csv('results2.csv')
        else:
            pd.DataFrame(data=self.log, columns=['time', *self.R_labs,
                                                 *cols]).to_csv('results.csv')


def plot_results():
    df = pd.read_csv('results.csv')
    # df[['m_x_free', 'm_y_free', 'm_z_free']].plot()
    df['R_free_bottom'].plot()
    plt.show()

