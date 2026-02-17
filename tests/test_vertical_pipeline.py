import os
import sqlite3
import tempfile
from pathlib import Path

from pipelines.ingest_open_calls import run as ingest_open_calls_run
from pipelines.propose_replies import run as propose_replies_run
from pipelines.rank_candidates import run as rank_candidates_run


def test_vertical_pipeline_end_to_end() -> None:
    old_db_path = os.environ.get("DB_PATH")
    old_data_dir = os.environ.get("REVFIRST_DATA_DIR")

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "db.sqlite"
        data_dir = Path(tmp_dir) / "data"

        try:
            os.environ["DB_PATH"] = str(db_path)
            os.environ["REVFIRST_DATA_DIR"] = str(data_dir)

            ingest_result = ingest_open_calls_run(limit=4)
            rank_result = rank_candidates_run(limit=10)
            propose_result = propose_replies_run(limit=10, min_score=0.5)

            assert ingest_result["status"] == "ok"
            assert ingest_result["ingested"] > 0
            assert rank_result["status"] == "ok"
            assert rank_result["ranked"] >= ingest_result["ingested"]
            assert propose_result["status"] == "ok"
            assert propose_result["proposed"] >= 1

            assert (data_dir / "candidates.jsonl").exists()
            assert (data_dir / "ranked_candidates.json").exists()
            assert (data_dir / "proposed_replies.jsonl").exists()
            assert (data_dir / "approved_queue.jsonl").exists()

            conn = sqlite3.connect(db_path)
            candidates_count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
            ranked_count = conn.execute("SELECT COUNT(*) FROM ranked_candidates").fetchone()[0]
            proposed_count = conn.execute("SELECT COUNT(*) FROM proposed_replies").fetchone()[0]
            conn.close()

            assert candidates_count > 0
            assert ranked_count > 0
            assert proposed_count > 0
        finally:
            if old_db_path is None:
                os.environ.pop("DB_PATH", None)
            else:
                os.environ["DB_PATH"] = old_db_path

            if old_data_dir is None:
                os.environ.pop("REVFIRST_DATA_DIR", None)
            else:
                os.environ["REVFIRST_DATA_DIR"] = old_data_dir
