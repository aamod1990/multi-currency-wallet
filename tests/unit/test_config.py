from app.core.config import Settings


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test,")
    assert Settings().cors_origin_list == ["http://a.test", "http://b.test"]


def test_database_url_normalized_to_psycopg_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/db")
    assert Settings().database_url == "postgresql+psycopg://u:p@h:5432/db"
