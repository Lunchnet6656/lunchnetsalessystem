"""最終利用の記録（ユーザー管理の「最終利用」「長期未利用」用）。

User.last_login はログイン画面でパスワードを入れた日しか更新されない。一般スタッフは
ログイン状態が最終操作から30日続く（スライド式）ので、毎日使う人ほど古い日付のまま残る。
そこで、ログインしたまま使った日も1日1回 last_login に書く（毎回書くとDBが重くなるため1日1回）。
"""
from django.contrib.auth import get_user_model
from django.utils import timezone


class LastSeenMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            now = timezone.now()
            if user.last_login is None or timezone.localtime(user.last_login).date() < timezone.localdate(now):
                # save() だと他の項目まで上書きするので、この1項目だけ更新する
                get_user_model().objects.filter(pk=user.pk).update(last_login=now)
                user.last_login = now
        return self.get_response(request)
