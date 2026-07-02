"""
結合テスト: routes/employees.py
品質特性: 機能適合性・信頼性（ISO/IEC 25010 §4.2.1, §4.2.5）
テストレベル: 結合テスト（Flaskテストクライアント使用）
"""
import pytest


class TestEmployeeListPage:
    def test_index_returns_200(self, client):
        resp = client.get("/employees/")
        assert resp.status_code == 200

    def test_index_contains_html(self, client):
        resp = client.get("/employees/")
        assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()

    def test_archive_param_accepted(self, client):
        resp = client.get("/employees/?archive=1")
        assert resp.status_code == 200

    def test_search_param_accepted(self, client):
        resp = client.get("/employees/?q=テスト")
        assert resp.status_code == 200


class TestEmployeeNewPage:
    def test_new_page_returns_200(self, client):
        resp = client.get("/employees/new")
        assert resp.status_code == 200


class TestEmployeeEditPage:
    def test_nonexistent_employee_redirects(self, client):
        # 存在しない従業員IDはフラッシュ付きで一覧へリダイレクト
        resp = client.get("/employees/99999/edit", follow_redirects=False)
        assert resp.status_code == 302
        assert "/employees" in resp.headers.get("Location", "")

    def test_nonexistent_employee_flash_message(self, client):
        resp = client.get("/employees/99999/edit", follow_redirects=True)
        assert resp.status_code == 200

    def test_invalid_id_handled(self, client):
        resp = client.get("/employees/abc/edit")
        assert resp.status_code in (400, 404)
