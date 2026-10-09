import sys

from mandate.config import get_settings
from mandate.embeddings import build_embedder
from mandate.reputation import ReputationIndex, load_records


def main() -> None:
    if len(sys.argv) < 2:
        print('usage: python scripts/risk_probe.py "Merchant name"')
        raise SystemExit(2)
    embedder = build_embedder(get_settings())
    index = ReputationIndex(load_records(), embedder)
    print(f"embedder: {type(embedder).__name__}  index: {index.backend}")
    for match in index.search(sys.argv[1], k=5):
        record = match.record
        print(f"{match.similarity:.2f}  {record.name:<26} {record.reputation:<8} {record.category}")


if __name__ == "__main__":
    main()
