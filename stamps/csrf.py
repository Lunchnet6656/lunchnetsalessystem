"""スタンプの送信（カード表示・押印・クーポン使用）の合言葉（CSRF）チェック。

LINE内ブラウザは、画面だけ復元してクッキーを消すことがあり、合言葉のクッキーが無くて
1日15〜20件のお客様が「期限切れ」になっていた（2026-10-17 Papertrail で確認）。
そこで、LINEのアクセストークンが付いた送信は、クッキーの代わりに
  ・送信元（Origin / Referer）が自分のサイトであること
  ・トークンが自分たちのLINEチャネルで発行されたこと
で確かめる。トークンが無い送信（開発環境・セッションでの本人確認）は今までどおり合言葉を確かめる。
"""
from functools import wraps
from urllib.parse import urlsplit

from django.conf import settings
from django.middleware.csrf import CsrfViewMiddleware
from django.views.decorators.csrf import csrf_exempt

from reservations import line_api


def stamp_channel_ids():
    # LIFF ID は「チャネルID-ランダム文字列」。チャネルIDの部分がトークンの client_id になる
    return {liff_id.split("-")[0] for liff_id in (settings.STAMP_LIFF_ID, settings.LIFF_ID) if liff_id}


def _same_site(request):
    allowed = {f"{request.scheme}://{request.get_host()}", *getattr(settings, "CSRF_TRUSTED_ORIGINS", [])}
    origin = request.META.get("HTTP_ORIGIN")
    if origin:
        return origin in allowed
    referer = request.META.get("HTTP_REFERER")
    if referer:
        parts = urlsplit(referer)
        return f"{parts.scheme}://{parts.netloc}" in allowed
    # 送信元ヘッダーが無いブラウザは、トークンの発行元確認だけで受け付ける
    return True


def line_token_or_csrf(on_line_error):
    """on_line_error(request, message) は、トークンの確認に失敗したときの画面を返す関数。"""
    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            token = (request.POST.get("line_access_token") or "").strip() if request.method == "POST" else ""
            if request.method == "POST" and not token:
                # トークンが無い送信は、今までどおりクッキーの合言葉で守る
                rejected = CsrfViewMiddleware(lambda r: None).process_view(request, view, args, kwargs)
                if rejected is not None:
                    return rejected
            elif token:
                if not _same_site(request):
                    return CsrfViewMiddleware(lambda r: None)._reject(request, "Origin checking failed (stamp).")
                try:
                    line_api.verify_channel(token, stamp_channel_ids())
                except line_api.LineAuthError as e:
                    return on_line_error(request, str(e), *args, **kwargs)
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
