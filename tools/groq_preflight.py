#!/usr/bin/env python3
"""
groq_preflight.py — prove your Groq models exist AND accept images, before deploying.

    export GROQ_API_KEY=gsk_...
    python3 tools/groq_preflight.py                       # checks the defaults
    python3 tools/groq_preflight.py qwen/qwen3.8-27b ...  # or your own candidates

Two checks per model (the second one is the one that actually matters):
  1. is the id in GET /openai/v1/models for *your* account?
  2. does POST /chat/completions accept a content array with image_url?

Exit code 0 = at least one model passed check 2 (it can be the primary for VISION_MODELS).
No jq, no curl: stdlib only, so it runs in Termux.
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

API_BASE = os.environ.get("GROQ_API_BASE", "https://api.groq.com/openai/v1").rstrip("/")
KEY = os.environ.get("GROQ_API_KEY", "").strip()
DEFAULT_CANDIDATES = ["qwen/qwen3.8-27b"]

# 1x1 transparent PNG
TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def request(method, path, payload=None, timeout=45):
    url = API_BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + KEY)
    req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as res:
            body = res.read().decode("utf-8", "replace")
            return res.status, body, (time.time() - started) * 1000
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, body, (time.time() - started) * 1000
    except Exception as exc:  # network/DNS/timeout
        return 0, "network error: %s" % exc, (time.time() - started) * 1000


def main():
    candidates = sys.argv[1:] or DEFAULT_CANDIDATES
    if not KEY:
        print("GROQ_API_KEY is not set.\n  export GROQ_API_KEY=gsk_...   (console.groq.com -> API Keys)")
        return 2

    print("1) GET %s/models" % API_BASE)
    status, body, ms = request("GET", "/models")
    if status != 200:
        print("   FAILED  HTTP %s  %.0fms" % (status or "network", ms))
        print("   " + body[:400].replace("\n", " "))
        return 2

    try:
        listed = [m.get("id") for m in json.loads(body).get("data", [])]
    except Exception:
        print("   could not parse the model list")
        return 2

    print("   %d models visible on this account (%.0fms)" % (len(listed), ms))
    interesting = sorted(i for i in listed if any(k in i.lower() for k in ("qwen", "llama-4", "vision", "muse", "gpt-oss")))
    for i in interesting:
        print("     - %s" % i)

    print("\n2) image_url smoke test (the check docs cannot answer for you)")
    results = []
    for model in candidates:
        present = model in listed
        status, body, ms = request(
            "POST",
            "/chat/completions",
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Reply with the single word: ok"},
                            {"type": "image_url", "image_url": {"url": TINY_PNG}},
                        ],
                    }
                ],
                "max_tokens": 8,
                "temperature": 0,
            },
        )
        ok = status == 200
        verdict = "VISION OK" if ok else "FAILED"
        print("   %-40s %-9s HTTP %-4s %.0fms" % (model, verdict, status or "net", ms))
        if not present:
            print("      ^ not in your account's model list — this id will 404")
        if not ok:
            detail = body[:240].replace("\n", " ")
            print("      %s" % detail)
            if '"code"' in detail and "does not support" in detail.lower():
                print("      ^ text-only model: never put this in VISION_MODELS")
        results.append((model, present, ok))

    winners = [m for m, _present, ok in results if ok]
    print("\n" + "-" * 64)
    if winners:
        print("Use this in backend/wrangler.toml [vars]:")
        print('  VISION_MODELS = "%s"' % ",".join(winners))
        print("\nThen after `wrangler deploy`, confirm from the Worker side:")
        print("  curl https://<worker>/models?probe=1")
        return 0

    print("No candidate accepted image input. Do not deploy the vision step yet.")
    print("Try the ids printed in step 1 that look multimodal, or request access to a vision model.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
