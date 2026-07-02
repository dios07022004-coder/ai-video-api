#!/usr/bin/env python3
"""Direct I2V smoke test against ComfyUI on the pod — no external API needed.

Loads the pack's french_kiss workflow (already ComfyUI API format), overrides the
input image / prompt / seed, submits to ComfyUI /prompt, waits, and prints the
output file. This is exactly the path our production API uses (POST /prompt +
poll /history), so a success here means the whole GPU stack is proven.

Run on the pod:
    # put a portrait into ComfyUI/input/ first (JupyterLab drag-drop), then:
    python3 /workspace/test-i2v.py --image myphoto.png --prompt "a gentle kiss" --seed 123
    # or use a bundled pose image that's already in input/:
    python3 /workspace/test-i2v.py --image vv_pose_standing.png
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request

COMFY = "http://127.0.0.1:8188"
DEFAULT_WF = "/workspace/ComfyUI/user/default/workflows/VirtuaVixen/I2V/french_kiss.json"


def _post(path: str, data: dict) -> dict:
    req = urllib.request.Request(
        COMFY + path, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _get(path: str) -> dict:
    with urllib.request.urlopen(COMFY + path, timeout=30) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="filename already present in ComfyUI/input/")
    ap.add_argument("--prompt", default="", help="override positive prompt (optional)")
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--wf", default=DEFAULT_WF)
    a = ap.parse_args()

    with open(a.wf, encoding="utf-8") as fh:
        graph = json.load(fh)

    # Override image (LoadImage), prompt (positive CLIPTextEncode), seed (both samplers).
    for node in graph.values():
        ct = node.get("class_type")
        title = node.get("_meta", {}).get("title", "")
        if ct == "LoadImage":
            node["inputs"]["image"] = a.image
        elif ct == "CLIPTextEncode" and "Positive" in title and a.prompt:
            node["inputs"]["text"] = a.prompt
        elif ct == "KSamplerAdvanced":
            node["inputs"]["noise_seed"] = a.seed

    print(f"Submitting french_kiss with image={a.image!r} seed={a.seed} ...")
    try:
        resp = _post("/prompt", {"prompt": graph})
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        print("ComfyUI REJECTED the graph:")
        print(e.read().decode()[:3000])
        return 1
    prompt_id = resp.get("prompt_id")
    if not prompt_id:
        print("No prompt_id returned:", resp)
        return 1
    print("Queued prompt_id:", prompt_id)

    t0 = time.time()
    while True:
        hist = _get(f"/history/{prompt_id}")
        if prompt_id in hist:
            entry = hist[prompt_id]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                print("EXECUTION ERROR:")
                print(json.dumps(status.get("messages", []), indent=2)[:3000])
                return 1
            outs = entry.get("outputs", {})
            if outs:
                print(f"\nDONE in {int(time.time()-t0)}s. Outputs:")
                print(json.dumps(outs, indent=2)[:3000])
                print("\nVideo is under /workspace/ComfyUI/output/ (subfolder 'video').")
                return 0
        if time.time() - t0 > 1800:
            print("Timed out after 30 min.")
            return 1
        print(f"  ...running ({int(time.time()-t0)}s)", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
