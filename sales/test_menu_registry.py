"""S4-1：メニュー辞書・判定ルール・週の登録のテスト。"""
import datetime
import io

from django.core.management import call_command
from django.test import TestCase

from sales.menu_registry import classify, register_week, week_state
from sales.models import ClassifyRule, MenuProfile, MenuWeekCheck, PriceRank, Product

WEEK = datetime.date(2026, 10, 14)  # 水曜
NAMES = ["★カキフライタルタルソース", "イタリア風ポーク煮込み", "サバの竜田揚げ香味ソース", "ハンバーグ和風きのこソース",
         "豚さんでチンジャオロースー", "チキンカツハニーマスタードソース", "チキンごま味噌唐揚げ", "鶏のパイタンあんかけ",
         "ガパオライス", "五目チャーハン"]


class ClassifyTest(TestCase):
    """初期ルール（0088）：種類と容器は別々に判定する。"""

    def check(self, name, rank, container):
        c = classify(name)
        self.assertEqual((c.rank.name, c.container), (rank, container), name)

    def test_initial_rules(self):
        self.check("★カキフライタルタルソース", "特選", "赤容器")
        self.check("五目チャーハン", "お手頃", "一体型")
        self.check("ふわとろオムライス", "お手頃", "一体型")
        self.check("ガパオライス", "通常", "一体型")      # 一体型でも通常の値段
        self.check("タコライス", "通常", "一体型")
        self.check("大盛りごはん", "大盛り", "ご飯容器")
        self.check("唐揚げ弁当", "通常", "黒容器")        # どれにも当たらない

    def test_rule_order_first_match(self):
        """上から順に見て最初に当たったルールを使う（★のついたチャーハンは特選・赤容器）。"""
        self.check("★海鮮チャーハン", "特選", "赤容器")
        ClassifyRule.objects.filter(keyword="チャーハン").update(sort_order=0)
        self.check("★海鮮チャーハン", "お手頃", "一体型")


class RegisterWeekTest(TestCase):
    def test_register_new_week(self):
        result = register_week(WEEK, NAMES, now=None)
        products = {p.no: p for p in Product.objects.filter(week="2026-10-14")}
        self.assertEqual(len(products), 11)
        self.assertEqual(products[11].name, "大盛りごはん")
        self.assertEqual((products[10].rank.name, products[10].container_type), ("お手頃", "一体型"))
        self.assertEqual((products[9].rank.name, products[9].container_type), ("通常", "一体型"))
        # 週の初日の価格表（2026-10-01版）の値段も入れておく
        self.assertEqual((int(products[1].price_A), int(products[1].price_B), int(products[1].price_C)), (750, 700, 700))
        self.assertEqual(len(result.needs_check), 10)  # 辞書が空なので①〜⑩は全部「要確認」（大盛りごはんは除く）
        self.assertTrue(MenuProfile.objects.get(name="大盛りごはん").confirmed)
        check = MenuWeekCheck.objects.get(week=WEEK)
        self.assertFalse(check.is_confirmed)
        self.assertEqual(check.received_count, 11)

    def test_dictionary_is_used_and_remembered(self):
        tsujo = PriceRank.objects.get(name="通常")
        MenuProfile.objects.create(name="五目チャーハン", rank=tsujo, container="一体型", confirmed=True)
        result = register_week(WEEK, NAMES)
        self.assertNotIn("五目チャーハン", result.needs_check)
        self.assertEqual(Product.objects.get(week="2026-10-14", no=10).rank, tsujo)  # ルールより辞書を優先
        self.assertEqual(MenuProfile.objects.get(name="五目チャーハン").last_seen, WEEK)

    def test_resend_resets_confirmation_and_overwrites(self):
        register_week(WEEK, NAMES)
        MenuWeekCheck.objects.filter(week=WEEK).update(confirmed_at=datetime.datetime(2026, 10, 9, 10, 0,
                                                                                      tzinfo=datetime.timezone.utc))
        names = NAMES[:9] + ["タコライス"]
        result = register_week(WEEK, names)
        self.assertTrue(result.resent_after_confirm)
        self.assertEqual(Product.objects.filter(week="2026-10-14").count(), 11)
        self.assertEqual(Product.objects.get(week="2026-10-14", no=10).name, "タコライス")
        self.assertFalse(MenuWeekCheck.objects.get(week=WEEK).is_confirmed)

    def test_week_state(self):
        self.assertEqual(week_state(WEEK, datetime.date(2026, 10, 9)), "upcoming")
        self.assertEqual(week_state(WEEK, datetime.date(2026, 10, 20)), "selling")
        self.assertEqual(week_state(WEEK, datetime.date(2026, 10, 21)), "ended")


class BuildMenuDictionaryTest(TestCase):
    def setUp(self):
        def p(no, name, price_a, container, week="2026-09-30"):
            Product.objects.create(week=week, no=no, name=name, price_A=price_a, price_B=0, price_C=0,
                                   container_type=container)
        p(1, "★エビチリ", 750, "赤容器")
        p(2, "唐揚げ", 700, "黒容器")
        p(3, "生姜焼き", 700, "黒容器など")
        p(10, "五目チャーハン", 650, "一体型")
        p(9, "高い唐揚げ", 720, "黒容器")
        p(11, "大盛りごはん", 50, "ごはん容器")

    def run_command(self, *args):
        out = io.StringIO()
        call_command("build_menu_dictionary", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_and_apply(self):
        out = self.run_command()
        self.assertIn("見るだけ", out)
        self.assertEqual(MenuProfile.objects.count(), 0)
        self.run_command("--apply")
        got = {m.name: (m.rank.name, m.container, m.confirmed) for m in MenuProfile.objects.all()}
        self.assertEqual(got["★エビチリ"], ("特選", "赤容器", True))
        self.assertEqual(got["生姜焼き"], ("通常", "黒容器", True))      # 表記ゆれをそろえる
        self.assertEqual(got["五目チャーハン"], ("お手頃", "一体型", True))
        self.assertEqual(got["高い唐揚げ"], ("通常", "黒容器", False))   # 通常より高い＝決めきれない
        self.assertEqual(got["大盛りごはん"], ("大盛り", "ご飯容器", True))

    def test_existing_profiles_are_kept(self):
        otegoro = PriceRank.objects.get(name="お手頃")
        MenuProfile.objects.create(name="唐揚げ", rank=otegoro, container="一体型", confirmed=True)
        self.run_command("--apply")
        self.assertEqual(MenuProfile.objects.get(name="唐揚げ").rank, otegoro)
