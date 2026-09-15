"""
A6 - Friction model library (CasADi symbolic)
=============================================

Friction laws used by the grey-box identification scripts of activity A6.
All models follow the formulations of

    W. C. F. Pinto and H. V. H. Ayala,
    "Ensemble Grey and Black-box Nonlinear System Identification of a
     Positioning System", CBA 2020.  DOI 10.48011/asba.v2i1.1570
    https://ojs.sba.org.br/index.php/cba/article/view/1570

and, for LuGre, of the review

    F. Marques, P. Flores, J. C. P. Claro and H. M. Lankarani,
    "A survey and comparison of several friction force models for dynamic
     analysis of multibody mechanical systems", Nonlinear Dyn (2016), sec. 3.5
    https://link.springer.com/content/pdf/10.1007/s11071-016-2999-3.pdf

and, for GMS, of

    F. Al-Bender, V. Lampaert and J. Swevers,
    "The generalized Maxwell-slip model: a novel model for friction simulation
     and compensation", IEEE Trans. Automatic Control 50(11), 2005.

Only the SYMMETRIC versions are implemented (the paper also proposes
asymmetric variants with separate coefficients for v > 0 and v < 0).

Equation numbers below refer to the CBA paper.

    (4)  Coulomb + viscous          F = Fv*v + Fc*sign(v)
    (8)  Threlfall (finite slope)   F = Fv*v + Fc*Kz*sign(v),  |v| <= v0
                                    F = Fv*v + Fc*sign(v),     |v| >  v0
    (9)  Kz = (1 - exp(-3|v|/v0)) / (1 - exp(-3))
    (10) Tustin                     F = Fv*v + Fc*sign(v) + (Fs - Fc)*exp(-|v|/vs)
    (11) Coulomb + viscous + Stribeck
                                    F = Fv*v + (Fc + (Fs-Fc)*exp(-(|v|/vs)^d))*sign(v)

and, outside the CBA paper,

    LuGre  dynamic, one bristle state (sec. 3.5 of the review above)
    GMS    dynamic, N elasto-slip elements in parallel (Al-Bender et al.)

A note on equation (10): as printed in the paper the Stribeck term
`(Fs - Fc)*exp(-|v|/vs)` is NOT multiplied by `sign(v)`, so the resulting
friction law is not an odd function of the velocity - it adds the same positive
force for v > 0 and v < 0.  The model is implemented here exactly as printed
(`tustin`), and the odd/physically symmetric variant is also provided
(`tustin_odd`) so that the effect of that detail can be measured.

Units are deliberately unspecified: the caller supplies the parameter bounds,
so the same code serves the EMPS case study (friction as a FORCE, in N) and the
ball-and-beam case study (friction folded with the mass, i.e. an ACCELERATION).

Every model exposes the same interface:

    model.param_names   -> list[str]         parameters, in order
    model.n_states      -> int               internal friction states (LuGre: 1)
    model.force(v, p, z) -> (F, dz)          CasADi expressions
    model.z_steady(v, p) -> (n_states, len(v))   numpy, steady-sliding states

`p` is the (already de-normalised) parameter vector slice belonging to the
friction model and `z` holds its internal states (empty for the static models).
`z_steady` exists so that a dynamic model can be *plotted* as a static F(v)
curve: it returns the internal states the model settles on while sliding at a
constant velocity, which is the only sense in which a law with memory has a
friction curve at all.
"""

import numpy as np
import casadi as ca

