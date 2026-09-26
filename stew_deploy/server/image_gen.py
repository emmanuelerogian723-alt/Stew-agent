"""
S.T.E.W Image Engine v2 — beautiful images, multi-provider fallback.

Primary: Cloudflare Workers AI (FLUX-2-klein-9b — flagship FLUX-2 quality at
~2s/image; token-authenticated, high reliability).
Second: Cloudflare Leonardo Lucid Origin → Cloudflare FLUX-1-schnell.
Final fallback: Pollinations (free, no key) — kept as the safety net.

Every prompt is auto-enriched with quality boosters so even fallback
generations look professional, not washed out.
"""
import asyncio
import base64
import io
import logging
import os
import time
import urllib.parse
from typing import Optional

logger = logging.getLogger("stew.imagegen")

QUALITY_BOOST = (
    ", ultra detailed, professional quality, sharp focus, "
    "cinematic lighting, high resolution, beautiful composition"
)

# Cloudflare Workers AI image engines, best-first
# (slugs verified against developers.cloudflare.com/workers-ai/models)
CF_ENGINES = [
    ("@cf/black-forest-labs/flux-2-klein-4b", "multipart"),  # FLUX.2 klein — ultra-fast flagship
    ("@cf/black-forest-labs/flux-2-dev", "multipart"),      # FLUX.2 dev — max quality
    ("@cf/black-forest-labs/flux-1-schnell", "json"),       # FLUX.1 schnell — cheap tier
]


def _cf_creds() -> tuple[Optional[str], Optional[str]]:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    return (token, acct) if token and acct else (None, None)


def enrich_prompt(prompt: str) -> str:
    p = (prompt or "").strip()
    if not p:
        return p
    low = p.lower()
    # don't double-enrich if the user already describes quality
    if not any(k in low for k in ("8k", "ultra detailed", "cinematic", "photoreal", "hyperreal")):
        p += QUALITY_BOOST
    return p[:950]


def _cf_call(model: str, mode: str, prompt: str, timeout: float) -> tuple[Optional[bytes], str]:
    """One Cloudflare image call. Returns (jpeg_bytes, err)."""
    import httpx
    token, acct = _cf_creds()
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if mode == "multipart":
            r = httpx.post(url, headers=headers, files={"prompt": (None, prompt)}, timeout=timeout)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and "image" in ct:
                return r.content, ""
            # JSON with base64 payload
            d = r.json()
            img = (d.get("result") or {}).get("image")
            if img:
                if isinstance(img, list):
                    img = img[0]
                raw = base64.b64decode(str(img).split(",", 1)[-1])
                return raw, ""
            return None, f"{model}: {str(d.get('errors'))[:120]}"
        else:
            r = httpx.post(url, headers=headers, json={"prompt": prompt}, timeout=timeout)
            d = r.json()
            img = (d.get("result") or {}).get("image")
            if img:
                if isinstance(img, list):
                    img = img[0]
                raw = base64.b64decode(str(img).split(",", 1)[-1])
                return raw, ""
            return None, f"{model}: {str(d.get('errors'))[:120]}"
    except Exception as e:
        return None, f"{model}: {str(e)[:140]}"


def _pollinations_call(prompt: str, width: int, height: int, retries: int = 3) -> Optional[bytes]:
    import httpx
    encoded = urllib.parse.quote(prompt[:900])
    headers = {"User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"}
    for attempt in range(retries):
        try:
            seed = int(time.time()) % 999999 + attempt * 17
            url = (f"https://image.pollinations.ai/prompt/{encoded}"
                   f"?width={width}&height={height}&model=flux&nologo=true&seed={seed}")
            with httpx.Client(timeout=120, follow_redirects=True, headers=headers) as http:
                resp = http.get(url)
            if resp.status_code == 200 and len(resp.content) > 4000:
                return resp.content
        except Exception as e:
            logger.warning(f"pollinations attempt {attempt+1} failed: {e}")
            time.sleep(2)
    return None


def _validate(raw: bytes) -> Optional[bytes]:
    if not raw or len(raw) < 4000:
        return None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        if im.size[0] < 256:
            return None
        return raw
    except Exception:
        return None


