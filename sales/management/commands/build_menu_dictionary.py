"""過去のメニュー（Product）からメニューデータベースの初期データを作る。最初の送信から、よく出るメニューを自動で決めるため。

    python manage.py build_menu_dictionary            # 作る内容を見るだけ（書き込まない）
    python manage.py build_menu_dictionary --apply    # 書き込む（メニューデータベースにもうある名前は変えない）

決め方（メニュー名ごとに、最後に出た週のメニューで）：
- 値段の種類：★→特選／大盛り→大盛り／それ以外は、その週の「通常」の値段（★と大盛り以外で一番多い価格A）と比べて
  安ければお手頃、同じなら通常。高いもの（★がないのに通常より高い）は通常にして「要確認」
- 容器：最後に出たときの容器（「黒容器など」「ごはん容器」の表記ゆれはそろえる）。一覧にない容器は黒容器にして「要確認」
- 実際にその値段・容器で売っていたので、決めきれたものは「確認済み」
- ただし判定ルールと食い違うものは「要確認」にする。値段の種類はルールのほうを使う
  （10/1の値上げ前はチャーハンも通常と同じ値段だったため、昔の値段だけで決めると今のお手頃を通常と間違える）。
  容器は実際に使っていた容器のまま
"""
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from sales.menu_registry import CONTAINER_ALIASES, classify, normalize_name, parse_week
from sales.models import ClassifyRule
from sales.models import CONTAINER_CHOICES, MenuProfile, PriceRank, Product


class Command(BaseCommand):
    help = "過去のメニューからメニューデータベースの初期データを作る（既定は見るだけ）"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="メニューデータベースに書き込む")

    def handle(self, *args, **opts):
        ranks = {r.name: r for r in PriceRank.objects.all()}
        by_week = defaultdict(list)
        for p in Product.objects.all():
            week = parse_week(p.week)
            if week:
                by_week[week].append(p)

        normal_price = {}
        for week, products in by_week.items():
            prices = [int(p.price_A) for p in products
                      if "★" not in p.name and "大盛り" not in p.name and p.price_A]
            normal_price[week] = Counter(prices).most_common(1)[0][0] if prices else None

        latest = {}
        first_seen = {}
        for week in sorted(by_week):
            for p in by_week[week]:
                name = normalize_name(p.name)
                if not name:
                    continue
                latest[name] = (week, p)
                first_seen.setdefault(name, week)

        rules = list(ClassifyRule.objects.select_related("rank"))
        existing = set(MenuProfile.objects.values_list("name", flat=True))
        rows, stats = [], Counter()
        for name, (week, p) in sorted(latest.items()):
            if name in existing:
                stats["skip_existing"] += 1
                continue
            confirmed = True
            if p.rank_id:
                rank = p.rank
            elif "★" in name:
                rank = ranks["特選"]
            elif "大盛り" in name:
                rank = ranks["大盛り"]
            else:
                normal = normal_price.get(week)
                price = int(p.price_A or 0)
                if normal and price and price < normal:
                    rank = ranks["お手頃"]
                else:
                    rank = ranks["通常"]
                    if not normal or price > normal:
                        confirmed = False
            container = CONTAINER_ALIASES.get(p.container_type, p.container_type)
            if container not in CONTAINER_CHOICES:
                container, confirmed = "黒容器", False
            by_rule = classify(name, rules, ranks)
            if by_rule.rank_rule and by_rule.rank != rank:
                rank, confirmed = by_rule.rank, False
                stats["rule_rank"] += 1
            if by_rule.container_rule and by_rule.container != container:
                confirmed = False
                stats["rule_container"] += 1
            rows.append(MenuProfile(name=name, rank=rank, container=container, confirmed=confirmed,
                                    first_seen=first_seen[name], last_seen=week))
            stats["confirmed" if confirmed else "needs_check"] += 1
            stats[f"rank:{rank.name}"] += 1

        if opts["apply"]:
            with transaction.atomic():
                MenuProfile.objects.bulk_create(rows)
        mode = "書き込みました" if opts["apply"] else "見るだけ（書き込みなし）"
        self.stdout.write(f"== build_menu_dictionary：{mode} ==")
        self.stdout.write(f"メニュー名 {len(latest)} 件（メニューデータベースにもうある {stats['skip_existing']} 件は変えない）")
        self.stdout.write(f"  確認済みで登録 {stats['confirmed']} 件／要確認で登録 {stats['needs_check']} 件")
        self.stdout.write(f"  判定ルールと食い違い：値段の種類 {stats['rule_rank']} 件（ルールの種類にした）／容器 {stats['rule_container']} 件")
        for name in ("特選", "通常", "お手頃", "大盛り"):
            self.stdout.write(f"  {name}：{stats[f'rank:{name}']} 件")
        for row in rows:
            if not row.confirmed:
                self.stdout.write(f"    要確認：{row.name}（{row.rank.name}・{row.container}・最後に出た週 {row.last_seen}）")
