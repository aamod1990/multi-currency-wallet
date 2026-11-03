from app.core.config import Settings


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test,")
    assert Settings().cors_origin_list == ["http://a.test", "http://b.test"]


def test_mongodb_settings_from_env(monkeypatch):
    monkeypatch.setenv("MONGODB_URL", "mongodb+srv://u:p@cluster.example.net/")
    monkeypatch.setenv("MONGODB_DB", "prod_wallet")
    s = Settings()
    assert s.mongodb_url.startswith("mongodb+srv://") and s.mongodb_db == "prod_wallet"
