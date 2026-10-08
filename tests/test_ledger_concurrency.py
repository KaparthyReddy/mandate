import threading
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from mandate import ledger
from mandate.models import Base


def test_concurrent_appends_all_land_and_the_chain_stays_valid(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    errors: list[Exception] = []

    def work(worker: int) -> None:
        with factory() as session:
            for step in range(5):
                try:
                    ledger.append(session, "test", {"worker": worker, "step": step})
                except Exception as exc:
                    errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with Session(engine) as session:
        result = ledger.verify_chain(session)
    engine.dispose()
    assert errors == []
    assert result.valid
    assert result.checked == 50
