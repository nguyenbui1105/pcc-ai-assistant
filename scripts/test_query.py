"""Quick CLI query test — usage: python scripts/test_query.py "your question here" """
import sys
import os
os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, ".")

from src.pipeline.query import run_query


def main() -> None:
    question = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "What are F1 visa requirements for PCC?"
    print(f"\nQuestion: {question}\n{'='*60}")

    result = run_query(question)
    print(result["answer"])

    if result["sources"]:
        print(f"\n{'='*60}\nSources:")
        for s in result["sources"]:
            print(f"  [{s['score']}] {s['title']}\n  {s['url']}")


if __name__ == "__main__":
    main()
