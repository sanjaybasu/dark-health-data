"""Modal GPU vision attribution for the figure-extraction hybrid (zero Claude spend).

The hybrid: exact data-label values come from the PDF vector layer (free, local); a vision
model only ASSIGNS each value to its series/category/year. This app runs that attribution step
on a GPU with an open model (Qwen2.5-VL), so it is fast and costs Modal credits, not Claude.

Decoupled by design: input items (per page: key, exact labels, base64 page image) are prepared
locally and passed in as a JSON file; this app only loads the model once and runs inference;
results are written back as JSON for local scoring. Deploys a NEW app 'dhd-vision-hybrid'.

Usage:
  python prepare items -> /tmp/vision_items.json   (done locally, see the driver snippet)
  modal run scripts/modal_vision_hybrid.py --items /tmp/vision_items.json --out /tmp/vision_attr.json
"""
from __future__ import annotations

import modal

app = modal.App("dhd-vision-hybrid")
MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
image = (
    modal.Image.debian_slim()
    .pip_install("torch", "torchvision", "transformers>=4.49.0", "accelerate",
                 "qwen-vl-utils", "pillow")
)
hf_cache = modal.Volume.from_name("hf-cache", create_if_missing=True)

PROMPT = (
    "This image is a figure (chart) from a health report. The exact numeric values printed on "
    "it are: {labels}. For each plotted value, identify its series/legend label, its category "
    "or cohort, and its year if shown on an axis. Use ONLY the exact values listed above; do "
    'not invent numbers. Respond as JSON only: {{"records":[{{"series":"","category":"","year":null,"value":null}}]}}'
)


@app.cls(image=image, gpu="L40S", volumes={"/cache": hf_cache}, timeout=3600)
class Model:
    @modal.enter()
    def load(self):
        import os
        os.environ["HF_HOME"] = "/cache/hf"
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration
        self.model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            MODEL, torch_dtype="auto", device_map="auto")
        self.proc = AutoProcessor.from_pretrained(MODEL)

    @modal.method()
    def attribute(self, items: list[dict]) -> list[dict]:
        import json
        from qwen_vl_utils import process_vision_info
        out = []
        for it in items:
            msgs = [{"role": "user", "content": [
                {"type": "image", "image": "data:image/png;base64," + it["image_b64"]},
                {"type": "text", "text": PROMPT.format(labels=it["labels"][:60])}]}]
            try:
                text = self.proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
                imgs, vids = process_vision_info(msgs)
                inp = self.proc(text=[text], images=imgs, padding=True, return_tensors="pt").to("cuda")
                gen = self.model.generate(**inp, max_new_tokens=1024, do_sample=False)
                trimmed = gen[0][inp.input_ids.shape[1]:]
                raw = self.proc.decode(trimmed, skip_special_tokens=True).strip()
                if raw.startswith("```"):  # strip markdown fence
                    raw = raw.strip("`")
                    raw = raw[4:] if raw.lower().startswith("json") else raw
                start = min([i for i in (raw.find("{"), raw.find("[")) if i >= 0], default=-1)
                obj = json.JSONDecoder().raw_decode(raw[start:])[0] if start >= 0 else {}
                recs = obj.get("records", []) if isinstance(obj, dict) else obj
                recs = recs if isinstance(recs, list) else []
            except Exception as exc:  # noqa: BLE001
                recs = []
                print(f"  ! {it['key']}: {exc}")
            out.append({"key": it["key"], "records": recs})
        return out


@app.local_entrypoint()
def run(items: str = "/tmp/vision_items.json", out: str = "/tmp/vision_attr.json"):
    import json
    data = json.load(open(items))
    print(f"attributing {len(data)} page(s) on GPU ({MODEL})")
    results = Model().attribute.remote(data)
    json.dump(results, open(out, "w"))
    print(f"wrote {len(results)} -> {out}")
