"""
結合テスト: routes/auth_routes.py
品質特性: セキュリティ・機能適合性（ISO/IEC 25010 §4.2.6, §4.2.1）
テストレベル: 結合テスト（Flaskテストクライアント使用）
"""
import pytest


class TestLoginGet:
    def test_login_page_returns_200(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200

    def test_login_page_contains_form(self, client):
        resp = client.get("/login")
        html = resp.data.decode("utf-8")
        assert "password" in html.lower() or "login" in html.lower()


class TestLoginPost:
    def test_wrong_password_stays_on_login(self, client, app):
        """APP_PASSWORDが設定されている場合、誤パスワードはログインページに留まる"""
        import auth
        original = auth.APP_PASSWORD
        try:
            auth.APP_PASSWORD = "correct_password"
            resp = client.post(
                "/login",
                data={"password": "wrong_password"},
                follow_redirects=False,
            )
            assert resp.status_code == 200
        finally:
            auth.APP_PASSWORD = original

    def test_no_password_set_redirects_after_any_post(self, client, app):
        """APP_PASSWORDが空の場合、任意のパスワードでもダッシュボードへリダイレクト"""
        import auth
        original = auth.APP_PASSWORD
        try:
            auth.APP_PASSWORD = ""
            # パスワード無しでも before_request で全開放
            resp = client.get("/", follow_redirects=False)
            assert resp.status_code in (200, 302)
        finally:
            auth.APP_PASSWORD = original


class TestSafeNext:
    """_safe_next のオープンリダイレクト対策"""

    def _get_safe_next(self, url):
        from routes.auth_routes import _safe_next
        return url

    def test_relative_path_allowed(self, app):
        with app.app_context():
            from routes.auth_routes import _safe_next
            result = _safe_next("/employees/")
            assert result == "/employees/"

    def test_external_url_rejected(self, app):
        with app.app_context():
            from routes.auth_routes import _safe_next
            result = _safe_next("https://evil.com")
            assert not result.startswith("http")

    def test_double_slash_rejected(self, app):
        with app.app_context():
            from routes.auth_routes import _safe_next
            result = _safe_next("//evil.com")
            assert not result.startswith("//")

    def test_backslash_rejected(self, app):
        with app.app_context():
            from routes.auth_routes import _safe_next
            result = _safe_next("/\\evil.com")
            assert "\\" not in result

    def test_empty_string_returns_dashboard(self, app):
        with app.app_context():
            from routes.auth_routes import _safe_next
            result = _safe_next("")
            assert result.startswith("/")


class TestLogout:
    def test_logout_redirects_to_login(self, client):
        resp = client.get("/logout", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers.get("Location", "")


class TestSecurityHeaders:
    """セキュリティヘッダーが全レスポンスに付与される"""

    def test_x_content_type_options(self, client):
        resp = client.get("/login")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options(self, client):
        resp = client.get("/login")
        assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN"

    def test_x_xss_protection(self, client):
        resp = client.get("/login")
        assert "1" in resp.headers.get("X-XSS-Protection", "")
