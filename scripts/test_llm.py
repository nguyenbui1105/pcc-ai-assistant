"""Sanity check: verify Ollama + qwen2.5:3b are working."""
import sys
import asyncio
sys.path.insert(0, ".")

from loguru import logger
from src.llm.ollama_llm import OllamaLLM


def test_sync() -> None:
    llm = OllamaLLM()

    logger.info("Checking Ollama connection...")
    assert llm.health_check(), "Ollama is not running or unreachable"
    logger.success("Ollama is reachable.")

    logger.info("Testing sync chat...")
    response = llm.chat("Say hello in one sentence.")
    logger.success(f"Response: {response}")


async def test_stream() -> None:
    llm = OllamaLLM()
    logger.info("Testing streaming chat...")
    print("Stream: ", end="", flush=True)
    async for chunk in llm.chat_stream("What is Portland Community College in one sentence?"):
        print(chunk, end="", flush=True)
    print()
    logger.success("Streaming works.")


if __name__ == "__main__":
    test_sync()
    asyncio.run(test_stream())
