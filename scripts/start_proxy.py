"""Start LiteLLM proxy for multi-provider routing.

Usage:
    python scripts/start_proxy.py

The proxy runs on http://localhost:4000 and must be running before
the Chainlit UI when LLM_PROVIDER=nine_router.

Provider chain (current): groq → ollama
To add a new provider: see litellm_config.yaml
"""
import subprocess
import sys
from pathlib import Path


CONFIG = Path(__file__).parent.parent / "litellm_config.yaml"
PORT = 4000


def main() -> None:
    if not CONFIG.exists():
        print(f"ERROR: config not found at {CONFIG}")
        sys.exit(1)

    print(f"Starting LiteLLM proxy on http://localhost:{PORT}")
    print(f"Config: {CONFIG}")
    print("Press Ctrl+C to stop.\n")

    subprocess.run(
        ["litellm", "--config", str(CONFIG), "--port", str(PORT)],
        check=False,
    )


if __name__ == "__main__":
    main()
