"""Per passage: each detector's AI logit + mean-pooled final hidden state. Saves feats_<model>_<tag>.npz"""
import json, sys, glob, re, time, numpy as np, torch, torch.nn as nn
from transformers import AutoTokenizer, AutoModel, AutoConfig, AutoModelForSequenceClassification
from safetensors.torch import load_file
name, tag, files = sys.argv[1], sys.argv[2], sys.argv[3:]
p = glob.glob(f"hf/models--{name.replace('/', '--')}/snapshots/*")[0]
dev = "mps"; tok = AutoTokenizer.from_pretrained(p)
def clean(t): return re.sub(" +", " ", t.replace("￼", " ").replace("\r", " ").replace("\n", " ").replace("\t", " ")).strip()
if "desklib" in name:
    cfg = AutoConfig.from_pretrained(p); enc_m = AutoModel.from_config(cfg); head = nn.Linear(cfg.hidden_size, 1)
    sd = load_file(f"{p}/model.safetensors")
    enc_m.load_state_dict({k[6:]: v for k, v in sd.items() if k.startswith("model.")}); head.load_state_dict({"weight": sd["classifier.weight"], "bias": sd["classifier.bias"]})
    def run(ids, am):
        h = enc_m(input_ids=ids, attention_mask=am)[0]; m = am.unsqueeze(-1).float(); pooled = (h * m).sum(1) / m.sum(1)
        return head(pooled)[0, 0].item(), pooled[0]
    enc_m = enc_m.to(dev).eval(); head = head.to(dev)
else:
    hf = AutoModelForSequenceClassification.from_pretrained(p).to(dev).eval()
    def run(ids, am):
        o = hf(input_ids=ids, attention_mask=am, output_hidden_states=True); lg = o.logits[0].float()
        h = o.hidden_states[-1]; m = am.unsqueeze(-1).float(); pooled = (h * m).sum(1) / m.sum(1)
        return (lg[1] - lg[0]).item(), pooled[0]
data = [d for f in files for d in json.load(open(f))]
Z, F = [], []; t0 = time.time()
for d in data:
    e = tok(clean(" ".join(d["text"].split()[:300])), return_tensors="pt", truncation=True, max_length=512).to(dev)
    with torch.no_grad(): z, f = run(e["input_ids"], e["attention_mask"])
    Z.append(z); F.append(f.float().cpu().numpy())
np.savez(f"feats_{name.split('/')[0].split('-')[0].lower()}_{tag}.npz", z=np.array(Z), f=np.array(F),
         model=np.array([d["model"] for d in data]), domain=np.array([d["domain"] for d in data]))
print(name, tag, len(data), f"{time.time()-t0:.0f}s", file=sys.stderr)