# ---------------------------------------------------------------------------
# Default parameter bounds (lo, hi).  Case-study scripts override what they
# need; anything not overridden falls back to these.
# ---------------------------------------------------------------------------
DEFAULT_BOUNDS = {
    "Fv": (0.0, 300.0),      # viscous coefficient
    "Fc": (0.0, 40.0),       # Coulomb (kinetic) level
    "Fs": (0.0, 60.0),       # static / breakaway level
    "vs": (1e-4, 0.05),      # Stribeck velocity
    "v0": (1e-4, 0.05),      # Threlfall tolerance velocity
    "delta": (0.2, 3.0),     # Stribeck shape exponent (delta_sigma)
    "sigma0": (1e3, 1e7),    # LuGre bristle stiffness
    "sigma1": (0.0, 1e4),    # LuGre bristle damping
    # GMS: one stiffness per elasto-slip element, a shared attraction rate and
    # the weights of the elements on the simplex (the last one takes the slack)
    "k1": (1e3, 1e7),        # GMS element stiffness
    "k2": (1e3, 1e7),
    "k3": (1e3, 1e7),
    "C": (1e1, 1e5),         # GMS attraction rate towards the Stribeck curve
    "a1": (0.0, 1.0),        # GMS element weight (fraction of the slip level)
    "a2": (0.0, 1.0),
}


def _sign(v, eps):
    """sign(v), optionally smoothed by tanh(v/eps).

    `eps = 0` reproduces the hard `sign` used in the reference MATLAB code.
    A small positive `eps` removes the discontinuity at v = 0, which is exactly
    the ingredient the CBA paper credits for the better-behaved models - and,
    just as importantly here, it gives IPOPT a differentiable ODE instead of one
    whose Jacobian is meaningless at every velocity zero-crossing.
    """
    return ca.sign(v) if eps <= 0.0 else ca.tanh(v / eps)


def _abs(v, eps):
    """|v|, smoothed to sqrt(v^2 + eps^2) when eps > 0 (note: |0| becomes eps)."""
    return ca.fabs(v) if eps <= 0.0 else ca.sqrt(v * v + eps * eps)


class FrictionModel:
    """Base class: a static friction law with no internal state."""

    name = "base"
    param_names = []
    n_states = 0

    def __init__(self, bounds=None, sign_eps=0.0):
        self.bounds = dict(DEFAULT_BOUNDS)
        if bounds:
            self.bounds.update(bounds)
        self.sign_eps = float(sign_eps)

    # -- bounds -------------------------------------------------------------
    def param_min(self):
        return [self.bounds[k][0] for k in self.param_names]

    def param_max(self):
        return [self.bounds[k][1] for k in self.param_names]

    # -- dynamics -----------------------------------------------------------
    def force(self, v, p, z):
        """Return (friction force expression, internal-state derivative)."""
        raise NotImplementedError

    def z_steady(self, v, p):
        """Internal states while sliding at the constant velocities `v`.

        Numpy, shape (n_states, len(v)).  A static law has none, so the default
        is an empty array of the right width.
        """
        return np.zeros((0, len(np.atleast_1d(v))))

    def __repr__(self):
        return f"{self.name}({', '.join(self.param_names)})"


# ---------------------------------------------------------------------------
# (4) Coulomb + viscous  -- the model used in the provided EMPS MATLAB code
# ---------------------------------------------------------------------------
class Coulomb(FrictionModel):
    name = "coulomb"
    param_names = ["Fv", "Fc"]

    def force(self, v, p, z):
        Fv, Fc = p[0], p[1]
        return Fv * v + Fc * _sign(v, self.sign_eps), ca.MX.zeros(0, 1)


# ---------------------------------------------------------------------------
# (8)-(9) Threlfall: Coulomb with a finite slope at zero velocity
# ---------------------------------------------------------------------------
class Threlfall(FrictionModel):
    name = "threlfall"
    param_names = ["Fv", "Fc", "v0"]

    def force(self, v, p, z):
        Fv, Fc, v0 = p[0], p[1], p[2]
        av = _abs(v, self.sign_eps)
        Kz = (1.0 - ca.exp(-3.0 * av / v0)) / (1.0 - ca.exp(-3.0))
        Kz = ca.if_else(av <= v0, Kz, 1.0)
        return Fv * v + Fc * Kz * _sign(v, self.sign_eps), ca.MX.zeros(0, 1)


