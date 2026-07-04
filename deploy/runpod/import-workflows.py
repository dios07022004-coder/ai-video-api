#!/usr/bin/env python3
"""Bulk-import ComfyUI API-format workflows into AI Video API config.

Turns a folder of ComfyUI API-format workflow JSONs (e.g. the VirtuaVixen I2V
pack) into ready-to-serve API config — one mode per workflow, faithful to the
original graph.

By DEFAULT only what genuinely must vary per user is parameterized:

    {{IMAGE}}   → the LoadImage feeding the video (traced from start_image)  [always]
    {{SEED}}    → every sampler's seed / noise_seed                          [unless --keep-seed]

The author's positive/negative PROMPTS are kept EXACTLY as written — they are
tuned to each action's LoRA, so replacing them would hurt quality. Model
filenames also stay hardcoded (as the author built it), so nothing is mis-bound.

Opt-in flags:
    --keep-seed            leave the seed hardcoded too (identical output per image)
    --parameterize-prompt  expose {{PROMPT}}/{{NEGATIVE}} so the site controls text
                           (author's text is then dropped from the graph)

Outputs under --out:
    workflows/<name>.json     parameterized graph (drop into config/workflows/)
    modes/<name>.json         mode definition   (drop into config/modes/)
    models.json               merged model registry
    download-manifest.txt     every model filename needed by all workflows
    import-report.txt         per-workflow result + any nodes needing review

Usage (on the pod):
    python3 import-workflows.py \
        --src /workspace/ComfyUI/user/default/workflows/VirtuaVixen/I2V \
        --out /workspace/api-config \
        --category i2v --price 20
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# class_type → (input key holding the model filename, model type)
LOADER_KEYS: dict[str, tuple[str, str]] = {
    "CheckpointLoaderSimple": ("ckpt_name", "checkpoint"),
    "UNETLoader": ("unet_name", "unet"),
    "VAELoader": ("vae_name", "vae"),
    "CLIPLoader": ("clip_name", "clip"),
    "LoraLoader": ("lora_name", "lora"),
    "LoraLoaderModelOnly": ("lora_name", "lora"),
    "CLIPVisionLoader": ("clip_name", "clip_vision"),
    "ControlNetLoader": ("control_net_name", "controlnet"),
    "UpscaleModelLoader": ("model_name", "upscale"),
    "RIFE VFI": ("ckpt_name", "vfi"),
}

SEED_KEYS = ("seed", "noise_seed")


def _iter_refs(inputs: dict):
    for v in inputs.values():
        if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
            yield v[0]


def _find_source(graph: dict, node_id: str, want_class: str, depth: int = 0, seen=None) -> str | None:
    """Walk input links back from node_id to the first node of want_class."""
    seen = seen or set()
    if node_id in seen or depth > 40:
        return None
    seen.add(node_id)
    node = graph.get(node_id)
    if node is None:
        return None
    if node.get("class_type") == want_class:
        return node_id
    for ref in _iter_refs(node.get("inputs", {})):
        found = _find_source(graph, ref, want_class, depth + 1, seen)
        if found:
            return found
    return None


def _find_prompt_sink(graph: dict) -> tuple[str | None, str | None, str | None]:
    """Return (positive_encoder_id, negative_encoder_id, image_loader_id)."""
    pos = neg = img = None
    # 1) a node carrying positive+negative (WanImageToVideo / KSampler*)
    for nid, node in graph.items():
        inp = node.get("inputs", {})
        if "positive" in inp and "negative" in inp:
            p, n = inp["positive"], inp["negative"]
            if isinstance(p, list):
                pos = _find_source(graph, p[0], "CLIPTextEncode")
            if isinstance(n, list):
                neg = _find_source(graph, n[0], "CLIPTextEncode")
        if "start_image" in inp and isinstance(inp["start_image"], list):
            img = _find_source(graph, inp["start_image"][0], "LoadImage")
        if pos and neg and img:
            break
    # 2) fallbacks by title
    if pos is None or neg is None:
        for nid, node in graph.items():
            if node.get("class_type") != "CLIPTextEncode":
                continue
            title = node.get("_meta", {}).get("title", "").lower()
            if pos is None and "posi" in title:
                pos = nid
            elif neg is None and "nega" in title:
                neg = nid
    # 3) image fallback: any LoadImage
    if img is None:
        for nid, node in graph.items():
            if node.get("class_type") == "LoadImage":
                img = nid
                break
    return pos, neg, img


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _model_id(filename: str) -> str:
    return _slug(Path(filename).stem)


def _humanize(stem: str) -> str:
    return re.sub(r"[_\-]+", " ", stem).strip().title()


def import_one(
    path: Path,
    category: str,
    price: int,
    warnings: list[str],
    *,
    keep_seed: bool = False,
    parameterize_prompt: bool = False,
) -> tuple[dict, dict, dict]:
    graph = json.loads(path.read_text(encoding="utf-8"))
    name = path.stem
    slug = _slug(name)

    pos, neg, img = _find_prompt_sink(graph)

    # IMAGE is the only always-required parameter (each user's own photo).
    if img:
        graph[img]["inputs"]["image"] = "{{IMAGE}}"
    else:
        warnings.append(f"{name}: no LoadImage found — IMAGE not parameterized")

    # Prompts stay verbatim unless the caller opts into site-controlled prompts.
    if parameterize_prompt:
        if pos:
            graph[pos]["inputs"]["text"] = "{{PROMPT}}"
        else:
            warnings.append(f"{name}: positive prompt node not found")
        if neg:
            graph[neg]["inputs"]["text"] = "{{NEGATIVE}}"
        else:
            warnings.append(f"{name}: negative prompt node not found")

    # SEED → random per request by default; --keep-seed leaves the author's value.
    if not keep_seed:
        seeds = 0
        for node in graph.values():
            for k in SEED_KEYS:
                if k in node.get("inputs", {}):
                    node["inputs"][k] = "{{SEED}}"
                    seeds += 1
        if seeds == 0:
            warnings.append(f"{name}: no seed field found")

    # models referenced
    models: dict[str, dict] = {}
    primary_ckpt: str | None = None
    for node in graph.values():
        ct = node.get("class_type")
        if ct in LOADER_KEYS:
            key, mtype = LOADER_KEYS[ct]
            fname = node.get("inputs", {}).get(key)
            if isinstance(fname, str) and fname:
                mid = _model_id(fname)
                models[mid] = {
                    "id": mid,
                    "name": Path(fname).stem,
                    "type": mtype if mtype != "vfi" else "upscale",
                    "path": fname,
                    "enabled": True,
                }
                if mtype == "checkpoint" and primary_ckpt is None:
                    primary_ckpt = mid

    params: dict = {}
    if not keep_seed:
        params["SEED"] = {"type": "seed", "default": -1, "overridable": True}

    mode = {
        "id": slug,
        "name": _humanize(name),
        "description": f"{_humanize(name)} (WAN 2.2 I2V). Prompt kept as authored; only the image varies.",
        "category": category,
        "task_type": "video",
        "enabled": True,
        "workflow": slug,
        "model": primary_ckpt or (next(iter(models), "")),
        "model_bindings": {},
        "control_video": None,
        "prompt_template": "{prompt}",
        "negative_prompt": "",
        "price_credits": price,
        "preview": f"{slug}.jpg",
        "params": params,
    }
    return {"slug": slug, "graph": graph}, mode, models


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="folder of ComfyUI API-format workflow JSONs")
    ap.add_argument("--out", required=True, help="output config dir")
    ap.add_argument("--category", default="i2v")
    ap.add_argument("--price", type=int, default=20)
    ap.add_argument("--keep-seed", action="store_true", help="leave seeds hardcoded (identical output per image)")
    ap.add_argument("--parameterize-prompt", action="store_true", help="expose {{PROMPT}}/{{NEGATIVE}} (drops author text)")
    a = ap.parse_args()

    src = Path(a.src)
    out = Path(a.out)
    (out / "workflows").mkdir(parents=True, exist_ok=True)
    (out / "modes").mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in src.glob("*.json") if p.name not in ("_index.json", "vv-manifest.json"))
    if not files:
        print(f"No workflow JSONs in {src}")
        return 1

    all_models: dict[str, dict] = {}
    warnings: list[str] = []
    ok = 0
    for path in files:
        try:
            wf, mode, models = import_one(
                path, a.category, a.price, warnings,
                keep_seed=a.keep_seed, parameterize_prompt=a.parameterize_prompt,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{path.name}: FAILED to import — {exc}")
            continue
        (out / "workflows" / f"{wf['slug']}.json").write_text(
            json.dumps(wf["graph"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out / "modes" / f"{mode['id']}.json").write_text(
            json.dumps(mode, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        all_models.update(models)
        ok += 1

    (out / "models.json").write_text(
        json.dumps({"models": list(all_models.values())}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = sorted({m["path"] for m in all_models.values()})
    (out / "download-manifest.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    (out / "import-report.txt").write_text(
        f"imported {ok}/{len(files)} workflows\n"
        f"models: {len(all_models)}\n\n"
        + ("WARNINGS:\n" + "\n".join(warnings) if warnings else "no warnings"),
        encoding="utf-8",
    )

    print(f"Imported {ok}/{len(files)} workflows → {out}")
    print(f"Models needed: {len(all_models)} (see download-manifest.txt)")
    if warnings:
        print(f"{len(warnings)} warnings — see import-report.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
