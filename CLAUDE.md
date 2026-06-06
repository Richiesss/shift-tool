# CLAUDE.md

## Git ワークフロー

コード変更後は必ずコミット＆プッシュをセットで行う。

```bash
git add <変更ファイル>
git commit -m "..."
git push origin main
git push hf main
```

リモートは2つある：
- `origin` — GitHub (https://github.com/Richiesss/shift-tool.git)
- `hf` — HuggingFace Spaces (本番環境)