# ---------------------------------------------------------------------------
# (10) Tustin -- LITERAL transcription of the equation as printed
# ---------------------------------------------------------------------------
class Tustin(FrictionModel):
    name = "tustin"
    param_names = ["Fv", "Fc", "Fs", "vs"]

    def force(self, v, p, z):
        Fv, Fc, Fs, vs = p[0], p[1], p[2], p[3]
        F = Fv * v + Fc * _sign(v, self.sign_eps) \
            + (Fs - Fc) * ca.exp(-_abs(v, self.sign_eps) / vs)
        return F, ca.MX.zeros(0, 1)


# ---------------------------------------------------------------------------
# (10') Tustin, odd version: the Stribeck term also multiplied by sign(v)
# ---------------------------------------------------------------------------
class TustinOdd(FrictionModel):
    name = "tustin_odd"
    param_names = ["Fv", "Fc", "Fs", "vs"]

    def force(self, v, p, z):
        Fv, Fc, Fs, vs = p[0], p[1], p[2], p[3]
        s = _sign(v, self.sign_eps)
        F = Fv * v + (Fc + (Fs - Fc) * ca.exp(-_abs(v, self.sign_eps) / vs)) * s
        return F, ca.MX.zeros(0, 1)


# ---------------------------------------------------------------------------
# (11) Coulomb + viscous + Stribeck (Bo & Pavelescu)
# ---------------------------------------------------------------------------
class Stribeck(FrictionModel):
    name = "stribeck"
    param_names = ["Fv", "Fc", "Fs", "vs", "delta"]

    def force(self, v, p, z):
        Fv, Fc, Fs, vs, d = p[0], p[1], p[2], p[3], p[4]
        # (av/vs)^d needs av > 0, so the floor below keeps `pow` (and its
        # derivative) finite at a velocity zero-crossing.  Clamping is used
        # rather than the sqrt smoothing of `_abs`: sqrt(v^2 + e^2) would make
        # d(av)/dv = v/av, and combined with the av^(d-1) of the power rule
        # that sends the Jacobian to ~1/e near v = 0.  IPOPT then crawls
        # (this cost a 1 min EMPS fit turning into a > 28 min one).
        av = ca.fmax(_abs(v, self.sign_eps), 1e-12)
        F = Fv * v + (Fc + (Fs - Fc) * ca.exp(-(av / vs) ** d)) \
            * _sign(v, self.sign_eps)
        return F, ca.MX.zeros(0, 1)


# ---------------------------------------------------------------------------
# LuGre (dynamic friction, one internal bristle state) -- the "extra" activity
# ---------------------------------------------------------------------------
class LuGre(FrictionModel):
    name = "lugre"
    param_names = ["Fv", "Fc", "Fs", "vs", "sigma0", "sigma1"]
    n_states = 1

    def force(self, v, p, z):
        Fv, Fc, Fs, vs, s0, s1 = p[0], p[1], p[2], p[3], p[4], p[5]
        zz = z[0]
        # the exponent is clipped: with a small vs, (v/vs)^2 overflows long
        # before the term matters, and IPOPT then only sees NaNs
        g = Fc + (Fs - Fc) * ca.exp(-ca.fmin((v / vs) ** 2, 50.0))
        g = ca.fmax(g, 1e-9)                            # Stribeck curve, > 0
        dz = v - s0 * _abs(v, self.sign_eps) * zz / g
        F = s0 * zz + s1 * dz + Fv * v
        return F, ca.vertcat(dz)

    def z_steady(self, v, p):
        v = np.atleast_1d(np.asarray(v, dtype=float))
        Fc, Fs, vs, s0 = float(p[1]), float(p[2]), float(p[3]), float(p[4])
        g = Fc + (Fs - Fc) * np.exp(-np.minimum((v / vs) ** 2, 50.0))
        return (g * np.sign(v) / s0).reshape(1, -1)


