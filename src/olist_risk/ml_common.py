"""Shared modelling utilities: pipelines, metrics, business-cost model, experiment runner, charts.

Every R$ figure produced here is CONDITIONAL on explicit cost assumptions (Olist has no refund or
churn data). The cost model therefore reports a break-even intervention cost, not just one number.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, precision_recall_curve, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from olist_risk import viz

TOP_SHARES = (0.05, 0.10, 0.20)  # pre-registered flag budgets; never tuned on the test set


# --------------------------------------------------------------------------- pipelines
def build_pipelines(numeric: list[str], categorical: list[str], seed: int = 42) -> dict[str, Pipeline]:
    """A transparent linear baseline and a gradient-boosted model over the same inputs."""
    linear_pre = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
        ("cat", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=100, sparse_output=False), categorical),
    ])
    tree_pre = ColumnTransformer([
        ("num", "passthrough", numeric),
        ("cat", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=np.nan, encoded_missing_value=np.nan), categorical),
    ])
    cat_mask = [False] * len(numeric) + [True] * len(categorical)
    return {
        "logistic": Pipeline([("pre", linear_pre), ("clf", LogisticRegression(max_iter=2000, C=0.5))]),
        "hist_gb": Pipeline([("pre", tree_pre), ("clf", HistGradientBoostingClassifier(
            max_iter=250, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=50, l2_regularization=1.0,
            categorical_features=cat_mask, early_stopping=False, random_state=seed))]),
    }


# --------------------------------------------------------------------------- metrics
def evaluate(y: np.ndarray, p: np.ndarray, top_share: float = 0.10) -> dict:
    """Ranking metrics plus precision / recall when the top `top_share` of scores is flagged."""
    y, p = np.asarray(y, dtype=int), np.asarray(p, dtype=float)
    k = max(1, int(round(top_share * len(y))))
    flagged = np.argsort(-p, kind="stable")[:k]
    tp = int(y[flagged].sum())
    base = float(y.mean())
    return {"n": int(len(y)), "positives": int(y.sum()), "base_rate": base,
            "roc_auc": float(roc_auc_score(y, p)), "pr_auc": float(average_precision_score(y, p)),
            "brier": float(brier_score_loss(y, p)), "top_share": top_share,
            "precision_top": tp / k, "recall_top": tp / max(1, int(y.sum())), "lift_top": (tp / k) / base if base else float("nan")}


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> pd.DataFrame:
    df = pd.DataFrame({"y": y, "p": p})
    df["bin"] = pd.qcut(df["p"].rank(method="first"), bins, labels=False)
    return df.groupby("bin").agg(n=("y", "size"), mean_pred=("p", "mean"), obs_rate=("y", "mean")).reset_index()


# --------------------------------------------------------------------------- business cost
@dataclass(frozen=True)
class CostModel:
    """exposure: R$ at stake per true positive if nobody acts; success_rate: share of flagged true
    positives the intervention rescues; intervention_cost: R$ spent per flagged item (right or wrong)."""
    exposure: float
    success_rate: float
    intervention_cost: float

    def net(self, tp: float, fp: float) -> float:
        return tp * (self.success_rate * self.exposure - self.intervention_cost) - fp * self.intervention_cost

    def break_even_cost(self, precision: float) -> float:
        """Highest per-item intervention cost at which flagging still pays, given the flags' precision."""
        return precision * self.success_rate * self.exposure


def benefit_curve(y: np.ndarray, p: np.ndarray, cost: CostModel, exposure: np.ndarray | None = None) -> pd.DataFrame:
    """Net benefit as the flag budget grows (highest scores flagged first). `exposure` optionally gives a
    per-item R$ at stake (e.g. each seller's own profit), replacing the scalar cost.exposure."""
    order = np.argsort(-np.asarray(p), kind="stable")
    y_o = np.asarray(y, dtype=float)[order]
    exp_o = np.full(len(order), cost.exposure) if exposure is None else np.asarray(exposure, dtype=float)[order]
    gain = y_o * (cost.success_rate * exp_o - cost.intervention_cost) - (1 - y_o) * cost.intervention_cost
    k = np.arange(1, len(order) + 1)
    tp = np.cumsum(y_o)
    gross = np.cumsum(y_o * cost.success_rate * exp_o)       # value rescued, before intervention costs
    return pd.DataFrame({"share_flagged": k / len(order), "tp": tp, "fp": k - tp, "net": np.cumsum(gain), "gross": gross})


def net_at_shares(y: np.ndarray, p: np.ndarray, cost: CostModel, shares=TOP_SHARES,
                  exposure: np.ndarray | None = None) -> list[dict]:
    curve = benefit_curve(y, p, cost, exposure)
    rows = []
    for s in shares:
        r = curve.iloc[max(0, int(round(s * len(curve))) - 1)]
        k = r["tp"] + r["fp"]
        rows.append({"share_flagged": s, "net": float(r["net"]), "precision": float(r["tp"] / k),
                     "break_even_cost": float(r["gross"] / k)})  # mean rescued value per flag = max affordable cost
    return rows


