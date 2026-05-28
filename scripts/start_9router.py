"""Start 9Router local proxy for multi-model routing.

Usage:
    python scripts/start_9router.py

9Router runs on http://localhost:20128 and routes to 300+ models
using provider prefixes (gc/, if/, qw/, kr/, groq/, ...).

Current free chain (configured in .env NINE_ROUTER_MODELS):
    gc/gemini-3-flash-preview  → Gemini 3 Flash  (180K tokens/month free)
    if/kimi-k2                 → Kimi K2         (unlimited free via iFlow)
    qw/qwen3-coder-plus        → Qwen3 Coder+    (unlimited free)

Dashboard: http://localhost:20128  (configure providers + get API key)
All models: http://localhost:20128/v1/models
"""
import subprocess
import sys


def main() -> None:
    print("Starting 9Router on http://localhost:20128")
    print("Dashboard: http://localhost:20128")
    print("Press Ctrl+C to stop.\n")

    subprocess.run(
        ["9router", "--no-browser", "--log"],
        check=False,
    )


if __name__ == "__main__":
    main()
