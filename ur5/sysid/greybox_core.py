"""
A6 - Grey-box identification core (CasADi + IPOPT)
=================================================

Python port of the machinery in the MATLAB support code `sysidEMPS.m`
(from the CasADi example pack, updated by Lucas Souza and Helon Ayala), kept
deliberately close to the original so it can be read side by side:

    MATLAB                                  here
    ---------------------------------------------------------------
    denorm / normalize                      denorm() / normalize()
    ode = Function('ode',...)               GreyBoxProblem._build()
    RK4 -> one_step -> one_sample           GreyBoxProblem._build()
    one_sample.mapaccum('all_samples', N)   GreyBoxProblem.simulate()
    single shooting NLP                     GreyBoxProblem.identify('single')
    multiple shooting NLP                   GreyBoxProblem.identify('multiple')

Two ideas carried over verbatim from the reference code:

1. **Normalised decision variables.**  Every physical parameter is optimised as
   a number in [0, 1] and de-normalised inside the ODE with
   `theta = theta_min + (theta_max - theta_min) * theta_n`.  Parameters of a
   mechanical system differ by orders of magnitude (a mass of ~95 kg next to a
   Stribeck velocity of ~0.005 m/s); without this rescaling the NLP is badly
   conditioned and IPOPT crawls.  It also turns physical knowledge (plausible
   ranges) into simple box constraints `0 <= theta_n <= 1`.

2. **Multiple shooting.**  Single shooting integrates the whole record from one
   initial condition, so the cost is a composition of N integrator steps: for a
   long record, or an unstable/marginally stable plant, the gradient explodes
   and the NLP is essentially unsolvable.  Multiple shooting instead promotes
   the whole state trajectory to decision variables and glues the pieces with
   equality ("gap") constraints `x_{k+1} - f(x_k, u_k) = 0`.  The problem gets
   much larger but far better conditioned, and it can be initialised with the
   measured trajectory.

Requires: casadi, numpy.
"""

import numpy as np
import casadi as ca


# ---------------------------------------------------------------------------
# Parameter (de)normalisation - `denorm` / `normalize` of the MATLAB script
# ---------------------------------------------------------------------------
def denorm(vn, vmin, vmax):
    return vmin + (vmax - vmin) * vn


def normalize(v, vmin, vmax):
    return (np.asarray(v, dtype=float) - vmin) / (vmax - vmin)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def rmse(y, yhat):
    return float(np.sqrt(np.mean((np.asarray(y) - np.asarray(yhat)) ** 2)))


def r2_score(y, yhat):
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    return float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0


def relative_error(y, yhat):
    """Relative error in %, eq. (17) of the CBA paper: 100*||y-yhat||/||y||."""
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    denom = np.linalg.norm(y)
    return float(100.0 * np.linalg.norm(y - yhat) / denom) if denom > 0 else np.inf


def metrics(y, yhat):
    return {
        "rmse": rmse(y, yhat),
        "r2": r2_score(y, yhat),
        "eps": relative_error(y, yhat),
    }