# --------------------------------------------------------------------------- experiment runner
PRIMARY_MODEL = "hist_gb"  # pre-specified before seeing any test metric; logistic is the reported baseline


def run_experiment(train: pd.DataFrame, test: pd.DataFrame, numeric: list[str], categorical: list[str],
                   target: str) -> dict:
    """Fit both models on `train`, score `test`. The primary model is fixed in advance (no selection on test);
    both models' test metrics are reported. Categorical levels are learned from train only."""
    cols = numeric + categorical
    out: dict = {"models": {}, "scores": pd.DataFrame({"y": test[target].to_numpy()}, index=test.index)}
    for name, pipe in build_pipelines(numeric, categorical).items():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pipe.fit(train[cols], train[target].astype(int))
            p = pipe.predict_proba(test[cols])[:, 1]
        out["scores"][name] = p
        out["models"][name] = {**evaluate(test[target].to_numpy(), p),
                               "at_shares": {f"{s:.2f}": evaluate(test[target].to_numpy(), p, s) for s in TOP_SHARES}}
        out.setdefault("fitted", {})[name] = pipe
    out["train_base_rate"] = float(train[target].mean())
    out["best"] = PRIMARY_MODEL
    return out


# --------------------------------------------------------------------------- charts
def fig_precision_recall(scores: pd.DataFrame, models: dict, best: str, title: str, subtitle: str, path: Path) -> Path:
    fig, ax = viz.new_axes(9.4, 5.8)
    y = scores["y"].to_numpy()
    palette = {"logistic": viz.AXIS, "hist_gb": viz.BLUE}
    ymax = 0.0
    for name in [n for n in models if n != best] + [best]:
        prec, rec, _ = precision_recall_curve(y, scores[name])
        prec, rec = prec[:-1], rec[:-1]                     # drop the artificial (recall 0, precision 1) endpoint
        keep = rec >= 0.01
        ymax = max(ymax, float(prec[keep].max()))
        color = viz.BLUE if name == best else palette.get(name, viz.AXIS)
        ax.plot(rec[keep], prec[keep], color=color, lw=2.4 if name == best else 1.8, zorder=3 if name == best else 2)
        i = int(np.argmin(np.abs(rec - (0.45 if name == best else 0.62))))
        ax.annotate(f"{name}  (PR-AUC {models[name]['pr_auc']:.2f})", (rec[i], prec[i]), xytext=(0, 14 if name == best else 10),
                    textcoords="offset points", color=viz.INK if name == best else viz.INK_2, fontsize=9, fontweight="bold", ha="center")
    base = float(y.mean())
    ax.axhline(base, color=viz.MUTED, ls="--", lw=1.2)
    ax.text(0.02, base + 0.012, f"No-skill baseline = base rate {base * 100:.1f}%", ha="left", color=viz.MUTED, fontsize=9)
    viz.pct(ax), viz.pct(ax, "x")
    ax.set_xlim(0, 1), ax.set_ylim(0, min(1.0, ymax * 1.1))
    ax.set_xlabel("Recall (share of all positives found)")
    ax.set_ylabel("Precision (share of flags that are right)")
    viz.title_block(fig, title, subtitle)
    viz.source_note(fig, "Source: Olist public dataset. Held-out chronological test set; no test data used for fitting or tuning.")
    return viz.save(fig, path)


def fig_net_benefit(y: np.ndarray, p: np.ndarray, base_cost: CostModel, intervention_costs: list[float], title: str,
                    subtitle: str, path: Path, money_unit: float = 1000.0, exposure: np.ndarray | None = None) -> Path:
    """Net benefit vs flag budget for several per-item intervention costs (first three categorical slots)."""
    colors = [viz.BLUE, viz.ORANGE, "#1baf7a"]
    fig, ax = viz.new_axes(9.4, 5.4)
    ax.axhline(0, color=viz.AXIS, lw=1)
    for c, color in zip(intervention_costs, colors):
        curve = benefit_curve(y, p, replace(base_cost, intervention_cost=c), exposure)
        ax.plot(curve["share_flagged"], curve["net"], color=color, zorder=3)
        viz.end_label(ax, 1.0, float(curve["net"].iloc[-1]), f"R${c:g} per flag", color)
    viz.pct(ax, "x")
    ax.set_xlim(0, 1.0)
    ax.yaxis.set_major_formatter(viz.FuncFormatter(lambda v, _: f"{'-' if v < 0 else ''}R\\${abs(v) / money_unit:,.0f}k"))
    ax.set_xlabel("Share flagged, highest risk first")
    ax.set_ylabel("Net benefit vs doing nothing")
    viz.title_block(fig, title, subtitle)
    viz.source_note(fig, "Illustrative: rests on the stated cost assumptions (see docs). Net benefit above zero means flagging pays for itself.")
    fig.subplots_adjust(right=0.85)
    return viz.save(fig, path)
