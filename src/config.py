from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM provider: "nine_router" (primary) | "groq" (cloud) | "ollama" (local)
    llm_provider: Literal["groq", "ollama", "nine_router"] = "groq"

    # Groq
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # 9Router — OpenAI-compatible local router (localhost:20128)
    nine_router_base_url: str = "http://localhost:20128/v1"
    nine_router_api_key: str = "local"
    # Comma-separated fallback chain — tried left→right until one succeeds.
    # Full free chain: gc/gemini-3-flash-preview,if/kimi-k2,qw/qwen3-coder-plus
    nine_router_models: str = "gc/gemini-3-flash-preview,if/kimi-k2,qw/qwen3-coder-plus"

    # Ollama (local fallback)
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "qwen3:4b"

    pcc_base_url: str = "https://www.pcc.edu"
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
