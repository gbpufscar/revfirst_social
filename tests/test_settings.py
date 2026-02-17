import os

from config.settings import get_settings


def test_settings_reads_runtime_environment() -> None:
    old_data_dir = os.environ.get("REVFIRST_DATA_DIR")
    old_db_path = os.environ.get("DB_PATH")

    try:
        os.environ["REVFIRST_DATA_DIR"] = "/tmp/revfirst_data_test"
        os.environ["DB_PATH"] = "/tmp/revfirst_db_test.sqlite"
        settings = get_settings()
        assert settings.data_dir == "/tmp/revfirst_data_test"
        assert settings.db_path == "/tmp/revfirst_db_test.sqlite"
    finally:
        if old_data_dir is None:
            os.environ.pop("REVFIRST_DATA_DIR", None)
        else:
            os.environ["REVFIRST_DATA_DIR"] = old_data_dir

        if old_db_path is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = old_db_path
