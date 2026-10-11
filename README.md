# todo_app_back

FastAPI + SQLite の Todo REST API（認証なし MVP）。

## 必要条件

- Python 3.11+

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 起動

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API: http://localhost:8000
- OpenAPI: http://localhost:8000/docs
- SQLite DB: `data/todos.db`（自動作成）

## API 概要

| Method | Path | 説明 |
|--------|------|------|
| GET | `/health` | ヘルスチェック |
| GET | `/todos` | 一覧（新しい順） |
| POST | `/todos` | 作成 `{ "title": "..." }` |
| PATCH | `/todos/{id}` | 更新 `{ "title"?, "completed"? }` |
| DELETE | `/todos/{id}` | 削除 |

レスポンス例:

```json
{
  "id": 1,
  "title": "牛乳を買う",
  "completed": false,
  "created_at": "2026-10-11T04:00:00+00:00",
  "updated_at": "2026-10-11T04:00:00+00:00"
}
```

フロントエンドから叩く場合は CORS を全許可しています（MVP）。
