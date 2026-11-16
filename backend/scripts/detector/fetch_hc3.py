"""Training data for the genre layer: MAGE scientific writing + HC3 (human vs ChatGPT) technical answers."""
import httpx, json, time, re, random
random.seed(11)
B = "https://datasets-server.huggingface.co"
c = httpx.Client(timeout=120)
def filt(where, n, off=0):
    for _ in range(10):
        d = c.get(f"{B}/filter", params={"dataset": "yaful/MAGE", "config": "default", "split": "train", "where": where, "offset": off, "length": n}).json()
        if "rows" in d: return d
        print("wait", d.get("error")); time.sleep(20)
    return {"rows": []}
out = []
for where, lab in [("\"src\" = 'sci_gen_human'", "human")]:
    d = filt(where, 100); print("total", d.get("num_rows_total"))
    for off in (0, 300, 600, 900):
        for r in filt(where, 100, off)["rows"]:
            out.append({"model": "human", "domain": "mage-sci", "text": r["row"]["text"]})
d = filt("\"src\" LIKE 'sci_gen_machine%'", 100); tot = d.get("num_rows_total", 0); print("machine total", tot)
for off in random.sample(range(0, max(1, tot - 100), 100), min(5, max(1, tot // 100))):
    for r in filt("\"src\" LIKE 'sci_gen_machine%'", 100, off)["rows"]:
        out.append({"model": "mage-" + r["row"]["src"].replace("sci_gen_machine_", "")[:30], "domain": "mage-sci", "text": r["row"]["text"]})
for cfg in ("wiki_csai", "finance", "open_qa", "medicine"):
    for off in (0, 200, 400):
        d = c.get(f"{B}/rows", params={"dataset": "Hello-SimpleAI/HC3", "config": cfg, "split": "train", "offset": off, "length": 100}).json()
        for r in d.get("rows", []):
            x = r["row"]
            for h in x["human_answers"][:1]: out.append({"model": "human", "domain": "hc3-" + cfg, "text": h})
            for a in x["chatgpt_answers"][:1]: out.append({"model": "hc3-chatgpt", "domain": "hc3-" + cfg, "text": a})
    print(cfg, len(out))
out = [d for d in out if len(d["text"].split()) >= 60]
for d in out: d["text"] = " ".join(d["text"].split()[:300])
json.dump(out, open("train_pool.json", "w"))
import collections; print(len(out), collections.Counter((d["model"] == "human", d["domain"]) for d in out))
