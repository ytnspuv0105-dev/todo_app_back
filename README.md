# todo_app_back

FastAPI + PostgreSQL のワークスペース型課題管理 API。

## 機能（v1）

- メール＋パスワード認証（オープンサインアップ）
- 最初に登録したユーザーがグローバル管理者
- ワークスペース作成は管理者のみ／招待リンクで参加（admin / worker）
- 課題（Backlog 風フィールド）・親子・関連・添付ファイル

## 必要条件

- Python 3.11+
- PostgreSQL 16（ローカル or Docker）

## PostgreSQL の起動

### Docker Compose（推奨）

```bash
docker compose up -d
```

### ローカル PostgreSQL

```bash
# 例
createuser todo WITH PASSWORD 'todo';
createdb -O todo todo_app
```

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` の `DATABASE_URL` を環境に合わせてください。

## 起動

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API: http://localhost:8000
- OpenAPI: http://localhost:8000/docs

## API 概要

| Method | Path | 説明 |
|--------|------|------|
| POST | `/auth/signup` | 新規登録 |
| POST | `/auth/login` | ログイン |
| GET | `/auth/me` | 自分の情報 |
| GET/POST | `/workspaces` | 一覧 / 作成（作成はグローバル admin） |
| GET | `/workspaces/{id}/members` | メンバー一覧 |
| POST | `/workspaces/{id}/invites` | 招待リンク発行（WS admin） |
| GET | `/invites/{token}` | 招待情報 |
| POST | `/invites/{token}/join` | 招待で参加 |
| GET/POST | `/workspaces/{id}/tasks` | 課題一覧 / 作成（ワークスペース内番号を採番） |
| GET/PATCH/DELETE | `/workspaces/{id}/tasks/{taskId}` | 課題操作（親・関連はワークスペース番号で指定） |
| GET | `/workspaces/{id}/task-history` | 課題の変更履歴（ダッシュボード用、新しい順） |
| POST | `/workspaces/{id}/tasks/{taskId}/attachments` | 添付アップロード |
| GET/DELETE | `/workspaces/{id}/attachments/{id}` | 添付取得 / 削除 |

認証: `Authorization: Bearer <token>`