# ---------------------------------------------------------------------------
# GMS - Generalized Maxwell-Slip (dynamic, one state per element)
# ---------------------------------------------------------------------------
class GMS(FrictionModel):
    """N elasto-slip elements in parallel, each with its own break-away level.

    Where LuGre lumps the contact into a single bristle, GMS splits it into N
    blocks that leave the stuck regime one after another.  That is the whole
    point of the model: a single element gives one break-away velocity, several
    elements give the gradual transition and the non-local memory (hysteresis
    with a history that survives velocity reversals) actually measured in
    pre-sliding.  Al-Bender et al. therefore describe GMS as the model that
    fixes what LuGre gets wrong at small displacements.

    State
    -----
    The state of element i is its own friction force `F_i` (not the deflection),
    which is the form used in the reference and keeps the equations free of the
    stiffness in the slipping branch:

        stuck    dF_i/dt = k_i * v
        slipping dF_i/dt = sign(v) * C * alpha_i * (1 - F_i*sign(v)/(alpha_i*s))

    with `s(|v|)` the Stribeck magnitude shared by every element and `alpha_i`
    the element's share of it (the alphas sum to 1, so in steady sliding the
    forces add up to exactly `s`, the same curve the static laws produce).

    Smoothing
    ---------
    Textbook GMS is a state machine: an element sticks until |F_i| reaches its
    threshold, then slips until the velocity reverses.  Switching like that has
    no useful Jacobian, and IPOPT needs one.  The two branches are therefore
    blended by a sigmoid of the saturation ratio `r_i = F_i*sign(v)/(alpha_i*s)`,
    which is < 1 while stuck and >= 1 at the slip boundary:

        w_i = (1 + tanh((r_i - 1)/blend_eps)) / 2
        dF_i/dt = (1 - max(r_i, 0)) * [ (1 - w_i)*k_i*v + w_i*sign(v)*C*alpha_i ]

    The reversal rule comes out for free: when the velocity flips, `r_i` goes
    negative, `w_i` collapses to 0 and the element sticks again.

    The `(1 - max(r_i, 0))` factor is not cosmetic.  Blending the two branches
    alone leaves `r_i = 1` at half stick rate rather than at rest, so the
    element would drift past its own threshold and steady sliding would no
    longer land on the Stribeck curve.  Multiplying both branches by that
    factor makes `r_i = 1` the equilibrium (verified in the smoke test at the
    foot of this file: exact with a hard `max`, and 0.04 % low once it is
    smoothed, which is the same order as LuGre's own residual), at the price of
    a presliding stiffness that softens as the element approaches break-away -
    which is exactly the regularisation Dupont's elastoplastic models apply for
    the same reason.

    `blend_eps` is the width of that transition in units of the ratio; 0.05
    keeps the switch tight enough to still look like stick-slip while leaving
    the gradient finite.  It is a numerical constant, not a fitted parameter.

    Cost
    ----
    N states and 5 + 2N - 1 parameters: with N = 2 that is 8 parameters, the
    same count as LuGre, and 2 states instead of 1.  The RK4 caveat of LuGre
    applies twice over - see the note on the bounds in the case-study scripts.
    """

    name = "gms"
    n_elements = 2
    blend_eps = 0.05

    def __init__(self, bounds=None, sign_eps=0.0, n_elements=None):
        super().__init__(bounds=bounds, sign_eps=sign_eps)
        if n_elements is not None:
            self.n_elements = int(n_elements)
        n = self.n_elements
        if n < 1 or n > 3:
            raise ValueError("GMS is set up for 1..3 elements (see bounds)")
        # instance-level, because they depend on the number of elements
        self.param_names = (["Fv", "Fc", "Fs", "vs", "C"]
                            + [f"k{i + 1}" for i in range(n)]
                            + [f"a{i + 1}" for i in range(n - 1)])
        self.n_states = n

    # -- element weights ----------------------------------------------------
    def _alphas(self, p, lib):
        """Weights on the simplex, symbolic (lib=ca) or numeric (lib=np).

        The free parameters are the first N-1 weights; the last one takes up
        the slack.  The vector is then renormalised, so the sum is 1 even if
        the solver walks the free weights past it - without that, a search
        direction that inflates the alphas would also inflate the total slip
        level and duplicate what Fc/Fs already do.
        """
        n, off = self.n_elements, 5 + self.n_elements
        free = [p[off + i] for i in range(n - 1)]
        floor = 1e-6
        if lib is ca:
            a = [ca.fmax(x, floor) for x in free]
            a.append(ca.fmax(1.0 - sum(free), floor) if free else 1.0)
        else:
            a = [max(float(x), floor) for x in free]
            a.append(max(1.0 - sum(float(x) for x in free), floor) if free else 1.0)
        total = sum(a)
        return [x / total for x in a]

    def _stribeck(self, av, p, lib):
        """Shared Stribeck magnitude s(|v|), floored away from zero."""
        Fc, Fs, vs = p[1], p[2], p[3]
        if lib is ca:
            e = ca.exp(-ca.fmin((av / vs) ** 2, 50.0))
            return ca.fmax(Fc + (Fs - Fc) * e, 1e-9)
        e = np.exp(-np.minimum((av / vs) ** 2, 50.0))
        return np.maximum(Fc + (Fs - Fc) * e, 1e-9)

    # -- dynamics -----------------------------------------------------------
    def force(self, v, p, z):
        Fv, C = p[0], p[4]
        n = self.n_elements
        k = [p[5 + i] for i in range(n)]
        alphas = self._alphas(p, ca)
        s = self._stribeck(_abs(v, self.sign_eps), p, ca)
        sgn = _sign(v, self.sign_eps)

        F = Fv * v
        dz = []
        for i in range(n):
            Fi = z[i]
            thr = ca.fmax(alphas[i] * s, 1e-9)     # this element's slip level
            r = Fi * sgn / thr                     # 1 exactly at the boundary
            w = 0.5 * (1.0 + ca.tanh((r - 1.0) / self.blend_eps))
            # `sat` is what makes r = 1 an exact fixed point: it multiplies BOTH
            # branches, so the element stops growing precisely at its threshold
            # and is pulled back if the threshold later drops (which it does -
            # the Stribeck curve shrinks as the velocity rises).  The positive
            # part keeps it at 1 while the force opposes the motion (r < 0, just
            # after a reversal), so unloading is fully elastic.
            #
            # That positive part is SMOOTHED rather than written fmax(r, 0):
            # r crosses zero at every velocity reversal, i.e. constantly, and a
            # kink sitting there is a kink the solver walks into on most
            # samples.  Same reasoning as `_sign`/`_abs` above - and it is not
            # academic: with the hard fmax an 11-parameter fit that should take
            # a couple of minutes ran past 30 without converging.
            e = self.blend_eps
            sat = 1.0 - 0.5 * (r + ca.sqrt(r * r + e * e))
            rate = (1.0 - w) * k[i] * v + w * sgn * C * alphas[i]
            dz.append(sat * rate)
            F = F + Fi
        return F, ca.vertcat(*dz)

    def z_steady(self, v, p):
        # every element slipping: F_i = alpha_i * s(|v|) * sign(v), so the sum
        # is the Stribeck curve itself
        v = np.atleast_1d(np.asarray(v, dtype=float))
        s = self._stribeck(np.abs(v), p, np)
        alphas = self._alphas(p, np)
        return np.vstack([a * s * np.sign(v) for a in alphas])


