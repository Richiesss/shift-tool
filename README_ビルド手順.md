# Windows EXE ビルド手順

## 必要な環境

| ソフトウェア | バージョン | 入手先 |
|---|---|---|
| Python | 3.11 以上 | https://www.python.org/downloads/ |
| Git（任意） | 最新版 | https://git-scm.com/ |

> **注意**: Python インストール時に「Add Python to PATH」に必ずチェックを入れてください

---

## ビルド手順

### 方法A: バッチファイルで自動ビルド（推奨）

1. このフォルダ（`shift-tool/`）をWindowsにコピー
2. `build_windows.bat` をダブルクリックして実行
3. `dist/シフト表構築ツール.exe` が生成されます

### 方法B: 手動ビルド

```cmd
cd shift-tool
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pyinstaller shift_tool.spec --clean --noconfirm
```

---

## 生成ファイル

```
dist/
└── シフト表構築ツール.exe  ← これを配布・実行
```

- 単一EXEファイル（依存DLL込み）
- 初回起動に5〜10秒かかる場合があります（正常）
- データは `%USERPROFILE%\.shift_tool\shift_tool.db` に保存されます

---

## トラブルシューティング

### 「WindowsによってPCが保護されました」と表示される
→ 「詳細情報」→「実行」をクリックして起動してください  
（署名なしEXEのため表示されますが安全です）

### 起動しない場合
コマンドプロンプトから実行してエラーメッセージを確認してください:
```cmd
dist\シフト表構築ツール.exe
```

### ビルドに失敗する場合
`build\shift_tool\warn-shift_tool.txt` を確認してください。
