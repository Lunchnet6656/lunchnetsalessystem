# CLAUDE.md - lunchnetsale Project

## Project Overview
ランチネット販売実績管理システム。弁当の販売管理・シフト管理・受注管理を行うWebアプリケーション。
Django 5.1.1, Python 3.12, Heroku + PostgreSQL でデプロイ。

## Key Commands
```bash
python manage.py runserver          # 開発サーバー起動
python manage.py makemigrations     # マイグレーション作成
python manage.py migrate            # マイグレーション適用
python manage.py collectstatic      # 静的ファイル収集
python manage.py test               # テスト実行
python manage.py test orders        # ordersアプリのテストのみ
git push heroku master              # Herokuデプロイ
```

## Architecture
- **lunchnetsale/** — メインプロジェクト設定（settings, root URLs, 主要views/forms）
- **sales/** — コアモデル（Product, SalesLocation, CustomUser, UserMenuPermission, DailyReport）
- **shifts/** — シフト管理アプリ（独自Tailwind UI）
- **orders/** — 受注・納品書管理アプリ（Tailwind standalone CSS）
- **templates/** — 共有テンプレート（Bootstrap 5.3 base.html + サイドバー）
- **static/** — 共有静的ファイル（CSS, JS）

## Important Patterns
- `AUTH_USER_MODEL` = `sales.CustomUser`（AbstractUser拡張、full_name, role付き）
- メニューアクセス制御: `UserMenuPermission` モデル（ユーザーと1対1）
- ビューは全て関数ベース + `@login_required` デコレータ
- 共有テンプレートは `templates/` から `base.html` を継承（Bootstrap）
- shifts / orders アプリは独自の `base.html` を持つ（TailwindCSS）
- Product の価格は3段階: `price_A`, `price_B`, `price_C`
- 日付は古いモデルでは `yyyymmdd` 文字列形式で保存される場合あり
- DB: ローカルはSQLite、Heroku上はPostgreSQL（dj_database_url経由）

## Static Files
- 本番: WhiteNoise で配信（`CompressedManifestStaticFilesStorage`）
- `STATICFILES_DIRS = [BASE_DIR / 'static']`
- `STATIC_ROOT = BASE_DIR / 'staticfiles'`
- `bin/post_compile` で Heroku ビルド時に collectstatic 実行

## Dependencies
- `requirements.txt` 参照（Django 5.1.1, reportlab, openpyxl, pandas 等）
- orders アプリ: TailwindCSS standalone CLI（Node.js不要）

## Deployment
- Heroku: `Procfile` → `gunicorn lunchnetsale.wsgi:application`
- Release phase: `python manage.py migrate --no-input`
- 環境変数: `SECRET_KEY`, `DATABASE_URL`, `DJANGO_ENV=production`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`

## Security
- CSRF保護有効（カスタムフェイルビュー `/csrf_failure/`）
- django-axes でログイン試行制限（5回失敗→1時間ロック）
- 本番: SSL強制、HSTS有効、セキュアクッキー
