from src.infrastructure.database.session import normalize_database_url


def test_normalize_database_url_converts_neon_postgres_url_for_asyncpg() -> None:
    database_url = (
        "postgresql://user:pass@example.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )

    normalized_url = normalize_database_url(database_url)

    assert normalized_url == (
        "postgresql+asyncpg://user:pass@example.neon.tech/neondb?ssl=require"
    )


def test_normalize_database_url_leaves_sqlite_url_unchanged() -> None:
    assert normalize_database_url("sqlite:///./file_converter.db") == (
        "sqlite:///./file_converter.db"
    )