# ---------------------------------------------------------------------------
# Grey-box problem
# ---------------------------------------------------------------------------
class GreyBoxProblem:
    """Continuous-time grey-box model + RK4 discretisation + NLP fitting.

    Parameters
    ----------
    rhs : callable (x, u, p) -> MX
        Continuous-time state equation.  `p` arrives ALREADY de-normalised, so
        the function can be written in plain physical units.
    nx, nu : int
        Number of states / inputs.
    param_names, param_min, param_max : sequences
        Physical parameters and their box constraints.
    ts : float
        Sampling time of the data.
    n_steps_per_sample : int
        RK4 sub-steps inside one sampling period (10 in the reference code).
    output_index : int or sequence of int
        Index (or indices) of the measured states.  Both case studies measure
        the position, state 0; the ball-and-beam additionally feeds the
        estimated velocity in as an optional second, down-weighted output
        (`W_VELOCITY`), since friction acts on the velocity.
    output_weights : sequence of float, optional
        Relative weight of each output in the least-squares cost.
    """

    def __init__(self, rhs, nx, nu, param_names, param_min, param_max, ts,
                 n_steps_per_sample=10, output_index=0, output_weights=None):
        self.nx = int(nx)
        self.nu = int(nu)
        self.param_names = list(param_names)
        self.param_min = np.asarray(param_min, dtype=float)
        self.param_max = np.asarray(param_max, dtype=float)
        self.nparam = len(self.param_names)
        self.ts = float(ts)
        self.n_steps_per_sample = int(n_steps_per_sample)
        self.output_index = ([int(output_index)] if np.isscalar(output_index)
                             else [int(i) for i in output_index])
        self.output_weights = (np.ones(len(self.output_index)) if output_weights is None
                               else np.asarray(output_weights, dtype=float))
        if len(self.output_weights) != len(self.output_index):
            raise ValueError("output_weights must match output_index")
        self._rhs = rhs
        self._build()

    # -- symbolic construction ---------------------------------------------
    def _build(self):
        x = ca.MX.sym("x", self.nx)
        u = ca.MX.sym("u", self.nu)
        pn = ca.MX.sym("pn", self.nparam)                 # normalised params
        p = denorm(pn, self.param_min, self.param_max)    # physical params

        ode = ca.Function("ode", [x, u, pn], [self._rhs(x, u, p)])
        self.ode = ode

        # Fixed-step RK4, exactly as in sysidEMPS.m
        dt = self.ts / self.n_steps_per_sample
        k1 = ode(x, u, pn)
        k2 = ode(x + dt / 2.0 * k1, u, pn)
        k3 = ode(x + dt / 2.0 * k2, u, pn)
        k4 = ode(x + dt * k3, u, pn)
        x_next = x + dt / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
        one_step = ca.Function("one_step", [x, u, pn], [x_next])

        X = x
        for _ in range(self.n_steps_per_sample):
            X = one_step(X, u, pn)
        # speedup trick from the reference: expand into scalar operations
        self.one_sample = ca.Function("one_sample", [x, u, pn], [X]).expand()

    # -- simulation ---------------------------------------------------------
    def simulate(self, x0, u_data, pn):
        """Free-run simulation of the whole record (mapaccum). Returns (nx, N)."""
        u_data = np.atleast_2d(np.asarray(u_data, dtype=float))
        if u_data.shape[0] != self.nu:
            u_data = u_data.reshape(self.nu, -1)
        N = u_data.shape[1]
        all_samples = self.one_sample.mapaccum("all_samples", N)
        X = all_samples(ca.DM(np.asarray(x0, dtype=float).reshape(-1, 1)),
                        ca.DM(u_data),
                        ca.repmat(ca.DM(np.asarray(pn).reshape(-1, 1)), 1, N))
        return np.asarray(ca.DM(X))

    def simulate_windows(self, X_meas, u_data, pn, k_steps):
        """Simulate in windows of `k_steps` samples, restarting from measurement.

        The honest metric for a plant that is unstable in open loop: a single
        free-run over hundreds of samples diverges no matter how good the model
        is, whereas a k-step-ahead simulation still tests the dynamics (and is
        much harder than the usual one-step-ahead prediction).

        `X_meas` is (nx, N) - the measured/reconstructed state trajectory used
        to restart every window.  Returns the simulated (nx, N).

        Time convention (must match `identify` and `simulate`)
        -----------------------------------------------------
        The NLP cost is built on `Xn = one_sample(X, u)`, so the fitted model
        states that `y[k] ~ one_sample(x_k, u_k)` - the sample measured at k is
        the state one integration step AFTER the shooting node x_k.  `simulate`
        inherits that from `mapaccum` (its column k is the state after k+1
        steps).  This routine therefore has to STEP FIRST and RECORD AFTER, so
        that column k likewise holds the model's prediction of `y[k]`.

        Recording before stepping instead would put the restart measurement
        itself in column `start` and shift the whole window one sample late,
        scoring every model against a signal it was never fitted to.
        """
        X_meas = np.asarray(X_meas, dtype=float)
        u_data = np.atleast_2d(np.asarray(u_data, dtype=float)).reshape(self.nu, -1)
        N = u_data.shape[1]
        out = np.empty((self.nx, N))
        one = self.one_sample
        pn_dm = ca.DM(np.asarray(pn).reshape(-1, 1))
        for start in range(0, N, k_steps):
            stop = min(start + k_steps, N)
            xk = ca.DM(X_meas[:, start].reshape(-1, 1))     # restart from data
            for k in range(start, stop):
                xk = one(xk, ca.DM(u_data[:, k].reshape(-1, 1)), pn_dm)
                out[:, k] = np.asarray(ca.DM(xk)).ravel()
        return out

    # -- identification -----------------------------------------------------
    def identify(self, u_data, y_data, x0=None, strategy="multiple",
                 param_guess=None, x_guess=None, ipopt_opts=None, verbose=False):
        """Fit the parameters. Returns dict with normalised/physical estimates."""
        u_data = np.atleast_2d(np.asarray(u_data, dtype=float)).reshape(self.nu, -1)
        N = u_data.shape[1]
        n_out = len(self.output_index)
        y_data = np.asarray(y_data, dtype=float).reshape(n_out, N)

        def cost(Xsym):
            """Weighted least squares over all measured states."""
            J = 0
            for j, (idx, w) in enumerate(zip(self.output_index, self.output_weights)):
                e = ca.DM(y_data[j].reshape(-1, 1)) - Xsym[idx, :].T
                J = J + w * ca.dot(e, e) / N
            return J

        if param_guess is None:
            param_guess = np.random.rand(self.nparam)      # `rand` of the ref.
        param_guess = np.clip(np.asarray(param_guess, dtype=float).ravel(), 0.0, 1.0)

        opts = {
            "ipopt.acceptable_tol": 1e-4,
            "ipopt.acceptable_obj_change_tol": 1e-4,
            "ipopt.print_level": 5 if verbose else 0,
            "ipopt.sb": "yes",
            "print_time": bool(verbose),
            # IPOPT probes points where the RK4 integrator overflows and
            # CasADi then prints a "NaN detected" warning for each of them.
            # They are benign - IPOPT handles the NaN by shortening the step -
            # and this trims most of them (the stiff LuGre fits still emit
            # some).
            "regularity_check": False,
        }
        if ipopt_opts:
            opts.update(ipopt_opts)

        pn = ca.MX.sym("pn", self.nparam)
        lbx_p = np.zeros(self.nparam)
        ubx_p = np.ones(self.nparam)

        if strategy == "single":
            if x0 is None:
                raise ValueError("single shooting needs x0")
            all_samples = self.one_sample.mapaccum("all_samples", N)
            Xsym = all_samples(ca.DM(np.asarray(x0, float).reshape(-1, 1)),
                               ca.DM(u_data), ca.repmat(pn, 1, N))
            nlp = {"x": pn, "f": cost(Xsym)}
            solver = ca.nlpsol("solver", "ipopt", nlp, opts)
            sol = solver(x0=param_guess, lbx=lbx_p, ubx=ubx_p)
            v = np.asarray(sol["x"]).ravel()
            pn_hat = v[: self.nparam]
            X_hat = None
        elif strategy == "multiple":
            X = ca.MX.sym("X", self.nx, N)
            res = self.one_sample.map(N, "thread", 4)
            Xn = res(X, ca.DM(u_data), ca.repmat(pn, 1, N))
            gaps = Xn[:, : N - 1] - X[:, 1:]
            V = ca.veccat(pn, X)
            nlp = {"x": V, "f": cost(Xn), "g": ca.vec(gaps)}

            if x_guess is None:
                raise ValueError("multiple shooting needs x_guess (nx, N)")
            x_guess = np.asarray(x_guess, dtype=float).reshape(self.nx, N)
            v0 = np.concatenate([param_guess, x_guess.reshape(-1, order="F")])

            # NOTE: sysidEMPS.m calls the solver WITHOUT lbx/ubx in the
            # multiple-shooting branch, so the [0, 1] box on the normalised
            # parameters is silently lost there and the estimates can leave
            # their physical range (we saw Fs ~ 8e3 N with a [0, 60] N box).
            # The box is restored here; the state variables stay free.
            lbx = np.concatenate([lbx_p, np.full(self.nx * N, -np.inf)])
            ubx = np.concatenate([ubx_p, np.full(self.nx * N, np.inf)])
            solver = ca.nlpsol("solver", "ipopt", nlp, opts)
            sol = solver(x0=v0, lbx=lbx, ubx=ubx, lbg=0, ubg=0)
            v = np.asarray(sol["x"]).ravel()
            pn_hat = v[: self.nparam]
            X_hat = v[self.nparam:].reshape(self.nx, N, order="F")
        else:
            raise ValueError("strategy must be 'single' or 'multiple'")

        stats = solver.stats()
        return {
            "pn": pn_hat,
            "p": denorm(pn_hat, self.param_min, self.param_max),
            "f": float(sol["f"]),
            "X": X_hat,
            "success": bool(stats.get("success", False)),
            "return_status": stats.get("return_status", "?"),
            "iter_count": stats.get("iter_count", -1),
        }

    # -- reporting ----------------------------------------------------------
    def param_table(self, p, title="identified parameters"):
        lines = [title, "-" * max(len(title), 34)]
        for name, val, lo, hi in zip(self.param_names, np.asarray(p).ravel(),
                                     self.param_min, self.param_max):
            edge = ""
            span = hi - lo
            if span > 0 and (val - lo) / span < 1e-3:
                edge = "  <-- at lower bound"
            elif span > 0 and (hi - val) / span < 1e-3:
                edge = "  <-- at upper bound"
            lines.append(f"  {name:<10} {val:>14.6g}   [{lo:g}, {hi:g}]{edge}")
        return "\n".join(lines)
