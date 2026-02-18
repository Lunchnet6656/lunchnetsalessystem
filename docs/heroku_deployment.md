# Heroku デプロイ手順

## 1. 前提条件

- [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli) がインストール済み
- `git` がインストール済み
- Heroku アカウントにログイン済み（`heroku login`）
- 対象の Heroku アプリが作成済み

```bash
heroku login
```

---

## 2. プロジェクト構成の確認

デプロイに必要なファイルが揃っているか確認します。

| ファイル | 役割 |
|---|---|
| `Procfile` | Heroku 起動コマンド定義 |
| `requirements.txt` | Python 依存パッケージ一覧 |
| `runtime.txt` | Python バージョン指定 |
| `.env.example` | 必要な環境変数の一覧（参考用） |

**Procfile の内容例:**
```
web: gunicorn lunchnetsale.wsgi --log-file -
```

**requirements.txt の更新:**
```bash
pip freeze > requirements.txt
```

---

## 3. 初回セットアップ（参考）

既存の Heroku アプリに git remote を追加する場合：

```bash
heroku git:remote -a <heroku-app-name>
```

現在の remote 確認：
```bash
git remote -v
```

---

## 4. 通常のデプロイ手順（メイン）

```bash
# 1. 変更をコミット
git add <変更ファイル>
git commit -m "変更内容の説明"

# 2. Heroku へプッシュ
git push heroku master

# 3. マイグレーション実行
heroku run python manage.py migrate

# 4. 動作確認
heroku open
```

> `master` ブランチ以外からデプロイする場合：
> ```bash
> git push heroku <branch-name>:master
> ```

---

## 5. 環境変数の管理

### 設定済み変数の確認
```bash
heroku config
```

### 変数の削除
```bash
heroku config:unset VARIABLE_NAME
```

---

### 5-1. 必須設定変数（本番で必ず設定すること）

| 変数名 | 本番での設定値 | 説明 |
|---|---|---|
| `DJANGO_ENV` | `production` | これを設定すると DEBUG・SSL・Cookie などが自動で本番モードに切り替わる |
| `SECRET_KEY` | ランダムな強い文字列 | デフォルトの insecure キーは絶対に使わない |
| `ALLOWED_HOSTS` | `your-app.herokuapp.com` | Heroku アプリのドメイン（カンマ区切りで複数可） |
| `CSRF_TRUSTED_ORIGINS` | `https://your-app.herokuapp.com` | CSRF 検証に使う完全 URL（https:// 必須） |
| `DATABASE_URL` | `postgres://...` | Heroku Postgres addon 追加で自動設定される場合が多い |

```bash
heroku config:set DJANGO_ENV='production'
heroku config:set SECRET_KEY='your-strong-random-secret-key'
heroku config:set ALLOWED_HOSTS='your-app.herokuapp.com'
heroku config:set CSRF_TRUSTED_ORIGINS='https://your-app.herokuapp.com'
```

---

### 5-2. メール送信設定（Gmail App Password）

| 変数名 | 設定値 |
|---|---|
| `EMAIL_HOST_USER` | 送信元 Gmail アドレス |
| `EMAIL_HOST_PASSWORD` | Gmail アプリパスワード（16文字） |
| `DEFAULT_FROM_EMAIL` | 送信元として表示するアドレス（通常は EMAIL_HOST_USER と同じ） |

```bash
heroku config:set EMAIL_HOST_USER='your-email@gmail.com'
heroku config:set EMAIL_HOST_PASSWORD='your-16-char-app-password'
heroku config:set DEFAULT_FROM_EMAIL='your-email@gmail.com'
```

---

### 5-3. LINE Bot 設定

| 変数名 | 設定値 |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Developers コンソールのチャネルアクセストークン |
| `LINE_CHANNEL_SECRET` | LINE Developers コンソールのチャネルシークレット |
| `LINE_BOT_BASIC_ID` | LINE Bot の Basic ID（例: @abc12345） |

```bash
heroku config:set LINE_CHANNEL_ACCESS_TOKEN='...'
heroku config:set LINE_CHANNEL_SECRET='...'
heroku config:set LINE_BOT_BASIC_ID='@abc12345'
```

---

### 5-4. DJANGO_ENV=production 設定時に自動で変わる挙動（settings.py 内）

`DJANGO_ENV=production` をセットするだけで、以下が自動で本番モードになる（追加設定不要）:

- `DEBUG = False`
- `SECURE_SSL_REDIRECT = True`（HTTP→HTTPS 強制リダイレクト）
- `SECURE_HSTS_SECONDS = 31536000`（HSTS 有効化）
- `SESSION_COOKIE_SECURE = True`
- `CSRF_COOKIE_SECURE = True`
- `STATICFILES_STORAGE` → WhiteNoise の圧縮ストレージを使用
- データベース接続で `ssl_require=True`

---

### 5-5. settings.py で変更が不要な箇所

以下は環境変数で制御されており、`settings.py` 本体を直接書き換える必要はない:

- `EMAIL_HOST`（デフォルト: smtp.gmail.com）
- `EMAIL_PORT`（デフォルト: 587）
- `EMAIL_USE_TLS`（デフォルト: True）
- `TIME_ZONE`（Asia/Tokyo 固定）

> `.env.example` を参照して必要な変数をすべて設定してください。

---

## 6. DB マイグレーション

```bash
# マイグレーション実行
heroku run python manage.py migrate

# マイグレーション状況確認
heroku run python manage.py showmigrations

# スーパーユーザー作成（初回のみ）
heroku run python manage.py createsuperuser
```

---

## 7. ログ確認

```bash
# リアルタイムログ監視
heroku logs --tail

# 最新 200 行を表示
heroku logs -n 200

# アプリログのみ（システムログを除外）
heroku logs --tail --source app
```

---

## 8. ロールバック

```bash
# リリース一覧を確認
heroku releases

# 特定バージョンへロールバック（例: v42）
heroku rollback v42

# 直前のバージョンへロールバック
heroku rollback
```

> ロールバック後もデータベーススキーマは変わらないため、マイグレーションの互換性に注意してください。

---

## 9. トラブルシューティング

### アプリが起動しない
```bash
heroku logs --tail
```
エラーログを確認し、以下を疑う：
- `SECRET_KEY` などの環境変数が未設定
- `requirements.txt` に漏れがある
- `Procfile` の記述ミス

### `H10 App crashed` エラー
```bash
heroku ps                    # dyno の状態確認
heroku restart               # dyno 再起動
heroku logs --tail           # エラー詳細確認
```

### マイグレーションエラー
```bash
heroku run python manage.py showmigrations   # 状況確認
heroku run python manage.py migrate --run-syncdb
```

### 静的ファイルが表示されない
```bash
heroku run python manage.py collectstatic --noinput
```
`DISABLE_COLLECTSTATIC=0` が設定されているか確認。

### データベース接続エラー
```bash
heroku config | grep DATABASE_URL    # URL 確認
heroku pg:info                        # DB 状態確認
heroku pg:diagnose                    # 診断レポート
```

### Dyno の再起動
```bash
heroku restart
```
