"""QRコード経由の本日上書き（完売・急休み）エンドポイント。

仕様: .company/engineering/harness/specs/w001-本日の出店状況ページ.md「QR拡張（Sprint 10-12）」

売場スタッフ／しょうへいが拠点に貼ったQRをスキャンするとGET一発で today_override が更新される。
同期で publish_status_json を呼び Cloudflare へ反映するが、同一拠点60秒スロットルでビルド数を抑える。

sales/views.py には既存のオーファン import（.forms 等）があり、QR エンドポイントだけ独立した
モジュールに置くことで urls.py 経由の import 失敗を避ける（views.py 自体は誰も import していない）。
"""
from django.core.management import call_command
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from sales.models import SalesLocation
from sales.management.commands.generate_status_json import is_business_day


QR_PUBLISH_THROTTLE_SECONDS = 60
QR_RECEPT_START_HOUR = 8  # JST 8:00以降のみ受付（8:00自動更新の直後から）


def _render_qr_page(title, message_lines, status=200, accent="ok"):
    accent_color = {"ok": "#2a8f3f", "warn": "#c25a1f", "ng": "#a83232"}.get(accent, "#333")
    body = "".join(f"<p>{line}</p>" for line in message_lines)
    html = (
        '<!doctype html><html lang="ja"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f'<title>{title}</title>'
        '<style>'
        'body{font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif;'
        'margin:0;padding:24px;background:#fafafa;color:#333;}'
        '.card{max-width:420px;margin:40px auto;background:#fff;padding:24px;'
        'border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.08);'
        f'border-top:6px solid {accent_color};}}'
        f'h1{{margin:0 0 16px;font-size:22px;color:{accent_color};}}'
        'p{margin:8px 0;font-size:16px;line-height:1.6;}'
        '.note{font-size:13px;color:#777;margin-top:16px;}'
        '</style></head><body><div class="card">'
        f'<h1>{title}</h1>{body}'
        '<p class="note">※このページを閉じて構いません。</p>'
        '</div></body></html>'
    )
    return HttpResponse(html, status=status)


def _handle_qr(request, token, kind):
    assert kind in ("sold_out", "closed")
    field_name = "qr_sold_out_token" if kind == "sold_out" else "qr_closed_token"
    kind_label = "完売" if kind == "sold_out" else "急休み"

    loc = SalesLocation.objects.filter(**{field_name: token}).first()
    if loc is None:
        return _render_qr_page(
            title="無効なQRコードです",
            message_lines=[
                "このQRコードは認識できませんでした。",
                "運営にご連絡ください。",
            ],
            status=404, accent="ng",
        )

    if not loc.qr_enabled:
        return _render_qr_page(
            title="このQRは現在無効です",
            message_lines=[
                f"拠点：{loc.name}",
                "このQRはまだ運用開始されていません。",
                "運営にご連絡ください。",
            ],
            status=403, accent="warn",
        )

    now = timezone.localtime()
    today = now.date()

    if not is_business_day(today):
        return _render_qr_page(
            title="ご利用時間外です",
            message_lines=[
                f"拠点：{loc.name}",
                "本日は営業日ではありません（土日祝）。",
            ],
            status=403, accent="warn",
        )

    if now.hour < QR_RECEPT_START_HOUR:
        return _render_qr_page(
            title="ご利用時間外です",
            message_lines=[
                f"拠点：{loc.name}",
                f"このQRは{QR_RECEPT_START_HOUR}:00 以降にご利用ください。",
            ],
            status=403, accent="warn",
        )

    already_set = (loc.today_override == kind and loc.today_override_date == today)

    if not already_set:
        loc.today_override = kind
        loc.today_override_date = today
        loc.save(update_fields=["today_override", "today_override_date"])

    should_publish = (
        not already_set
        and (
            loc.last_qr_publish_at is None
            or (timezone.now() - loc.last_qr_publish_at).total_seconds() >= QR_PUBLISH_THROTTLE_SECONDS
        )
    )
    publish_note = "Cloudflareへの反映に最大1分かかります。"
    if should_publish:
        try:
            call_command("publish_status_json")
            loc.last_qr_publish_at = timezone.now()
            loc.save(update_fields=["last_qr_publish_at"])
            publish_note = "Cloudflareへ反映を送りました（最大1分で表示が更新されます）。"
        except Exception:
            # publish 失敗は致命的ではない（DBには保存済）。次の publish タイミングで同期される。
            publish_note = "サーバーへ保存しました。表示反映は次回更新時となります。"
    elif already_set:
        publish_note = f"この拠点はすでに「{kind_label}」に設定されています。"

    return _render_qr_page(
        title=f"✅ {kind_label} に変更しました",
        message_lines=[
            f"拠点：{loc.name}",
            f"時刻：{now.strftime('%H:%M')}",
            publish_note,
        ],
        status=200, accent="ok",
    )


@require_GET
def qr_sold_out(request, token):
    return _handle_qr(request, token, "sold_out")


@require_GET
def qr_closed(request, token):
    return _handle_qr(request, token, "closed")
