import sys

from mandate.config import get_settings
from mandate.embeddings import build_embedder
from mandate.reputation import ReputationIndex, load_records


def main() -> None:
    if len(sys.argv) < 3:
        print('usage: python scripts/risk_probe.py "Merchant name" category')
        raise SystemExit(2)
    merchant, category = sys.argv[1], sys.argv[2]
    embedder = build_embedder(get_settings())
    index = ReputationIndex(load_records(), embedder)
    print(f"embedder: {type(embedder).__name__}  index: {index.backend}")
    for match in index.search(f"{merchant} | {category}", k=5):
        record = match.record
        print(f"{match.similarity:.2f}  {record.name:<26} {record.reputation:<8} {record.category}")


if __name__ == "__main__":
    main()
