# Training the AI-detection layer

HumanWrite's detector runs two open classifiers on each passage and combines them with a small
logistic layer (`app/services/detection/layer_v3.json`, about 57 KB). These scripts rebuild that layer.

The two classifiers are not retrained. Only the combining layer is.

## Steps

1. **Download the two models** into `hf/` (a Hugging Face cache directory):
   - `fakespot-ai/roberta-base-ai-text-detection-v1`
   - `desklib/ai-text-detector-v1.01`
2. **Collect labelled passages** as JSON lists of `{"model": "human" | <generator name>, "domain": ..., "text": ...}`.
   Passages should be 60–300 words.
3. **Extract features** for each set:
   ```
   python extract_features.py fakespot-ai/roberta-base-ai-text-detection-v1 <tag> <files…>
   python extract_features.py desklib/ai-text-detector-v1.01 <tag> <files…>
   ```
   Each feature row is the model's AI logit plus its mean-pooled final hidden state, the same as the app computes.
4. **Train and evaluate:**
   ```
   python train_layer.py <tag1,tag2,…> 0.03
   ```
   This prints cross-validated results and results on held-out sets.
5. **Export the layer:** run the same command with `--all --export`, then copy the resulting `layer_v3.json`
   into `app/services/detection/`.

## Data used for v3

| Set | Label | Source |
|---|---|---|
| Ghostbuster essays | human + ChatGPT/Claude variants | Ghostbuster benchmark |
| MAGE stories, news, Q&A | human | MAGE benchmark |
| HC3 (wiki_csai, finance, medicine, open_qa) | human + ChatGPT | `Hello-SimpleAI/HC3`; tokenizer spacing normalised so spacing can't be used as a cue |
| Stack Exchange answers, before 2021 (dba, cs, softwareengineering, money, economics, networkengineering, superuser) | human | Stack Exchange API, CC BY-SA |
| arXiv abstracts, before 2021 (cs, econ, q-fin, stat, …) | human | `gfissore/arxiv-abstracts-2021` |
| Technical exam answers | AI | qwen3:4b, generated locally (`generate_ai_technical.py`) |
| Technical answers and essays | AI | Claude, written for this purpose |
| Reworded AI essays; AI essays prompted to sound human | AI | qwen3:4b |

## Why the human technical data matters

Without human technical writing on the human side, a layer learns "terse technical style = AI" and wrongly flags
students who write in notes. With it in training, human Stack Exchange answers and arXiv abstracts are wrongly
flagged only about 0–5% of the time.

Calibration sets the 50% point so that each kind of human writing has about 3% false alarms. Each genre is
weighted equally, so no one genre can dominate the calibration.

## Known limits

- **Unfamiliar AI models.** Generators not represented in training are caught less reliably. Adding samples from
  more AI models, especially in technical and note styles, is the most useful contribution.
- **Short text.** Passages under 60 words are not scored.
- **Proof.** No detector can prove authorship.
