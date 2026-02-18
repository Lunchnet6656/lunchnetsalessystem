# LINE Bot & Heroku Scheduler セットアップ手順

このドキュメントでは、シフト管理アプリの通知機能（LINE Bot・メールリマインダー）を動かすために必要な外部サービスの登録から設定・動作確認まで、順を追って説明します。

---

## 目次

1. [全体の構成イメージ](#全体の構成イメージ)
2. [LINE Bot セットアップ](#line-bot-セットアップ)
   - [Step 1: LINE Developers でアカウント作成](#step-1-line-developers-でアカウント作成)
   - [Step 2: プロバイダーとチャンネルの作成](#step-2-プロバイダーとチャンネルの作成)
   - [Step 3: トークン・シークレットの取得](#step-3-トークンシークレットの取得)
   - [Step 4: Webhook URL の設定](#step-4-webhook-url-の設定)
   - [Step 5: .env への設定](#step-5-env-への設定)
   - [Step 6: スタッフとのアカウント連携フロー](#step-6-スタッフとのアカウント連携フロー)
3. [Gmail 送信設定（メール通知）](#gmail-送信設定メール通知)
   - [Step 1: Googleアカウントの2段階認証を有効化](#step-1-googleアカウントの2段階認証を有効化)
   - [Step 2: アプリパスワードの発行](#step-2-アプリパスワードの発行)
   - [Step 3: .env への設定](#step-3-env-への設定)
4. [Heroku Scheduler セットアップ](#heroku-scheduler-セットアップ)
   - [Step 1: アドオンの追加](#step-1-アドオンの追加)
   - [Step 2: ジョブの登録](#step-2-ジョブの登録)
   - [Step 3: 動作確認](#step-3-動作確認)
5. [Heroku Config Vars への設定反映](#heroku-config-vars-への設定反映)
6. [動作確認チェックリスト](#動作確認チェックリスト)
7. [トラブルシューティング](#トラブルシューティング)

---

## 全体の構成イメージ

```
スタッフ（スマホ）
    │
    ├─ LINE Bot ────────────────────→ LINE Messaging API
    │   友だち追加・コード送信              │
    │                                      ↓ Webhook
    └─ ブラウザ（アプリ）             /shifts/line-webhook/
         設定画面でコード発行               │
                                           ↓
                                    UserProfile.line_user_id に保存

管理者
    │
    ├─ 募集期間を作成 ──────────────→ 全スタッフに LINE / メール通知
    └─ 「未提出者に通知」ボタン ─────→ 未提出スタッフに LINE / メール通知

Heroku Scheduler（毎朝9時）
    └─ send_shift_reminders --days 3 → 締切3日前の未提出スタッフに自動通知
```

---

## LINE Bot セットアップ

### Step 1: LINE Developers でアカウント作成

1. https://developers.line.biz/ にアクセス
2. 右上の「**ログイン**」をクリック
3. 普段使っている LINE アカウントでログイン（LINEアプリでQRコードをスキャン）
4. 初回は「**開発者情報の登録**」画面が表示される
   - 名前・メールアドレスを入力して「**同意して登録**」
5. LINE Developers コンソールのトップページが開いたら完了

---

### Step 2: プロバイダーとチャンネルの作成

#### プロバイダーを作成

1. コンソール左上の「**プロバイダー**」→「**作成**」
2. プロバイダー名を入力（例: `シフト管理`）→「**作成**」

#### Messaging API チャンネルを作成

1. 作成したプロバイダーを選択
2. 「**チャンネル設定**」タブ →「**新規チャンネル作成**」
3. 「**Messaging API**」を選択
4. 以下を入力して「**作成**」

   | 項目 | 入力例 |
   |---|---|
   | チャンネル名 | シフト管理Bot |
   | チャンネル説明 | シフト希望提出の通知Bot |
   | 大業種 | IT・通信 |
   | 小業種 | その他 |

5. 利用規約に同意して「**作成**」→「**OK**」

---

### Step 3: トークン・シークレットの取得

作成したチャンネルのページで以下を取得します。

#### Channel Secret（チャンネルシークレット）

1. 「**チャンネル基本設定**」タブを開く
2. 「**Channel secret**」の欄に表示されている値をコピー

#### Channel Access Token（チャンネルアクセストークン）

1. 「**Messaging API設定**」タブを開く
2. 下部の「**チャンネルアクセストークン（長期）**」セクション
3. 「**発行**」ボタンをクリック
4. 表示された長いトークン文字列をコピー

> **メモ**: 両方の値は後で `.env` に設定します。画面を閉じないようにしておくか、テキストに貼り付けておいてください。

---

### Step 4: Webhook URL の設定

1. 「**Messaging API設定**」タブ
2. 「**Webhook設定**」セクションの「**編集**」
3. Webhook URL に以下を入力:

   ```
   https://あなたのアプリのドメイン/shifts/line-webhook/
   ```

   例: `https://your-app.herokuapp.com/shifts/line-webhook/`

4. 「**更新**」→「**Webhookの利用**」をオンに切り替え
5. 「**検証**」ボタンを押して「**成功**」と表示されることを確認

> **注意**: Webhook の検証が通るためには、先にアプリが Heroku にデプロイされ、`LINE_CHANNEL_SECRET` が設定されている必要があります。一旦スキップして後で確認しても OK です。

#### 自動応答メッセージを無効化（任意）

LINE公式アカウントのデフォルト自動応答が邪魔になる場合は:
1. 「Messaging API設定」→「LINE公式アカウント機能」→「自動応答メッセージ」→「編集」
2. 「応答メッセージ」をオフにする

---

### Step 5: .env への設定

ローカル開発用の `.env` ファイルに追記します:

```env
# LINE Bot
LINE_CHANNEL_ACCESS_TOKEN=取得したアクセストークンをここに貼り付け
LINE_CHANNEL_SECRET=取得したチャンネルシークレットをここに貼り付け
```

---

### Step 6: スタッフとのアカウント連携フロー

スタッフが LINE 通知を受け取るには、以下の手順でアカウント連携を行います。

#### スタッフ側の手順

1. **LINE で Bot を友だち追加**
   - 管理者から Bot の QR コードまたは ID を共有してもらう
   - LINE アプリで「友だち追加」→「QRコード」でスキャン

2. **アプリで連携コードを発行**
   - ブラウザでシフト管理アプリにログイン
   - ナビバー「通知設定」→「連携コードを発行」ボタンをタップ
   - 画面に **6桁の数字** が表示される（例: `483921`）

3. **LINE Bot にコードを送信**
   - LINE アプリで Bot のトーク画面を開く
   - 6桁のコードをそのまま送信（例: `483921`）
   - Bot から「LINE連携が完了しました！」と返信が来れば成功

4. **通知設定を確認**
   - アプリの「通知設定」画面で「LINE連携済み」と表示されることを確認
   - 「LINEで通知を受け取る」にチェックが入っていることを確認

#### 管理者側の確認

Django 管理画面（`/secure-admin/`）→「シフト」→「ユーザープロフィール」で、
対象スタッフの `LINE User ID` にUIDが保存されているか確認できます。

---

## Gmail 送信設定（メール通知）

LINE 未連携のスタッフへはメールで通知します。Gmail の「アプリパスワード」機能を使います。

### Step 1: Googleアカウントの2段階認証を有効化

1. https://myaccount.google.com/ にアクセス
2. 左メニュー「**セキュリティ**」
3. 「**Googleへのログイン**」→「**2段階認証プロセス**」
4. 「**使ってみる**」→ 画面の指示に従って設定

> アプリパスワードを発行するには2段階認証が必須です。

---

### Step 2: アプリパスワードの発行

1. https://myaccount.google.com/apppasswords にアクセス
2. 「**アプリを選択**」→「**その他（名前を入力）**」
3. 名前に `シフト管理` などと入力 →「**生成**」
4. 表示された **16桁のパスワード**（スペースなし）をコピー

> このパスワードは一度しか表示されません。必ずコピーしておいてください。

---

### Step 3: .env への設定

```env
# Gmail メール送信
EMAIL_HOST_USER=your-gmail@gmail.com
EMAIL_HOST_PASSWORD=xxxxxxxxxxxxxxxxxxxx   # 16桁のアプリパスワード（スペースなし）
DEFAULT_FROM_EMAIL=your-gmail@gmail.com
```

---

## Heroku Scheduler セットアップ

締切 3 日前に未提出スタッフへ自動でリマインドを送るための設定です。

### Step 1: アドオンの追加

#### Heroku CLI を使う場合

```bash
heroku addons:create scheduler:standard --app あなたのアプリ名
```

#### Heroku ダッシュボードを使う場合

1. https://dashboard.heroku.com/ でアプリを選択
2. 「**Resources**」タブ
3. 「**Find more add-ons**」→ 検索欄に `Scheduler` と入力
4. 「**Heroku Scheduler**」を選択 →「**Install Heroku Scheduler**」
5. プラン「**Standard – Free**」を選択 →「**Submit Order Form**」

---

### Step 2: ジョブの登録

1. Heroku ダッシュボード →「Resources」タブ
2. 「**Heroku Scheduler**」をクリックして管理画面を開く
3. 「**Create job**」ボタン
4. 以下のように設定:

   | 項目 | 設定値 |
   |---|---|
   | Schedule | Every day at... |
   | Time | `09:00 AM UTC` ※日本時間18時。9時JST にしたい場合は `00:00 UTC` |
   | Run Command | `python manage.py send_shift_reminders --days 3` |

5. 「**Save Job**」

> **時刻の注意**: Heroku Scheduler は UTC 基準です。
> 日本時間（JST = UTC+9）に変換:
> - 日本時間 9:00 → UTC 0:00
> - 日本時間 18:00 → UTC 9:00
> 毎朝9時JST に実行したい場合は `00:00 UTC` を指定してください。

---

### Step 3: 動作確認

手動でコマンドを即時実行してテストできます:

```bash
# 締切3日前の期間を対象にリマインド送信
heroku run python manage.py send_shift_reminders --days 3 --app あなたのアプリ名

# 今後999日以内の全期間を対象にテスト（対象なし確認）
heroku run python manage.py send_shift_reminders --days 999 --app あなたのアプリ名
```

実行ログの例:
```
[2026年3月 1日〜14日 (募集中)] リマインド送信: LINE=3, メール=2, 対象=5名
```

---

## Heroku Config Vars への設定反映

ローカルの `.env` の内容を Heroku の環境変数（Config Vars）に設定します。
**この設定をしないと本番環境でLINE/メール通知が動きません。**

#### Heroku CLI を使う場合（推奨・一括設定）

```bash
heroku config:set \
  LINE_CHANNEL_ACCESS_TOKEN="ここにトークン" \
  LINE_CHANNEL_SECRET="ここにシークレット" \
  EMAIL_HOST_USER="your-gmail@gmail.com" \
  EMAIL_HOST_PASSWORD="xxxxxxxxxxxxxxxx" \
  DEFAULT_FROM_EMAIL="your-gmail@gmail.com" \
  --app あなたのアプリ名
```

#### Heroku ダッシュボードを使う場合

1. ダッシュボード →「**Settings**」タブ
2. 「**Config Vars**」→「**Reveal Config Vars**」
3. 以下のキーと値を1つずつ「Add」で追加:

   | Key | Value |
   |---|---|
   | `LINE_CHANNEL_ACCESS_TOKEN` | チャンネルアクセストークン |
   | `LINE_CHANNEL_SECRET` | チャンネルシークレット |
   | `EMAIL_HOST_USER` | Gmailアドレス |
   | `EMAIL_HOST_PASSWORD` | 16桁アプリパスワード |
   | `DEFAULT_FROM_EMAIL` | Gmailアドレス（同上でOK） |

---

## 動作確認チェックリスト

### LINE Bot

- [ ] LINE Developers でチャンネルを作成した
- [ ] Channel Access Token を発行・コピーした
- [ ] Channel Secret をコピーした
- [ ] Heroku に `LINE_CHANNEL_ACCESS_TOKEN` と `LINE_CHANNEL_SECRET` を設定した
- [ ] Webhook URL を設定して「検証」が成功した
- [ ] テストスタッフが Bot を友だち追加した
- [ ] アプリで連携コードを発行し、LINE Bot に送信して連携完了した
- [ ] Django 管理画面でスタッフの `line_user_id` が保存されているか確認した
- [ ] 管理者が「未提出者に通知」ボタンを押し、テストスタッフの LINE に届いた

### Gmail メール

- [ ] Googleアカウントの2段階認証を有効化した
- [ ] アプリパスワード（16桁）を発行・コピーした
- [ ] Heroku に `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` を設定した
- [ ] テストスタッフのプロフィールに `notification_email` を設定した
- [ ] 管理者が「未提出者に通知」ボタンを押し、テストメールが届いた

### Heroku Scheduler

- [ ] Heroku Scheduler アドオンを追加した
- [ ] ジョブ `python manage.py send_shift_reminders --days 3` を登録した
- [ ] 実行時刻を UTC で正しく設定した（JST 9:00 = UTC 0:00）
- [ ] `heroku run` で手動実行してエラーが出ないことを確認した

---

## トラブルシューティング

### LINE Bot が応答しない

**原因**: Webhook URL が間違っているか、アプリが起動していない
**対処**:
1. Heroku ダッシュボードで直近のログを確認: `heroku logs --tail`
2. LINE Developers の Webhook 設定画面で「検証」を再実行
3. URL が `https://` で始まり末尾に `/` があることを確認

---

### 「連携コードが一致しない」と Bot が返す

**原因**: コードの有効期限切れ（セッション期限）または入力ミス
**対処**:
1. アプリの「通知設定」画面で「連携コードを発行」を再度押す
2. 新しく表示された6桁のコードを LINE に送信する

---

### メールが届かない

**原因**: アプリパスワードの設定ミスまたは Gmail のセキュリティ設定
**対処**:
1. Heroku の Config Vars で `EMAIL_HOST_PASSWORD` を確認（スペースなし16桁）
2. 送信元 Gmail アカウントの「最近のアクティビティ」で不審なブロックがないか確認
3. ローカルで動作確認: `python manage.py shell` で以下を実行

   ```python
   from django.core.mail import send_mail
   send_mail('テスト', 'テストメール', 'from@gmail.com', ['to@example.com'])
   ```

---

### Heroku Scheduler が実行されない

**原因**: ジョブのコマンドやスケジュールの設定ミス
**対処**:
1. `heroku run python manage.py send_shift_reminders --days 3` で手動テスト
2. エラーが出た場合は `heroku logs --tail` でスタックトレースを確認
3. 対象期間が存在するか確認（OPEN ステータスで締切3日前の期間があるか）

---

### `line-bot-sdk` がインストールされていない

```bash
pip install line-bot-sdk==3.14.0
# または
pip install -r requirements.txt
```

---

*最終更新: 2026-02-18*
