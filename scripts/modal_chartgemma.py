"""ChartGemma figure extraction on Modal GPU (zero Claude) -- completeness test.

The hybrid's weakness was completeness: on multi-series figures it captured one series and
missed others. ChartGemma is trained to emit the full chart contents, so this tests whether a
chart-specialised model recovers the whole figure. Runs the open ChartGemma (ahmed-masry/
chartgemma) on the already-prepared page images; returns raw model text per page for local
scoring against the exact vector-text labels (an objective completeness signal: what fraction
of the printed values the model recovers). Deploys a NEW app 'dhd-chartgemma'.

Usage:
  modal run scripts/modal_chartgemma.py --items /tmp/vision_items.json --out /tmp/chartgemma_out.json
"""
from __future__ import annotations

import modal

app = modal.App("dhd-chartgemma")
MODEL = "ahmed-masry/chartgemma"
image = (
    modal.Image.debian_slim()
    .pip_install("torch", "transformers>=4.44.0", "accelerate", "sentencepiece",
                 "protobuf", "pillow")
)
hf_cache = modal.Volume.from_name("hf-cache", create_if_missing=True)
PROMPT = ("Extract the underlying data of this chart. List every data point as a line "
          "'series | category | value', including every series and every category shown.")


@app.cls(image=image, gpu="L40S", volumes={"/cache": hf_cache}, timeout=3600)
class Model:
    @modal.enter()
    def load(self):
        import os
        os.environ["HF_HOME"] = "/cache/hf"
        import torch
        from transformers import AutoProcessor, PaliGemmaForConditionalGeneration
        self.model = PaliGemmaForConditionalGeneration.from_pretrained(
            MODEL, torch_dtype=torch.float16).to("cuda")
        self.proc = AutoProcessor.from_pretrained(MODEL)

    @modal.method()
    def generate(self, items: list[dict]) -> list[dict]:
        import base64
        import io
        from PIL import Image
        out = []
        for it in items:
            try:
                img = Image.open(io.BytesIO(base64.b64decode(it["image_b64"]))).convert("RGB")
                inp = self.proc(text=PROMPT, images=img, return_tensors="pt")
                plen = inp["input_ids"].shape[1]
                inp = {k: v.to("cuda") for k, v in inp.items()}
                gen = self.model.generate(**inp, num_beams=1, max_new_tokens=640)
                txt = self.proc.batch_decode(gen[:, plen:], skip_special_tokens=True)[0]
            except Exception as exc:  # noqa: BLE001
                txt = ""
                print(f"  ! {it['key']}: {exc}")
            out.append({"key": it["key"], "text": txt})
        return out


@app.local_entrypoint()
def run(items: str = "/tmp/vision_items.json", out: str = "/tmp/chartgemma_out.json"):
    import json
    data = json.load(open(items))
    print(f"running ChartGemma on {len(data)} page(s)")
    res = Model().generate.remote(data)
    json.dump(res, open(out, "w"))
    print(f"wrote {len(res)} -> {out}")
