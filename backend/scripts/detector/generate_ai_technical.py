import json, re, random, httpx
random.seed(5)
TOPICS = ["checkpointing in DBMS", "log-based recovery", "the three-schema architecture", "ER model and its components", "views in SQL", "triggers in databases",
 "NoSQL databases", "the CAP theorem", "data mining techniques", "OLTP vs OLAP", "fragmentation in distributed databases", "query processing steps",
 "the critical section problem", "semaphores", "memory segmentation", "file allocation methods", "disk scheduling algorithms", "the producer–consumer problem",
 "IPv4 vs IPv6", "the TCP three-way handshake", "DNS resolution", "routing algorithms", "network topologies", "firewalls and their types",
 "the time value of money", "capital budgeting techniques", "ratio analysis", "the capital asset pricing model", "types of bank accounts", "mutual funds",
 "monetary policy tools", "fiscal deficit", "price elasticity of demand", "market structures", "GDP and its measurement", "the balance of payments",
 "Maslow's hierarchy of needs", "leadership styles", "organisational structure types", "the recruitment process", "performance appraisal methods", "Porter's five forces",
 "agile vs waterfall", "software testing levels", "the SDLC", "coupling and cohesion", "UML diagrams", "requirements elicitation techniques"]
STYLES = ["in compact note style with short labelled points (e.g. 'Definition: ...', 'Advantages: ...'), no markdown",
          "in two or three short paragraphs, plain and direct, like a good student's exam answer",
          "as a concise answer that starts with a definition, then explains with an example, no markdown"]
def ask(p):
    r = httpx.post("http://127.0.0.1:11434/api/generate", json={"model": "qwen3:4b", "prompt": p + " /no_think", "stream": False, "think": False,
        "options": {"temperature": 0.9, "num_predict": 2000}}, timeout=400)
    t = r.json()["response"]
    return re.sub(r"[#*`]", "", t.split("</think>")[-1]).strip() if "</think>" in t else ""
out = []
jobs = [(t, s) for t in TOPICS for s in random.sample(STYLES, 2)][:120]
random.shuffle(jobs)
for i, (t, s) in enumerate(jobs):
    a = ask(f"Answer this university exam question in about {random.choice([150, 180, 220, 260])} words, {s}: Explain {t}.")
    if a: out.append({"model": "qwen-tech2", "domain": "tech", "text": re.sub(r"\s*\n\s*", " ", a)})
    print(i, len(a.split()), flush=True)
    if i % 10 == 9: json.dump(out, open("qwen_tech2.json", "w"), indent=1)
json.dump(out, open("qwen_tech2.json", "w"), indent=1)