class GMS3(GMS):
    """GMS with three elements (11 parameters, 3 states)."""

    name = "gms3"
    n_elements = 3


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
FRICTION_MODELS = {
    "coulomb": Coulomb,
    "threlfall": Threlfall,
    "tustin": Tustin,
    "tustin_odd": TustinOdd,
    "stribeck": Stribeck,
    "lugre": LuGre,
    "gms": GMS,
    "gms3": GMS3,
}


def make_friction(name, bounds=None, sign_eps=0.0):
    """Instantiate a friction model by name (see FRICTION_MODELS)."""
    try:
        cls = FRICTION_MODELS[name]
    except KeyError:
        raise ValueError(
            f"Unknown friction model '{name}'. "
            f"Available: {sorted(FRICTION_MODELS)}"
        ) from None
    return cls(bounds=bounds, sign_eps=sign_eps)


# ---------------------------------------------------------------------------
# Smoke test:  python friction_models.py
#
# Checks the three properties of the dynamic laws that are easy to get wrong
# and impossible to notice from a fitted R^2:
#   1. steady sliding lands exactly on the Stribeck curve (it is a fixed point);
#   2. a slow small oscillation traces a presliding hysteresis loop;
#   3. the worst corner of the parameter box still integrates with the fixed-step
#      RK4 the identification uses.
# ---------------------------------------------------------------------------
def _smoke_test():
    # ball-and-beam boxes: friction folded with the mass, so su/s^2
    bounds = {"Fv": (0., 100.), "Fc": (0., 150.), "Fs": (0., 300.),
              "vs": (0.05, 10.), "k1": (10., 1000.), "k2": (10., 1000.),
              "C": (10., 2000.), "a1": (0., 1.),
              "sigma0": (10., 1000.), "sigma1": (0., 200.)}
    dt = 0.05 / 10                      # the sub-step of the case studies

    def rk4(fn, z, v):
        a = np.array(fn(v, ca.DM(z))[1]).ravel()
        b = np.array(fn(v, ca.DM(z + dt / 2 * a))[1]).ravel()
        c = np.array(fn(v, ca.DM(z + dt / 2 * b))[1]).ravel()
        d = np.array(fn(v, ca.DM(z + dt * c))[1]).ravel()
        return z + dt / 6 * (a + 2 * b + 2 * c + d)

    for name, p in (("gms",   [5., 60., 90., 2., 800., 600., 120., 0.35]),
                    ("lugre", [5., 60., 90., 2., 400., 20.])):
        f = make_friction(name, bounds=bounds, sign_eps=0.25)
        p = np.array(p, dtype=float)
        vs_, zs_ = ca.MX.sym("v"), ca.MX.sym("z", f.n_states)
        F, dz = f.force(vs_, ca.DM(p), zs_)
        fn = ca.Function("f", [vs_, zs_], [F, dz])
        print(f"\n{name}: {f.param_names}")

        # 1. steady sliding, well clear of the smoothed sign
        for v in (8.0, 30.0):
            z = np.zeros(f.n_states)
            for _ in range(4000):
                z = rk4(fn, z, v)
            got = float(fn(v, ca.DM(z))[0])
            want = float(fn(v, ca.DM(f.z_steady([v], p)[:, 0]))[0])
            print(f"  v={v:>5.1f}  converged F={got:>9.4f}  z_steady F={want:>9.4f}"
                  f"  diff={abs(got - want):.2e}")

        # 2. presliding loop, force against displacement
        t = np.arange(0, 6.0, dt)
        v_tr = 0.25 * np.sin(2 * np.pi * 0.5 * t)
        x = np.cumsum(v_tr) * dt
        z, hist = np.zeros(f.n_states), []
        for v in v_tr:
            z = rk4(fn, z, v)
            hist.append(float(fn(v, ca.DM(z))[0]))
        m = t > 3.0                                   # skip the first cycle
        area = abs(np.trapezoid(np.array(hist)[m], x[m]))
        print(f"  presliding hysteresis area = {area:.4f}")

        # 3. the stiffest corner of the box, fast and large
        p_hard = np.array([b[1] if n not in ("Fv", "Fc", "Fs", "vs") else v
                           for n, v, b in zip(f.param_names, p,
                                              [f.bounds[k] for k in f.param_names])])
        F2, dz2 = f.force(vs_, ca.DM(p_hard), zs_)
        fn2 = ca.Function("f2", [vs_, zs_], [F2, dz2])
        z, big = np.zeros(f.n_states), []
        for v in 40 * np.sin(2 * np.pi * 3 * t):
            z = rk4(fn2, z, v)
            big.append(float(fn2(v, ca.DM(z))[0]))
        big = np.array(big)
        print(f"  worst-case box: finite={np.all(np.isfinite(big))}  "
              f"|F|max={np.abs(big).max():.2f}")


if __name__ == "__main__":
    _smoke_test()
