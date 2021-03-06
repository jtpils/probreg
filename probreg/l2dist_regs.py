from __future__ import print_function
from __future__ import division
from collections import namedtuple
import numpy as np
from scipy.optimize import minimize
import open3d as o3
from . import features as ft
from . import cost_functions as cf


class L2DistRegistration():
    """L2 distance registration class
    This algorithm expresses point clouds as mixture gaussian distributions and
    performs registration by minimizing the distance between two distributions.

    Args:
        source (numpy.ndarray): Source point cloud data.
        feature_gen (probreg.features.Feature): Generator of mixture gaussian distribution.
        cost_fn (probreg.cost_functions.CostFunction): Cost function to caliculate L2 distance.
        sigma (float, optional): Scaling parameter for L2 distance.
        delta (float, optional): Annealing parameter for optimization.
        use_estimated_sigma (float, optional): If this flag is True,
            sigma estimates from the source point cloud.
    """
    def __init__(self, source, feature_gen, cost_fn,
                 sigma=1.0, delta=0.9,
                 use_estimated_sigma=True):
        self._source = source
        self._feature_gen = feature_gen
        self._cost_fn = cost_fn
        self._sigma = sigma
        self._delta = delta
        self._use_estimated_sigma = use_estimated_sigma
        self._callbacks = []
        if not self._source is None and self._use_estimated_sigma:
            self._estimate_sigma(self._source)

    def set_source(self, source):
        self._source = source
        if self._use_estimated_sigma:
            self._estimate_sigma(self._source)

    def set_callbacks(self, callbacks):
        self._callbacks.extend(callbacks)

    def _estimate_sigma(self, data):
        ndata, ndim = data.shape
        data_hat = data - np.mean(data, axis=0)
        self._sigma = np.power(np.linalg.det(np.dot(data_hat.T, data_hat) / (ndata - 1)), 1.0 / (2.0 * ndim))

    def _annealing(self):
        self._sigma *= self._delta

    def optimization_cb(self, x):
        tf_result = self._cost_fn.to_transformation(x)
        for c in self._callbacks:
            c(tf_result)

    def registration(self, target, maxitr=1, tol=1.0e-3):
        f = None
        x_ini = self._cost_fn.initial()
        for _ in range(maxitr):
            self._feature_gen.init()
            mu_source, phi_source = self._feature_gen.compute(self._source)
            mu_target, phi_target = self._feature_gen.compute(target)
            args = (mu_source, phi_source,
                    mu_target, phi_target, self._sigma)
            res = minimize(self._cost_fn,
                           x_ini,
                           args=args,
                           method='BFGS', jac=True,
                           callback=self.optimization_cb)
            self._annealing()
            self._feature_gen.annealing()
            if not f is None and abs(res.fun - f) < tol:
                break
            f = res.fun
            x_ini = res.x
        return self._cost_fn.to_transformation(res.x)


class RigidGMMReg(L2DistRegistration):
    def __init__(self, source, sigma=1.0, delta=0.9,
                 n_gmm_components=800, use_estimated_sigma=True):
        n_gmm_components = min(n_gmm_components, int(source.shape[0] * 0.8))
        super(RigidGMMReg, self).__init__(source, ft.GMM(n_gmm_components),
                                          cf.RigidCostFunction(),
                                          sigma, delta,
                                          use_estimated_sigma)


class TPSGMMReg(L2DistRegistration):
    def __init__(self, source, sigma=1.0, delta=0.9,
                 n_gmm_components=800, alpha=1.0, beta=0.1,
                 use_estimated_sigma=True):
        n_gmm_components = min(n_gmm_components, int(source.shape[0] * 0.8))
        super(TPSGMMReg, self).__init__(source, ft.GMM(n_gmm_components),
                                        cf.TPSCostFunction([], alpha, beta),
                                        sigma, delta,
                                        use_estimated_sigma)
        self._feature_gen.init()
        control_pts, _ = self._feature_gen.compute(source)
        self._cost_fn._control_pts = control_pts


class RigidSVR(L2DistRegistration):
    def __init__(self, source, sigma=1.0, delta=0.9,
                 gamma=0.5, nu=0.1, use_estimated_sigma=True):
        super(RigidSVR, self).__init__(source,
                                       ft.OneClassSVM(source.shape[1],
                                                      sigma, gamma, nu),
                                       cf.RigidCostFunction(),
                                       sigma, delta,
                                       use_estimated_sigma)

    def _estimate_sigma(self, data):
        super(RigidSVR, self)._estimate_sigma(data)
        self._feature_gen._sigma = self._sigma
        self._feature_gen._gamma = 1.0 / (2.0 * np.square(self._sigma))


class TPSSVR(L2DistRegistration):
    def __init__(self, source, sigma=1.0, delta=0.9,
                 gamma=0.5, nu=0.1, alpha=1.0, beta=0.1,
                 use_estimated_sigma=True):
        super(TPSSVR, self).__init__(source,
                                     ft.OneClassSVM(source.shape[1],
                                                    sigma, gamma, nu),
                                     cf.TPSCostFunction([], alpha, beta),
                                     sigma, delta,
                                     use_estimated_sigma)
        self._feature_gen.init()
        control_pts, _ = self._feature_gen.compute(source)
        self._cost_fn._control_pts = control_pts

    def _estimate_sigma(self, data):
        super(TPSSVR, self)._estimate_sigma(data)
        self._feature_gen._sigma = self._sigma
        self._feature_gen._gamma = 1.0 / (2.0 * np.square(self._sigma))


def registration_gmmreg(source, target, tf_type_name='rigid',
                        callbacks=[], **kargs):
    cv = lambda x: np.asarray(x.points if isinstance(x, o3.PointCloud) else x)
    if tf_type_name == 'rigid':
        gmmreg = RigidGMMReg(cv(source), **kargs)
    elif tf_type_name == 'nonrigid':
        gmmreg = TPSGMMReg(cv(source), **kargs)
    else:
        raise ValueError('Unknown transform type %s' % tf_type_name)
    gmmreg.set_callbacks(callbacks)
    return gmmreg.registration(cv(target))


def registration_svr(source, target, tf_type_name='rigid',
                     callbacks=[], **kargs):
    cv = lambda x: np.asarray(x.points if isinstance(x, o3.PointCloud) else x)
    if tf_type_name == 'rigid':
        svr = RigidSVR(cv(source), **kargs)
    elif tf_type_name == 'nonrigid':
        svr = TPSSVR(cv(source), **kargs)
    else:
        raise ValueError('Unknown transform type %s' % tf_type_name)
    svr.set_callbacks(callbacks)
    return svr.registration(cv(target))