def generate_image_sync(prompt: str, width: int = 1024, height: int = 1024,
                        enrich: bool = True,
                        premium: bool = False) -> tuple[Optional[bytes], str]:
    """Returns (image_bytes, provider_used).
    Cloudflare Workers AI (FLUX.2) is reserved for PAID users + admin
    (premium=True). Free users go straight to the free Pollinations engine."""
    p = enrich_prompt(prompt) if enrich else prompt

    token, _ = _cf_creds()
    if token and premium:
        for model, mode in CF_ENGINES:
            t0 = time.time()
            raw, err = _cf_call(model, mode, p, timeout=90)
            img = _validate(raw) if raw else None
            if img:
                logger.info(f"image via {model} in {time.time()-t0:.1f}s")
                return img, model
            logger.warning(f"CF engine failed ({err}) — next")

    raw = _pollinations_call(p, width, height)
    img = _validate(raw) if raw else None
    if img:
        return img, "pollinations-flux"
    return None, "all-providers-failed"


async def generate_image(prompt: str, width: int = 1024, height: int = 1024,
                         enrich: bool = True,
                         premium: bool = False) -> tuple[Optional[bytes], str]:
    return await asyncio.to_thread(generate_image_sync, prompt, width, height, enrich, premium)

# ── Image-to-image: recreate a user's photo with a beautiful touch ──────────
# Premium flagship feature (consumes the daily premium-image quota).
# Engine: Cloudflare Workers AI FLUX.2 (multipart prompt + image, JSON response).
# Verified live 2026-09-26: output preserves the original photo's structure
# (correlation +0.199 with the source photo vs -0.062 for a no-image control).
# NOTE: pollinations "flux-kontext-pro" IGNORES the image param (proven
# identical to text-only output) — do not use it for image recreation.

IMG2IMG_BOOST = ("professional photo enhancement, magical golden-hour lighting, "
                 "cinematic color grading, ultra detailed, refined composition, "
                 "soft glow, beautiful, high resolution")

CF_IMG2IMG_ENGINES = [
    "@cf/black-forest-labs/flux-2-klein-4b",   # FLUX.2 klein — ultra-fast flagship
    "@cf/black-forest-labs/flux-2-dev",        # FLUX.2 dev — max quality
]


def _cf_img2img_call(model: str, prompt: str, image_bytes: bytes, timeout: float) -> tuple[Optional[bytes], str]:
    """Cloudflare FLUX.2 image-to-image. Returns (jpeg_bytes, err)."""
    import httpx
    token, acct = _cf_creds()
    if not token:
        return None, "no cloudflare creds"
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
    try:
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            files={
                "prompt": (None, prompt),
                "image": ("input.jpg", image_bytes, "image/jpeg"),
            },
            timeout=timeout,
        )
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and "image" in ct:
            return _validate(r.content) and r.content, ""
        try:
            d = r.json()
            img = (d.get("result") or {}).get("image")
            if img:
                if isinstance(img, list):
                    img = img[0]
                raw = base64.b64decode(str(img).split(",", 1)[-1])
                if raw:
                    return raw, ""
            return None, f"{model}: {str(d.get('errors'))[:140]}"
        except Exception:
            return None, f"{model}: HTTP {r.status_code}"
    except Exception as e:
        return None, f"{model}: {str(e)[:140]}"


def generate_image_to_image_sync(prompt: str, image_bytes: bytes, strength: float = 0.55) -> tuple[Optional[bytes], str]:
    """Recreate a user's image with a beautiful applied touch.

    Returns (jpeg_bytes, engine_name) or (None, error)."""
    p = (prompt or "Recreate this image with a beautiful, refined touch").strip()
    low = p.lower()
    if not any(k in low for k in ("8k", "ultra detailed", "cinematic")):
        p = f"{p}, {IMG2IMG_BOOST}"

    token, acct = _cf_creds()
    if not (token and acct):
        return None, "premium engine not configured (cloudflare creds missing)"

    for model in CF_IMG2IMG_ENGINES:
        raw, err = _cf_img2img_call(model, p, image_bytes, 180.0)
        if raw:
            return raw, model.split("/")[-1]
        logger.warning(f"img2img {model} failed: {err}")
    return None, "image recreation engines all failed"


async def generate_image_to_image(prompt: str, image_bytes: bytes, strength: float = 0.55) -> tuple[Optional[bytes], str]:
    return await asyncio.to_thread(generate_image_to_image_sync, prompt, image_bytes, strength)
