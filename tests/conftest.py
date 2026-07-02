"""pytestの共有フィクスチャ"""
import os
import sys
import tempfile
import pytest

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(scope="session")
def app():
    """結合テスト用Flaskアプリ（テスト用SQLite・CSRF無効）"""
    import db.database as dbmod
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    dbmod.DB_PATH = __import__("pathlib").Path(tmp.name)

    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("APP_PASSWORD", None)

    # auth.py はモジュールロード時に APP_PASSWORD を確定するため再ロードする
    import auth
    import importlib
    importlib.reload(auth)

    from web_app import create_app
    application = create_app()
    application.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SECRET_KEY="test-secret",
    )
    yield application

    os.unlink(tmp.name)


@pytest.fixture
def client(app):
    return app.test_client()
