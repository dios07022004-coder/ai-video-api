#!/usr/bin/env python3
"""Selective downloader for the WAN 2.2 I2V subset of the VirtuaVixen model pack.

The bundled installer's `--i2v` flag does NOT filter model downloads (it always
pulls the full ~682 GB repo). This script downloads only what the I2V pipeline
needs, by basename, placing each file at its correct ComfyUI models/ path.

Usage (run on the RunPod pod, ComfyUI at /workspace/ComfyUI):

    python3 vv-i2v-select.py --base           # base WAN 2.2 I2V stack (~40 GB)
    python3 vv-i2v-select.py --grep doggy     # list repo files matching a word
    python3 vv-i2v-select.py --get A.safetensors B.safetensors   # specific files
    python3 vv-i2v-select.py --base --get <action-lora-high> <action-lora-low>

Discover the action LoRA you want with --grep, then --get its HIGH and LOW files.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HF_REPO = "VirtuaVixenTube/comfyui-nsfw-model-pack"
MODELS_DIR = Path("/workspace/ComfyUI/models")

# Base WAN 2.2 I2V stack — shared by every I2V workflow. Matched by basename, so
# the repo's own folder layout (checkpoints/…, loras/…, vae/…) is preserved.
BASE_FILES = {
    "Wan2_2-I2V-A14B-HIGH_fp8_e4m3fn_scaled_KJ.safetensors",
    "Wan2_2-I2V-A14B-LOW_fp8_e4m3fn_scaled_KJ.safetensors",
    "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "wan_2.1_vae.safetensors",
    "wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors",
    "wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors",
    "rife47.pth",
    "apple_DFN5B-CLIP-ViT-H-14-384_fp16.safetensors",
}


def _setup_caches() -> None:
    """Pin ALL HF caches + temp onto the network volume, not the tiny container
    disk. Without this, xet reconstruction of large files writes to the container
    overlay and fails with 'Disk quota exceeded (os error 122)'."""
    import os

    base = Path("/workspace/.cache/huggingface")
    tmp = Path("/workspace/tmp")
    for d in (base / "hub", base / "xet", tmp):
        d.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(base)
    os.environ["HF_HUB_CACHE"] = str(base / "hub")
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(base / "hub")
    os.environ["HF_XET_CACHE"] = str(base / "xet")
    os.environ["TMPDIR"] = str(tmp)
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"


def _hub():
    _setup_caches()
    try:
        from huggingface_hub import HfApi, hf_hub_download  # noqa
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "huggingface_hub[hf_transfer]"])
    from huggingface_hub import HfApi, hf_hub_download

    return HfApi(), hf_hub_download


def repo_tree(api):
    files = list(api.list_repo_tree(HF_REPO, recursive=True, repo_type="model"))
    return [f for f in files if getattr(f, "size", 0)]


def human(n: int) -> str:
    return f"{n/1024**3:.2f} GB" if n > 1024**3 else f"{n/1024**2:.0f} MB"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", action="store_true", help="download the base I2V stack")
    p.add_argument("--get", nargs="*", default=[], help="download these exact basenames")
    p.add_argument("--manifest", help="download every basename listed in this file (one per line)")
    p.add_argument("--grep", help="just list repo files whose path contains this substring")
    p.add_argument("--yes", action="store_true", help="skip confirmation")
    args = p.parse_args()

    api, hf_hub_download = _hub()
    files = repo_tree(api)

    if args.grep:
        needle = args.grep.lower()
        hits = [f for f in files if needle in f.rfilename.lower()]
        total = sum(f.size for f in hits)
        for f in sorted(hits, key=lambda x: x.rfilename):
            print(f"  {human(f.size):>10}  {f.rfilename}")
        print(f"\n  {len(hits)} files, {human(total)} total")
        return 0

    wanted: set[str] = set()
    if args.base:
        wanted |= BASE_FILES
    wanted |= set(args.get)
    if args.manifest:
        for line in Path(args.manifest).read_text(encoding="utf-8").splitlines():
            name = Path(line.strip()).name
            if name:
                wanted.add(name)
    if not wanted:
        print("Nothing selected. Use --base, --get <file...>, --manifest <file>, or --grep <word>.")
        return 2

    selected = [f for f in files if Path(f.rfilename).name in wanted]
    found = {Path(f.rfilename).name for f in selected}
    missing = wanted - found
    total = sum(f.size for f in selected)

    print(f"\nRepo: {HF_REPO}")
    print(f"Selected {len(selected)} files, {human(total)} total:")
    for f in sorted(selected, key=lambda x: x.rfilename):
        print(f"  {human(f.size):>10}  {f.rfilename}")
    if missing:
        print("\n  NOT FOUND in repo (check name with --grep):")
        for m in sorted(missing):
            print(f"    {m}")

    if not selected:
        return 1
    if not args.yes:
        ans = input(f"\nDownload {human(total)} to {MODELS_DIR}? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            return 0

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    done = 0
    for i, f in enumerate(sorted(selected, key=lambda x: x.rfilename), 1):
        dst = MODELS_DIR / f.rfilename
        if dst.exists() and dst.stat().st_size == f.size:
            print(f"[{i}/{len(selected)}] skip (present) {f.rfilename}")
            continue
        print(f"[{i}/{len(selected)}] {f.rfilename} ({human(f.size)}) ...", flush=True)
        hf_hub_download(repo_id=HF_REPO, filename=f.rfilename, local_dir=str(MODELS_DIR), repo_type="model")
        done += 1
    print(f"\nDone. Downloaded {done}, total set {human(total)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
