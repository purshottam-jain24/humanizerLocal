"""Train the v3 combining layer and export layer_v3.json for the backend.

Features per passage (same order as the backend): [fakespot AI-logit, fakespot pooled(768), desklib logit, desklib pooled(1024)].
Held out entirely (never trained on): my own current-generation technical answers, arXiv human abstracts,
and a 25% split of Stack Exchange human answers and of qwen technical answers.
"""
import json, sys, numpy as np, torch
rng = np.random.default_rng(42)
TAGS = sys.argv[1].split(",")            # e.g. base,pool,se,tech
LAM = float(sys.argv[2]) if len(sys.argv) > 2 else 0.1
TARGET_FA = 0.03
def load(tag):
    parts = [np.load(f"feats_{m}_{tag}.npz") for m in ("fakespot", "desklib")]
    X = np.concatenate([np.concatenate([p["z"][:, None], p["f"]], 1) for p in parts], 1)
    return X, parts[0]["model"].astype(str), parts[0]["domain"].astype(str)
Xs, M, D, S = [], [], [], []
for t in TAGS:
    X, m, d = load(t); Xs.append(X); M.append(m); D.append(d); S.append(np.array([t] * len(m)))
X = np.concatenate(Xs); M = np.concatenate(M); D = np.concatenate(D); S = np.concatenate(S)
y = (M != "human").astype(float)
held = np.isin(D, ["arxiv-tech"]) | (M == "claude-modern-tech")
split_groups = np.isin(M, ["qwen-tech", "qwen-tech2"]) | np.char.startswith(D, "se-")
held |= split_groups & (rng.random(len(M)) < 0.25)
held |= (D == "arxiv2") & (rng.random(len(M)) < 0.5)
if "--train-hard" not in sys.argv: held |= (S == "hard")
train = ~held
def fit(Xt, yt, lam):
    mu, sd = Xt.mean(0), Xt.std(0) + 1e-6; Z = torch.tensor((Xt - mu) / sd, dtype=torch.float64); yy = torch.tensor(yt)
    w = torch.zeros(Xt.shape[1], dtype=torch.float64, requires_grad=True); b = torch.zeros(1, dtype=torch.float64, requires_grad=True)
    pw = torch.tensor((1 - yt).sum() / yt.sum()); opt = torch.optim.LBFGS([w, b], max_iter=500)
    def cl():
        opt.zero_grad(); l = torch.nn.functional.binary_cross_entropy_with_logits(Z @ w + b, yy, pos_weight=pw) + lam * (w ** 2).sum(); l.backward(); return l
    opt.step(cl)
    return mu, sd, w.detach().numpy(), float(b)
def apply(p, Z): mu, sd, w, b = p; return ((Z - mu) / sd) @ w + b
# cross-validated scores on the training part
ti = np.where(train)[0]; folds = rng.integers(0, 5, len(ti)); cv = np.zeros(len(ti))
for k in range(5):
    p = fit(X[ti[folds != k]], y[ti[folds != k]], LAM); cv[folds == k] = apply(p, X[ti[folds == k]])
final = fit(X[train], y[train], LAM); ho = apply(final, X[held])
if "--all" in sys.argv: final = fit(X, y, LAM)   # shipped layer: every labelled passage
# calibration: Platt on CV scores, then shift so that P>=0.5 gives TARGET_FA false alarms on human CV passages
zt = torch.tensor(cv); yt = torch.tensor(y[ti]); a = torch.ones(1, dtype=torch.float64, requires_grad=True); c = torch.zeros(1, dtype=torch.float64, requires_grad=True)
opt = torch.optim.LBFGS([a, c], max_iter=200)
def cl():
    opt.zero_grad(); l = torch.nn.functional.binary_cross_entropy_with_logits(a * zt + c, yt); l.backward(); return l
opt.step(cl); a, c = float(a), float(c)
# domain-balanced: every kind of human writing counts equally, so no genre gets a high false-alarm rate
hz = cv[y[ti] == 0]; hd = D[ti][y[ti] == 0]; dom = {g: np.sum(hd == g) for g in set(hd)}
wts = np.array([1.0 / dom[g] for g in hd]); o = np.argsort(hz); cw = np.cumsum(wts[o]) / wts.sum()
thr = hz[o][np.searchsorted(cw, 1 - TARGET_FA)]; c = -a * thr      # P = 0.5 exactly at the threshold
prob = lambda z: 1 / (1 + np.exp(-(a * z + c)))
def rep(title, z, m, d):
    print(title); h = m == "human"; p = prob(z)
    for g in sorted(set(m[~h])): v = p[m == g]; print(f"   AI  {g:22s} n={len(v):4d} caught={np.mean(v >= 0.5):.2f}  possible+={np.mean(v >= 0.2):.2f}")
    for g in sorted(set(d[h])): v = p[(d == g) & h]; print(f"   HUM {g:22s} n={len(v):4d} false-alarm={np.mean(v >= 0.5):.3f}  shown-as-possible={np.mean((v >= 0.2) & (v < 0.5)):.2f}")
print(f"lam={LAM} calib a={a:.3f} c={c:.3f}")
rep("CROSS-VALIDATED (training sources)", cv, M[ti], D[ti])
rep("HELD OUT (never trained on)", ho, M[held], D[held])
if "--export" in sys.argv:
    mu, sd, w, b = final
    json.dump({"version": 3, "features": "fakespot_logit,fakespot_pool768,desklib_logit,desklib_pool1024",
               "mu": [round(float(x), 6) for x in mu], "sd": [round(float(x), 6) for x in sd], "w": [round(float(x), 7) for x in w], "b": round(b, 6),
               "calibration": {"a": round(a, 5), "b": round(c, 5)}, "target_false_alarm": TARGET_FA, "lambda": LAM},
              open("layer_v3.json", "w"))
    print("exported layer_v3.json")
