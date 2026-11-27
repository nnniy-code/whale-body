"""core/cmaes.py — 精简 CMA-ES（(μ/μ_w, λ)，rank-μ 更新）。

设计依据：docs/design/04-library.md、HANDOFF §4.5（三段搜索）

为什么自己写：环境里没有 pycma，而我们需要「按预算分片」的接口
（ask/tell 分离），现成库的 fmin 是阻塞的。
"""

from __future__ import annotations

import numpy as np


class CMAES:
    def __init__(self, x0, sigma: float, lo, hi, popsize: int | None = None, seed: int = 0):
        self.rng = np.random.default_rng(seed)
        self.n = len(x0)
        self.mean = np.asarray(x0, float).copy()
        self.sigma = float(sigma)
        self.lo = np.asarray(lo, float)
        self.hi = np.asarray(hi, float)
        n = self.n

        self.lam = int(popsize) if popsize else 4 + int(3 * np.log(n))
        self.mu = self.lam // 2
        w = np.log(self.mu + 0.5) - np.log(np.arange(1, self.mu + 1))
        self.weights = w / w.sum()
        self.mueff = 1.0 / float((self.weights ** 2).sum())

        self.cc = (4 + self.mueff / n) / (n + 4 + 2 * self.mueff / n)
        self.cs = (self.mueff + 2) / (n + self.mueff + 5)
        self.c1 = 2.0 / ((n + 1.3) ** 2 + self.mueff)
        self.cmu = min(1 - self.c1,
                       2 * (self.mueff - 2 + 1 / self.mueff) / ((n + 2) ** 2 + self.mueff))
        self.damps = 1 + 2 * max(0, np.sqrt((self.mueff - 1) / (n + 1)) - 1) + self.cs
        self.chiN = np.sqrt(n) * (1 - 1 / (4 * n) + 1 / (21 * n * n))

        self.pc = np.zeros(n)
        self.ps = np.zeros(n)
        self.C = np.eye(n)
        self.B = np.eye(n)
        self.D = np.ones(n)
        self.evals = 0
        self.gen = 0
        self._eigen = 0

    # ---- 采样 ----
    def ask(self):
        if self.gen - self._eigen >= max(1, self.n // 5):
            self._update_eigen()
        z = self.rng.standard_normal((self.lam, self.n))
        y = (z * self.D) @ self.B.T
        x = self.mean + self.sigma * y
        x = np.clip(x, self.lo, self.hi)
        return x, y

    def _update_eigen(self):
        C = np.triu(self.C) + np.triu(self.C, 1).T
        vals, vecs = np.linalg.eigh(C)
        vals = np.maximum(vals, 1e-20)
        self.C = (vecs * vals) @ vecs.T
        self.D = np.sqrt(vals)
        self.B = vecs
        self._eigen = self.gen

    # ---- 更新 ----
    def tell(self, x, y, fitness):
        self.gen += 1
        idx = np.argsort(fitness)[:self.mu]
        ysel = y[idx]
        yw = (self.weights[:, None] * ysel).sum(axis=0)

        self.mean = np.clip(self.mean + self.sigma * yw, self.lo, self.hi)

        Cinv_sqrt = self.B @ np.diag(1.0 / self.D) @ self.B.T
        self.ps = ((1 - self.cs) * self.ps
                   + np.sqrt(self.cs * (2 - self.cs) * self.mueff) * (Cinv_sqrt @ yw))
        hsig = (np.linalg.norm(self.ps)
                / np.sqrt(1 - (1 - self.cs) ** (2 * self.gen)) / self.chiN) < (1.4 + 2 / (self.n + 1))
        self.pc = ((1 - self.cc) * self.pc
                   + hsig * np.sqrt(self.cc * (2 - self.cc) * self.mueff) * yw)

        rank_mu = (self.weights[:, None] * ysel).T @ ysel
        self.C = ((1 - self.c1 - self.cmu) * self.C
                  + self.c1 * (np.outer(self.pc, self.pc) + (1 - hsig) * self.cc * (2 - self.cc) * self.C)
                  + self.cmu * rank_mu)
        self.sigma *= np.exp((self.cs / self.damps) * (np.linalg.norm(self.ps) / self.chiN - 1))
        self.sigma = float(np.clip(self.sigma, 1e-8, 1e3))
        self.evals += self.lam
        return float(fitness[idx[0]])


def cma_es(f, x0, sigma, lo, hi, max_evals: int = 2000,
           popsize: int | None = None, seed: int = 0,
           target: float = 1e-9, slice_fn=None):
    """跑完整 CMA-ES。slice_fn(evals_done) -> bool 可用来中断（分片）。"""
    es = CMAES(x0, sigma, lo, hi, popsize=popsize, seed=seed)
    best_x = np.asarray(x0, float).copy()
    best_f = float(f(best_x))
    es.evals += 1
    while es.evals < max_evals:
        x, y = es.ask()
        fit = np.array([f(xi) for xi in x])
        es.tell(x, y, fit)
        i = int(np.argmin(fit))
        if fit[i] < best_f:
            best_f = float(fit[i])
            best_x = x[i].copy()
        if best_f <= target:
            break
        if slice_fn is not None and slice_fn(es.evals):
            break
    return best_x, best_f, es.evals
