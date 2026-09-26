"""拼版核心算法单元测试（仅依赖标准库，可直接 `python -m unittest` 运行）。"""

import itertools
import unittest

from app.imposition import (
    BindingCheckError,
    ChapterTooLargeError,
    _ceil_to_multiple_of_four,
    build_binding_check,
    build_chapter_imposition,
    build_color_plan,
    build_imposition,
    build_manual_duplex,
    build_reprint,
    impose_padded_booklet,
    split_booklets,
    split_chapter_booklets,
)


class TestSplitBooklets(unittest.TestCase):
    def test_single_booklet_within_capacity(self):
        ranges = split_booklets(total_pages=10, max_sheets_per_booklet=4)
        self.assertEqual(len(ranges), 1)
        self.assertEqual((ranges[0].start_page, ranges[0].end_page), (1, 10))
        self.assertEqual(ranges[0].padded_pages, 12)
        self.assertEqual(ranges[0].blank_pages, 2)
        self.assertEqual(ranges[0].sheet_count, 3)

    def test_exact_multiple_of_four_no_padding(self):
        ranges = split_booklets(total_pages=16, max_sheets_per_booklet=4)
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0].padded_pages, 16)
        self.assertEqual(ranges[0].blank_pages, 0)

    def test_multiple_booklets_only_last_is_padded(self):
        # 每册 2 张纸 = 8 页；17 页 -> 8 + 8 + 1
        ranges = split_booklets(total_pages=17, max_sheets_per_booklet=2)
        self.assertEqual(len(ranges), 3)
        self.assertEqual((ranges[0].start_page, ranges[0].end_page), (1, 8))
        self.assertEqual(ranges[0].padded_pages, 8)
        self.assertEqual(ranges[0].blank_pages, 0)
        self.assertEqual((ranges[1].start_page, ranges[1].end_page), (9, 16))
        self.assertEqual(ranges[1].padded_pages, 8)
        self.assertEqual(ranges[1].blank_pages, 0)
        self.assertEqual((ranges[2].start_page, ranges[2].end_page), (17, 17))
        self.assertEqual(ranges[2].padded_pages, 4)
        self.assertEqual(ranges[2].blank_pages, 3)
        self.assertEqual(ranges[0].index, 0)
        self.assertEqual(ranges[2].index, 2)

    def test_one_page_padded_to_four(self):
        ranges = split_booklets(total_pages=1, max_sheets_per_booklet=1)
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0].padded_pages, 4)
        self.assertEqual(ranges[0].sheet_count, 1)

    def test_capacity_equal_multiple_of_four(self):
        # 总页数恰好装满两册
        ranges = split_booklets(total_pages=16, max_sheets_per_booklet=2)
        self.assertEqual(len(ranges), 2)
        self.assertEqual([r.original_pages for r in ranges], [8, 8])


class TestImposePaddedBooklet(unittest.TestCase):
    def test_four_sheets_classic_layout(self):
        # 16 页 / 4 张纸的经典骑马钉拼版
        sheets = impose_padded_booklet(16)
        self.assertEqual(len(sheets), 4)
        # 最外一张：正面 16|1，反面 2|15
        self.assertEqual(sheets[0]["front"], {"left": 16, "right": 1})
        self.assertEqual(sheets[0]["back"], {"left": 2, "right": 15})
        # 第二张：正面 14|3，反面 4|13
        self.assertEqual(sheets[1]["front"], {"left": 14, "right": 3})
        self.assertEqual(sheets[1]["back"], {"left": 4, "right": 13})
        # 第三张：正面 12|5，反面 6|11
        self.assertEqual(sheets[2]["front"], {"left": 12, "right": 5})
        self.assertEqual(sheets[2]["back"], {"left": 6, "right": 11})
        # 最内一张：正面 10|7，反面 8|9
        self.assertEqual(sheets[3]["front"], {"left": 10, "right": 7})
        self.assertEqual(sheets[3]["back"], {"left": 8, "right": 9})

    def test_single_sheet_padded(self):
        # 4 页 1 张纸
        sheets = impose_padded_booklet(4)
        self.assertEqual(len(sheets), 1)
        self.assertEqual(sheets[0]["front"], {"left": 4, "right": 1})
        self.assertEqual(sheets[0]["back"], {"left": 2, "right": 3})

    def test_invalid_inputs(self):
        for bad in (0, -4, 6, 10):
            with self.assertRaises(ValueError):
                impose_padded_booklet(bad)


def _reading_order(sheets):
    """模拟对折后翻阅顺序：按纸张从外到内，依次取正面左、反面左、
    反面右、正面右（骑马钉嵌套书帖的物理翻阅序）。"""
    order = []
    for sheet in sheets:
        order.extend(
            [
                sheet["front"]["left"],
                sheet["back"]["left"],
                sheet["back"]["right"],
                sheet["front"]["right"],
            ]
        )
    return order


class TestFoldingReadingOrder(unittest.TestCase):
    """对折后的核心不变量：正反页码互补为 n+1，且偶数页始终在左、奇数页在右。"""

    def test_pair_sums_and_parity(self):
        for n in (4, 8, 12, 16, 20, 40):
            sheets = impose_padded_booklet(n)
            seen = set()
            for sheet in sheets:
                for side in ("front", "back"):
                    left, right = sheet[side]["left"], sheet[side]["right"]
                    self.assertEqual(left + right, n + 1, f"n={n} 左右页码之和应为 n+1")
                    self.assertEqual(left % 2, 0, "左页必为偶数页")
                    self.assertEqual(right % 2, 1, "右页必为奇数页")
                    seen.update((left, right))
            self.assertEqual(seen, set(range(1, n + 1)), f"n={n} 所有版面应恰好出现一次")

    def test_reading_order_of_a_sheet(self):
        # 单张纸（4 页）对折后翻阅顺序为 4,2,3,1：
        # 背面封面 4 -> 内左 2 -> 内右 3 -> 封面 1。
        # 内页正文顺序即 1(封面) -> 2 -> 3 -> 4(封底)。
        sheets = impose_padded_booklet(4)
        self.assertEqual(_reading_order(sheets), [4, 2, 3, 1])


class TestBuildImposition(unittest.TestCase):
    def test_16_pages_2_sheets_per_booklet_splits_and_maps_original_pages(self):
        result = build_imposition(total_pages=16, max_sheets_per_booklet=2)
        self.assertEqual(result["booklet_count"], 2)
        self.assertEqual(result["total_sheets"], 4)

        first = result["booklets"][0]
        self.assertEqual((first["start_page"], first["end_page"]), (1, 8))
        # 第 1 册 8 页 / 2 张纸
        self.assertEqual(first["sheets"][0]["front"], {"left": 8, "right": 1})
        self.assertEqual(first["sheets"][0]["back"], {"left": 2, "right": 7})
        self.assertEqual(first["sheets"][1]["front"], {"left": 6, "right": 3})
        self.assertEqual(first["sheets"][1]["back"], {"left": 4, "right": 5})

        second = result["booklets"][1]
        # 第 2 册页码从原稿第 9 页重新映射
        self.assertEqual((second["start_page"], second["end_page"]), (9, 16))
        self.assertEqual(second["sheets"][0]["front"], {"left": 16, "right": 9})
        self.assertEqual(second["sheets"][0]["back"], {"left": 10, "right": 15})

    def test_padding_blanks_are_null_and_contiguous_at_inner_end(self):
        # 5 页、1 张纸/册：仅 1 册，补齐到 8 页，3 个空白
        result = build_imposition(total_pages=5, max_sheets_per_booklet=10)
        booklet = result["booklets"][0]
        self.assertEqual(booklet["blank_pages"], 3)
        self.assertEqual(booklet["padded_pages"], 8)
        self.assertEqual(booklet["sheet_count"], 2)
        # 外纸：8(空)|1 / 2|7(空)
        self.assertEqual(booklet["sheets"][0]["front"], {"left": None, "right": 1})
        self.assertEqual(booklet["sheets"][0]["back"], {"left": 2, "right": None})
        # 内纸：6(空)|3 / 4|5
        self.assertEqual(booklet["sheets"][1]["front"], {"left": None, "right": 3})
        self.assertEqual(booklet["sheets"][1]["back"], {"left": 4, "right": 5})

    def test_single_page_booklet(self):
        result = build_imposition(total_pages=1, max_sheets_per_booklet=1)
        booklet = result["booklets"][0]
        self.assertEqual(booklet["blank_pages"], 3)
        # 4|1(原稿1) / 2(空)|3(空)
        self.assertEqual(booklet["sheets"][0]["front"], {"left": None, "right": 1})
        self.assertEqual(booklet["sheets"][0]["back"], {"left": None, "right": None})

    def test_every_original_page_appears_exactly_once(self):
        for total, cap in ((1, 1), (5, 2), (17, 2), (33, 3), (100, 4)):
            result = build_imposition(total_pages=total, max_sheets_per_booklet=cap)
            pages = []
            for booklet in result["booklets"]:
                for sheet in booklet["sheets"]:
                    for side in ("front", "back"):
                        for pos in ("left", "right"):
                            value = sheet[side][pos]
                            if value is not None:
                                pages.append(value)
            self.assertEqual(
                sorted(pages),
                list(range(1, total + 1)),
                f"total={total}, cap={cap}",
            )

    def test_summary_fields(self):
        result = build_imposition(total_pages=17, max_sheets_per_booklet=2)
        self.assertEqual(result["total_pages"], 17)
        self.assertEqual(result["max_sheets_per_booklet"], 2)
        self.assertEqual(result["booklet_count"], 3)
        self.assertEqual(result["total_sheets"], 2 + 2 + 1)


class TestSplitChapterBooklets(unittest.TestCase):
    def test_adjacent_chapters_merged_within_capacity(self):
        # 容量 8 页：[6,5,3] -> 第 1 章单独成册，第 2、3 章合册（6+5=11 > 8 不能三合）
        ranges = split_chapter_booklets([6, 5, 3], max_sheets_per_booklet=2)
        self.assertEqual(
            [(r.start_chapter, r.end_chapter) for r in ranges], [(1, 1), (2, 3)]
        )
        self.assertEqual(
            [(r.start_page, r.end_page) for r in ranges], [(1, 6), (7, 14)]
        )
        self.assertEqual(ranges[0].padded_pages, 8)
        self.assertEqual(ranges[0].blank_pages, 2)
        self.assertEqual(ranges[0].sheet_count, 2)
        self.assertEqual(ranges[1].padded_pages, 8)
        self.assertEqual(ranges[1].blank_pages, 0)
        self.assertEqual([r.index for r in ranges], [0, 1])

    def test_single_chapter_per_booklet_when_nothing_merges(self):
        # 每章 6 页、容量 8：任何两章都超容量，只能各自成册
        ranges = split_chapter_booklets([6, 6, 6], max_sheets_per_booklet=2)
        self.assertEqual(
            [(r.start_chapter, r.end_chapter) for r in ranges],
            [(1, 1), (2, 2), (3, 3)],
        )

    def test_total_sheets_minimized_before_booklet_count(self):
        # [2,2,2] 容量 8：合成一册 6 页 2 张纸，与拆成两册同为 2 张纸，
        # 但册数更少 -> 选一册方案
        ranges = split_chapter_booklets([2, 2, 2], max_sheets_per_booklet=2)
        self.assertEqual(len(ranges), 1)
        self.assertEqual((ranges[0].start_chapter, ranges[0].end_chapter), (1, 3))
        self.assertEqual(ranges[0].sheet_count, 2)

    def test_total_sheets_beats_greedy_fill(self):
        # [4,1,1,4,1,1] 容量 8：顺序装满得 [4,1,1][4,1,1] 共 4 张纸；
        # 最优为 [4][1,1,4,1,1] 共 1+2=3 张纸
        ranges = split_chapter_booklets([4, 1, 1, 4, 1, 1], max_sheets_per_booklet=2)
        self.assertEqual(sum(r.sheet_count for r in ranges), 3)
        self.assertEqual(
            [(r.start_chapter, r.end_chapter) for r in ranges], [(1, 1), (2, 6)]
        )

    def test_ending_chapter_sequence_lexicographic_tie_break(self):
        # [4,1,4] 容量 8：[4][1,4] 与 [4,1][4] 同为 3 张纸、2 册，
        # 结束章节序号序列 [1,3] < [2,3] -> 选 [4][1,4]
        ranges = split_chapter_booklets([4, 1, 4], max_sheets_per_booklet=2)
        self.assertEqual(sum(r.sheet_count for r in ranges), 3)
        self.assertEqual(
            [(r.start_chapter, r.end_chapter) for r in ranges], [(1, 1), (2, 3)]
        )

    def test_chapter_exceeding_capacity_raises(self):
        # 容量 8 页，第 2 章 9 页不可拆分 -> 抛错
        with self.assertRaises(ChapterTooLargeError) as ctx:
            split_chapter_booklets([4, 9, 4], max_sheets_per_booklet=2)
        self.assertEqual(ctx.exception.chapter, 2)
        self.assertEqual(ctx.exception.pages, 9)
        self.assertEqual(ctx.exception.capacity, 8)

    def test_exact_capacity_chapter_fits(self):
        ranges = split_chapter_booklets([8, 4], max_sheets_per_booklet=2)
        self.assertEqual(
            [(r.start_chapter, r.end_chapter) for r in ranges], [(1, 1), (2, 2)]
        )
        self.assertEqual(ranges[0].blank_pages, 0)


class TestBuildChapterImposition(unittest.TestCase):
    def test_booklet_fields_and_global_page_ranges(self):
        result = build_chapter_imposition([6, 5, 3], max_sheets_per_booklet=2)
        self.assertEqual(result["chapter_pages"], [6, 5, 3])
        self.assertEqual(result["chapter_count"], 3)
        self.assertEqual(result["total_pages"], 14)
        self.assertEqual(result["booklet_count"], 2)
        self.assertEqual(result["total_sheets"], 4)
        self.assertEqual(result["total_blank_pages"], 2)

        first, second = result["booklets"]
        self.assertEqual((first["start_chapter"], first["end_chapter"]), (1, 1))
        self.assertEqual((first["start_page"], first["end_page"]), (1, 6))
        self.assertEqual(first["original_pages"], 6)
        self.assertEqual(first["padded_pages"], 8)
        self.assertEqual(first["blank_pages"], 2)
        self.assertEqual(first["sheet_count"], 2)
        self.assertEqual((second["start_chapter"], second["end_chapter"]), (2, 3))
        self.assertEqual((second["start_page"], second["end_page"]), (7, 14))
        self.assertEqual(second["blank_pages"], 0)

    def test_sheets_use_global_pages_and_null_blanks(self):
        result = build_chapter_imposition([6, 5, 3], max_sheets_per_booklet=2)
        first = result["booklets"][0]
        # 第 1 册 6 页补齐到 8：外纸 8(空)|1 / 2|7(空)
        self.assertEqual(first["sheets"][0]["front"], {"left": None, "right": 1})
        self.assertEqual(first["sheets"][0]["back"], {"left": 2, "right": None})
        self.assertEqual(first["sheets"][1]["front"], {"left": 6, "right": 3})
        self.assertEqual(first["sheets"][1]["back"], {"left": 4, "right": 5})
        # 第 2 册 8 页，页码为全局原稿页码 7..14
        second = result["booklets"][1]
        self.assertEqual(second["sheets"][0]["front"], {"left": 14, "right": 7})
        self.assertEqual(second["sheets"][0]["back"], {"left": 8, "right": 13})
        self.assertEqual(second["sheets"][1]["front"], {"left": 12, "right": 9})
        self.assertEqual(second["sheets"][1]["back"], {"left": 10, "right": 11})

    def test_every_original_page_appears_exactly_once(self):
        cases = (
            ([1], 1),
            ([4, 1, 4], 2),
            ([6, 5, 3], 2),
            ([3, 3, 3, 3], 2),
            ([2, 7, 1, 4, 8], 3),
        )
        for chapters, cap in cases:
            result = build_chapter_imposition(chapters, cap)
            pages = []
            for booklet in result["booklets"]:
                for sheet in booklet["sheets"]:
                    for side in ("front", "back"):
                        for pos in ("left", "right"):
                            value = sheet[side][pos]
                            if value is not None:
                                pages.append(value)
            self.assertEqual(
                sorted(pages),
                list(range(1, sum(chapters) + 1)),
                f"chapters={chapters}, cap={cap}",
            )

    def test_single_chapter_too_large_raises(self):
        with self.assertRaises(ChapterTooLargeError):
            build_chapter_imposition([2, 9], max_sheets_per_booklet=2)


class TestBuildColorPlan(unittest.TestCase):
    """彩印 / 黑白混合计费的最优分册。"""

    def test_no_color_ranges_all_bw(self):
        # 8 页容量 8：无彩色页时全部黑白；[8] 与 [4,4] 同为 2 张纸，
        # 册数更少 -> 选一册方案
        result = build_color_plan(8, 2, None, 50, 10)
        self.assertEqual(result["total_pages"], 8)
        self.assertEqual(result["max_sheets_per_booklet"], 2)
        self.assertEqual(result["color_ranges"], [])
        self.assertEqual(result["booklet_count"], 1)
        self.assertEqual(result["total_sheets"], 2)
        self.assertEqual(result["color_sheet_count"], 0)
        self.assertEqual(result["bw_sheet_count"], 2)
        self.assertEqual(result["total_cost"], 20)
        booklet = result["booklets"][0]
        self.assertEqual((booklet["start_page"], booklet["end_page"]), (1, 8))
        self.assertEqual(booklet["cost"], 20)
        self.assertEqual(
            [s["print_mode"] for s in booklet["sheets"]], ["bw", "bw"]
        )
        self.assertEqual([s["price"] for s in booklet["sheets"]], [10, 10])

    def test_empty_color_ranges_equivalent_to_none(self):
        empty = build_color_plan(8, 2, [], 50, 10)
        none = build_color_plan(8, 2, None, 50, 10)
        self.assertEqual(empty["total_cost"], none["total_cost"])
        self.assertEqual(
            [(b["start_page"], b["end_page"]) for b in empty["booklets"]],
            [(b["start_page"], b["end_page"]) for b in none["booklets"]],
        )
        self.assertEqual(empty["color_ranges"], [])

    def test_color_sheet_detection_within_one_booklet(self):
        # 16 页 1 册 4 张纸，彩色 3..6 -> 第 2、3 张纸彩印（彩印更便宜时不拆册）
        result = build_color_plan(16, 4, [(3, 6)], 5, 10)
        self.assertEqual(result["booklet_count"], 1)
        booklet = result["booklets"][0]
        self.assertEqual(
            [s["print_mode"] for s in booklet["sheets"]],
            ["bw", "color", "color", "bw"],
        )
        self.assertEqual(booklet["color_sheet_count"], 2)
        self.assertEqual(booklet["cost"], 2 * 5 + 2 * 10)
        self.assertEqual(result["total_cost"], 30)
        self.assertEqual(result["color_sheet_count"], 2)
        self.assertEqual(result["bw_sheet_count"], 2)
        # 每张纸给出单价与正反面页码
        self.assertEqual(booklet["sheets"][1]["price"], 5)
        self.assertEqual(booklet["sheets"][1]["front"], {"left": 14, "right": 3})
        self.assertEqual(booklet["sheets"][1]["back"], {"left": 4, "right": 13})

    def test_cost_driven_split_isolates_color_pages(self):
        # 16 页、彩色 3..6、彩印 50 / 黑白 10：
        # 一册方案 2 彩 2 黑白 = 120；[1..8][9..16] 把彩色页集中到 1 张纸，
        # 1 彩 3 黑白 = 80 -> 拆册更省
        result = build_color_plan(16, 4, [(3, 6)], 50, 10)
        self.assertEqual(result["total_cost"], 80)
        self.assertEqual(result["booklet_count"], 2)
        self.assertEqual(
            [(b["start_page"], b["end_page"]) for b in result["booklets"]],
            [(1, 8), (9, 16)],
        )
        first, second = result["booklets"]
        self.assertEqual([s["print_mode"] for s in first["sheets"]], ["bw", "color"])
        self.assertEqual(first["cost"], 10 + 50)
        self.assertEqual([s["print_mode"] for s in second["sheets"]], ["bw", "bw"])
        self.assertEqual(second["cost"], 20)
        # 总费用等于各册费用之和
        self.assertEqual(
            result["total_cost"], sum(b["cost"] for b in result["booklets"])
        )

    def test_cheaper_color_price_favors_more_color_sheets(self):
        # 8 页、彩色 {1, 8}、彩印 5 / 黑白 10：
        # [8] 为 1 彩 1 黑白 = 15；[4,4] 两册各 1 彩 = 10 -> 选 [4,4]
        result = build_color_plan(8, 2, [(1, 1), (8, 8)], 5, 10)
        self.assertEqual(result["total_cost"], 10)
        self.assertEqual(
            [(b["start_page"], b["end_page"]) for b in result["booklets"]],
            [(1, 4), (5, 8)],
        )
        self.assertEqual(result["color_sheet_count"], 2)

    def test_overlapping_ranges_charged_once(self):
        # 交叠区间取并集：[(1,4),(3,6)] 与 [(1,6)] 完全等价
        merged = build_color_plan(16, 4, [(1, 6)], 5, 10)
        overlapped = build_color_plan(16, 4, [(1, 4), (3, 6)], 5, 10)
        self.assertEqual(overlapped["total_cost"], merged["total_cost"])
        self.assertEqual(
            [s["print_mode"] for b in overlapped["booklets"] for s in b["sheets"]],
            [s["print_mode"] for b in merged["booklets"] for s in b["sheets"]],
        )
        # 并集 1..6 -> 前三张纸彩印，彩印更便宜 -> 一册方案 3 彩 1 黑白 = 25
        self.assertEqual(overlapped["total_cost"], 25)
        self.assertEqual(overlapped["color_sheet_count"], 3)
        self.assertEqual(
            overlapped["color_ranges"],
            [{"start_page": 1, "end_page": 4}, {"start_page": 3, "end_page": 6}],
        )

    def test_blank_padding_pages_never_color(self):
        # 5 页、彩色第 5 页：一册补齐到 8 页，外纸只有 1、2 两个非空页 -> 黑白；
        # 内纸含第 5 页 -> 彩印（补白页不计费）
        result = build_color_plan(5, 10, [(5, 5)], 50, 10)
        self.assertEqual(result["booklet_count"], 1)
        booklet = result["booklets"][0]
        self.assertEqual(booklet["blank_pages"], 3)
        self.assertEqual(
            [s["print_mode"] for s in booklet["sheets"]], ["bw", "color"]
        )
        self.assertEqual(result["total_cost"], 60)
        # 补白仍以 None 表示
        self.assertIsNone(booklet["sheets"][0]["front"]["left"])
        self.assertIsNone(booklet["sheets"][0]["back"]["right"])

    def test_tie_breaks_sheets_then_booklets_then_end_pages(self):
        # 6 页、每册最多 1 张纸（容量 4 页）、无彩色页：
        # [1..2][3..6]、[1..3][4..6]、[1..4][5..6] 同为 2 张纸、2 册，
        # 末页序列 (2,6) < (3,6) < (4,6) -> 选 [1..2][3..6]
        result = build_color_plan(6, 1, None, 50, 10)
        self.assertEqual(result["total_sheets"], 2)
        self.assertEqual(result["booklet_count"], 2)
        self.assertEqual(
            [(b["start_page"], b["end_page"]) for b in result["booklets"]],
            [(1, 2), (3, 6)],
        )

    def test_every_original_page_appears_exactly_once(self):
        for total, cap, ranges in (
            (1, 1, None),
            (5, 2, [(2, 4)]),
            (17, 2, [(1, 17)]),
            (33, 3, [(5, 9), (8, 12), (30, 33)]),
            (100, 4, [(1, 50)]),
        ):
            result = build_color_plan(total, cap, ranges, 7, 3)
            pages = []
            for booklet in result["booklets"]:
                for sheet in booklet["sheets"]:
                    for side in ("front", "back"):
                        for pos in ("left", "right"):
                            value = sheet[side][pos]
                            if value is not None:
                                pages.append(value)
            self.assertEqual(
                sorted(pages),
                list(range(1, total + 1)),
                f"total={total}, cap={cap}, ranges={ranges}",
            )
            # 费用与纸张数账目自洽
            self.assertEqual(
                result["total_cost"], sum(b["cost"] for b in result["booklets"])
            )
            self.assertEqual(
                result["total_sheets"],
                result["color_sheet_count"] + result["bw_sheet_count"],
            )

    def test_all_pages_color(self):
        result = build_color_plan(8, 2, [(1, 8)], 50, 10)
        self.assertEqual(result["color_sheet_count"], result["total_sheets"])
        self.assertEqual(result["bw_sheet_count"], 0)
        self.assertEqual(result["total_cost"], 2 * 50)

    def test_summary_fields_echo_request(self):
        result = build_color_plan(10, 3, [(2, 3)], 40, 8)
        self.assertEqual(result["total_pages"], 10)
        self.assertEqual(result["max_sheets_per_booklet"], 3)
        self.assertEqual(result["color_price_per_sheet"], 40)
        self.assertEqual(result["bw_price_per_sheet"], 8)
        self.assertEqual(
            result["color_ranges"], [{"start_page": 2, "end_page": 3}]
        )

    def test_matches_brute_force_enumeration(self):
        # 小规模随机用例与穷举所有合法分册的暴力解交叉验证：
        # (总费用, 总纸张数, 册数, 末页页码序列) 必须一致。
        import random

        def brute(total, capacity, color_pages, color_price, bw_price):
            def sheet_cost(start, end):
                original = end - start + 1
                padded = _ceil_to_multiple_of_four(original)
                sheets = padded // 4
                color_sheets = 0
                for k in range(sheets):
                    positions = (
                        padded - 2 * k, 1 + 2 * k, 2 + 2 * k, padded - 1 - 2 * k,
                    )
                    if any(
                        p <= original and (start + p - 1) in color_pages
                        for p in positions
                    ):
                        color_sheets += 1
                return (
                    color_sheets * color_price + (sheets - color_sheets) * bw_price,
                    sheets,
                )

            best = None

            def rec(start, ends, cost, sheets):
                nonlocal best
                if start > total:
                    key = (cost, sheets, len(ends), tuple(ends))
                    if best is None or key < best:
                        best = key
                    return
                for end in range(start, min(total, start + capacity - 1) + 1):
                    c, s = sheet_cost(start, end)
                    rec(end + 1, ends + [end], cost + c, sheets + s)

            rec(1, [], 0, 0)
            return best

        rng = random.Random(20260926)
        for total in range(1, 11):
            for max_sheets in (1, 2, 3):
                for _ in range(20):
                    ranges = []
                    for _ in range(rng.randint(0, 3)):
                        a = rng.randint(1, total)
                        ranges.append((a, rng.randint(a, total)))
                    color_price = rng.randint(1, 9)
                    bw_price = rng.randint(1, 9)
                    color_pages = {
                        p for a, b in ranges for p in range(a, b + 1)
                    }
                    expected = brute(
                        total, 4 * max_sheets, color_pages, color_price, bw_price
                    )
                    result = build_color_plan(
                        total, max_sheets, ranges, color_price, bw_price
                    )
                    got = (
                        result["total_cost"],
                        result["total_sheets"],
                        result["booklet_count"],
                        tuple(b["end_page"] for b in result["booklets"]),
                    )
                    self.assertEqual(
                        got,
                        expected,
                        f"total={total}, max_sheets={max_sheets}, "
                        f"ranges={ranges}, prices=({color_price}, {bw_price})",
                    )


class TestBuildReprint(unittest.TestCase):
    def test_single_damaged_page_locates_outer_sheet(self):
        # 16 页 1 册 4 张纸：第 1 页位于最外一张正面右
        result = build_reprint(16, 4, [(1, 1)])
        self.assertEqual(result["reprint_sheet_count"], 1)
        self.assertEqual(result["booklet_count"], 1)
        sheet = result["sheets"][0]
        self.assertEqual((sheet["booklet_index"], sheet["sheet_index"]), (0, 0))
        self.assertEqual(sheet["front"], {"left": 16, "right": 1})
        self.assertEqual(sheet["back"], {"left": 2, "right": 15})
        self.assertEqual(sheet["damaged_pages"], [1])

    def test_range_spanning_multiple_sheets_and_sides(self):
        # 16 页：受损 1..4 -> 外纸（1,2）与第二张（3,4）
        result = build_reprint(16, 4, [(1, 4)])
        self.assertEqual(result["reprint_sheet_count"], 2)
        self.assertEqual(
            [(s["booklet_index"], s["sheet_index"]) for s in result["sheets"]],
            [(0, 0), (0, 1)],
        )
        self.assertEqual(result["sheets"][0]["damaged_pages"], [1, 2])
        self.assertEqual(result["sheets"][1]["damaged_pages"], [3, 4])

    def test_overlapping_ranges_dedupe_sheets_and_pages(self):
        result = build_reprint(16, 4, [(1, 3), (2, 4)])
        # 并集仍为 1..4，落在外两张纸；交叠区间不产生重复纸张
        self.assertEqual(result["reprint_sheet_count"], 2)
        self.assertEqual(result["sheets"][0]["damaged_pages"], [1, 2])
        self.assertEqual(result["sheets"][1]["damaged_pages"], [3, 4])

    def test_sorting_outer_to_inner(self):
        # 16 页：第 11 页落在第三张（index 2），第 9 页落在最内一张（index 3）
        result = build_reprint(16, 4, [(11, 11), (9, 9)])
        self.assertEqual(
            [(s["sheet_index"], s["damaged_pages"]) for s in result["sheets"]],
            [(2, [11]), (3, [9])],
        )

    def test_multi_booklet_sorting_and_indices(self):
        # 17 页、每册 2 张纸（8 页）-> 3 册：受损第 8 页（册0最外纸）与第 9 页（册1最外纸）
        result = build_reprint(17, 2, [(8, 9)])
        self.assertEqual(result["booklet_count"], 3)
        self.assertEqual(result["reprint_sheet_count"], 2)
        first, second = result["sheets"]
        self.assertEqual((first["booklet_index"], first["sheet_index"]), (0, 0))
        self.assertEqual(first["damaged_pages"], [8])
        self.assertEqual((second["booklet_index"], second["sheet_index"]), (1, 0))
        self.assertEqual(second["damaged_pages"], [9])
        # 最后一册存在补白，但不受损、不出现在结果中
        self.assertTrue(all(s["booklet_index"] != 2 for s in result["sheets"]))

    def test_padding_blanks_are_null_but_never_listed_as_damaged(self):
        # 5 页补齐到 8：受损第 5 页 -> 内纸（index 1），外封补白不受影响
        result = build_reprint(5, 10, [(5, 5)])
        self.assertEqual(result["reprint_sheet_count"], 1)
        sheet = result["sheets"][0]
        self.assertEqual(sheet["sheet_index"], 1)
        self.assertEqual(sheet["front"], {"left": None, "right": 3})
        self.assertEqual(sheet["back"], {"left": 4, "right": 5})
        self.assertEqual(sheet["damaged_pages"], [5])

    def test_all_pages_damaged_returns_every_sheet_once(self):
        result = build_reprint(17, 2, [(1, 17)])
        self.assertEqual(result["reprint_sheet_count"], 2 + 2 + 1)
        # 结果按分册序号、纸张从外到内
        keys = [(s["booklet_index"], s["sheet_index"]) for s in result["sheets"]]
        self.assertEqual(
            keys,
            [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0)],
        )
        # 每个原稿页都恰好出现在某张纸的 damaged_pages 中一次
        listed = [p for s in result["sheets"] for p in s["damaged_pages"]]
        self.assertEqual(sorted(listed), list(range(1, 18)))

    def test_summary_fields(self):
        result = build_reprint(16, 4, [(15, 16)])
        self.assertEqual(result["total_pages"], 16)
        self.assertEqual(result["max_sheets_per_booklet"], 4)


def _orient(sheet, swap_sides: bool, swap_left_right: bool):
    """对一张纸施加正反互换 / 左右互换，返回新的纸张字典。"""
    fl = sheet["front"]["left"]
    fr = sheet["front"]["right"]
    bl = sheet["back"]["left"]
    br = sheet["back"]["right"]
    if swap_sides:
        fl, fr, bl, br = bl, br, fl, fr
    if swap_left_right:
        fl, fr, bl, br = fr, fl, br, bl
    return {"front": {"left": fl, "right": fr}, "back": {"left": bl, "right": br}}


class TestBuildBindingCheck(unittest.TestCase):
    """装订核对：基准为 16 页 1 册 4 张纸。"""

    S0 = {"front": {"left": 16, "right": 1}, "back": {"left": 2, "right": 15}}
    S1 = {"front": {"left": 14, "right": 3}, "back": {"left": 4, "right": 13}}
    S2 = {"front": {"left": 12, "right": 5}, "back": {"left": 6, "right": 11}}
    S3 = {"front": {"left": 10, "right": 7}, "back": {"left": 8, "right": 9}}
    BASE = [S0, S1, S2, S3]

    def check(self, sheets, booklet_index=0, **kw):
        return build_binding_check(
            total_pages=kw.get("total_pages", 16),
            max_sheets_per_booklet=kw.get("max_sheets_per_booklet", 4),
            booklet_index=booklet_index,
            sheets=sheets,
        )

    def test_perfect_stack_needs_nothing(self):
        result = self.check([dict(s) for s in self.BASE])
        self.assertEqual(result["sheet_count"], 4)
        self.assertEqual(result["start_page"], 1)
        self.assertEqual(result["end_page"], 16)
        self.assertEqual(
            [(s["current_index"], s["expected_index"], s["required_operations"])
             for s in result["sheets"]],
            [(0, 0, []), (1, 1, []), (2, 2, []), (3, 3, [])],
        )
        self.assertEqual(result["swap_step_count"], 0)
        self.assertEqual(result["swap_steps"], [])

    def test_all_four_orientations_recognized(self):
        # 每张基准纸以四种摆放方式提交，均应识别且给出正确操作。
        variants = [
            (False, False, []),
            (False, True, ["swap_left_right"]),
            (True, False, ["swap_front_back"]),
            (True, True, ["swap_front_back", "swap_left_right"]),
        ]
        for target_index, (swap_sides, swap_lr, expected_ops) in enumerate(variants):
            target = self.BASE[target_index]
            placed = [dict(s) for s in self.BASE]
            placed[target_index] = _orient(target, swap_sides, swap_lr)
            result = self.check(placed)
            sheet = next(s for s in result["sheets"] if s["expected_index"] == target_index)
            self.assertEqual(sheet["required_operations"], expected_ops)
            self.assertEqual(result["swap_step_count"], 0)

    def test_reversed_two_sheets_one_swap(self):
        # 当前顺序 [S1, S0, S2, S3] -> 一次交换 (0,1)
        result = self.check([dict(self.S1), dict(self.S0), dict(self.S2), dict(self.S3)])
        self.assertEqual(
            [(s["current_index"], s["expected_index"]) for s in result["sheets"]],
            [(0, 1), (1, 0), (2, 2), (3, 3)],
        )
        self.assertEqual(result["swap_step_count"], 1)
        self.assertEqual(
            result["swap_steps"], [{"position_a": 0, "position_b": 1}]
        )

    def test_three_cycle_lexicographic_steps(self):
        # 当前顺序 [S1, S2, S0, S3]：最小 2 步，字典序最小序列 (0,1) 后 (0,2)
        result = self.check([dict(self.S1), dict(self.S2), dict(self.S0), dict(self.S3)])
        self.assertEqual(result["swap_step_count"], 2)
        self.assertEqual(
            [(s["position_a"], s["position_b"]) for s in result["swap_steps"]],
            [(0, 1), (0, 2)],
        )

    def test_missing_sheet_rejected(self):
        with self.assertRaises(BindingCheckError) as ctx:
            self.check([dict(self.S0), dict(self.S1), dict(self.S3)])
        messages = [d["message"] for d in ctx.exception.details]
        self.assertEqual(len(messages), 1)
        self.assertIn("缺少基准纸张", messages[0])
        self.assertIn("位置 2", messages[0])

    def test_duplicate_sheet_rejected(self):
        with self.assertRaises(BindingCheckError) as ctx:
            self.check([dict(self.S0), dict(self.S0), dict(self.S2), dict(self.S3)])
        messages = [d["message"] for d in ctx.exception.details]
        self.assertTrue(any("重复" in m for m in messages))
        self.assertTrue(any("缺少基准纸张" in m and "位置 1" in m for m in messages))

    def test_cross_booklet_page_rejected(self):
        # 17 页、每册 2 张纸：第 0 册区间 1..8，页码 9 属于第 1 册
        s0 = {"front": {"left": 8, "right": 1}, "back": {"left": 2, "right": 7}}
        s1 = {"front": {"left": 6, "right": 3}, "back": {"left": 4, "right": 5}}
        foreign = {"front": {"left": 9, "right": 3}, "back": {"left": 4, "right": 5}}
        with self.assertRaises(BindingCheckError) as ctx:
            build_binding_check(17, 2, 0, [s0, foreign])
        messages = [d["message"] for d in ctx.exception.details]
        self.assertTrue(any("跨册" in m and "9" in m for m in messages))

    def test_out_of_range_page_rejected(self):
        with self.assertRaises(BindingCheckError) as ctx:
            self.check(
                [
                    {"front": {"left": 17, "right": 1}, "back": {"left": 2, "right": 15}},
                    dict(self.S1), dict(self.S2), dict(self.S3),
                ]
            )
        self.assertTrue(
            any("跨册" in m or "越界" in m for m in
                [d["message"] for d in ctx.exception.details])
        )

    def test_unrestorable_layout_rejected(self):
        # 页码集合与 S0 相同（{1,2,15,16}），但配对方式不在四种摆放之内
        bad = {"front": {"left": 16, "right": 15}, "back": {"left": 1, "right": 2}}
        with self.assertRaises(BindingCheckError) as ctx:
            self.check([bad, dict(self.S1), dict(self.S2), dict(self.S3)])
        messages = [d["message"] for d in ctx.exception.details]
        self.assertTrue(any("无法通过左右互换及正反互换还原" in m for m in messages))
        # 物理纸并未缺失：不应再报 S0 缺纸
        self.assertFalse(any("缺少基准纸张：应在位置 0" in m for m in messages))

    def test_unknown_sheet_and_missing_rejected(self):
        # 页码集合 {1,3,13,15} 横跨 S0/S1，不属于本册任何一张基准纸
        weird = {"front": {"left": 15, "right": 1}, "back": {"left": 3, "right": 13}}
        with self.assertRaises(BindingCheckError) as ctx:
            self.check([weird, dict(self.S1), dict(self.S2), dict(self.S3)])
        messages = [d["message"] for d in ctx.exception.details]
        self.assertTrue(any("不属于第 0 册的任何基准纸张" in m for m in messages))
        self.assertTrue(any("缺少基准纸张" in m for m in messages))

    def test_booklet_index_out_of_range(self):
        with self.assertRaises(BindingCheckError) as ctx:
            self.check([dict(s) for s in self.BASE], booklet_index=1)
        self.assertEqual(ctx.exception.details[0]["field"], "booklet_index")

    def test_padded_booklet_with_nulls(self):
        # 5 页补齐到 8：S0 = null|1 / 2|null，S1 = null|3 / 4|5
        s0 = {"front": {"left": None, "right": 1}, "back": {"left": 2, "right": None}}
        s1 = {"front": {"left": None, "right": 3}, "back": {"left": 4, "right": 5}}
        # 顺序颠倒且 S1 左右互换：front 3|null / 5|4
        s1_lr = {"front": {"left": 3, "right": None}, "back": {"left": 5, "right": 4}}
        result = build_binding_check(5, 10, 0, [s1_lr, s0])
        self.assertEqual(result["sheet_count"], 2)
        self.assertEqual(result["sheets"][0]["expected_index"], 1)
        self.assertEqual(
            result["sheets"][0]["required_operations"], ["swap_left_right"]
        )
        self.assertEqual(
            result["swap_steps"], [{"position_a": 0, "position_b": 1}]
        )

    def test_blank_only_sheet_rejected(self):
        s0 = {"front": {"left": None, "right": 1}, "back": {"left": 2, "right": None}}
        blank = {
            "front": {"left": None, "right": None},
            "back": {"left": None, "right": None},
        }
        with self.assertRaises(BindingCheckError) as ctx:
            build_binding_check(5, 10, 0, [s0, blank])
        self.assertTrue(
            any("不属于第 0 册的任何基准纸张" in d["message"]
                for d in ctx.exception.details)
        )

    def test_multi_booklet_last_booklet(self):
        # 17 页、每册 2 张：第 2 册仅第 17 页，唯一纸 null|17 / null|null
        sheet = {"front": {"left": None, "right": 17},
                 "back": {"left": None, "right": None}}
        result = build_binding_check(17, 2, 2, [sheet])
        self.assertEqual((result["start_page"], result["end_page"]), (17, 17))
        self.assertEqual(result["sheets"][0]["required_operations"], [])
        self.assertEqual(result["swap_steps"], [])


class TestBuildManualDuplex(unittest.TestCase):
    """两轮手工双面打印清单。基准：8 页 1 册 2 张纸。

    S0: front 8|1  / back 2|7
    S1: front 6|3  / back 4|5
    """

    def _entry_keys(self, round_):
        return [
            (e["sequence"], e["booklet_index"], e["sheet_index"])
            for e in round_["entries"]
        ]

    def test_first_round_prints_fronts_outer_to_inner(self):
        result = build_manual_duplex(8, 4, refill_reversed=False, flip_edge="short")
        booklet = result["booklets"][0]
        self.assertEqual(booklet["index"], 0)
        self.assertEqual(booklet["sheet_count"], 2)
        self.assertEqual(len(booklet["rounds"]), 2)
        first, second = booklet["rounds"]
        self.assertEqual((first["round"], first["side"]), (1, "front"))
        self.assertEqual((second["round"], second["side"]), (2, "back"))
        # 首轮：外到内，正面
        self.assertEqual(self._entry_keys(first), [(0, 0, 0), (1, 0, 1)])
        self.assertEqual(
            [(e["left"], e["right"]) for e in first["entries"]],
            [(8, 1), (6, 3)],
        )

    def test_second_round_short_edge_keeps_order_and_lr(self):
        # 不回填倒序 + 短边翻转：反面同序，左右不变
        result = build_manual_duplex(8, 4, refill_reversed=False, flip_edge="short")
        second = result["booklets"][0]["rounds"][1]
        self.assertEqual(self._entry_keys(second), [(0, 0, 0), (1, 0, 1)])
        self.assertEqual(
            [(e["left"], e["right"]) for e in second["entries"]],
            [(2, 7), (4, 5)],
        )

    def test_reversed_refill_reverses_second_round_order(self):
        # 倒序回填 + 短边翻转：次序 S1 -> S0，左右不变
        result = build_manual_duplex(8, 4, refill_reversed=True, flip_edge="short")
        second = result["booklets"][0]["rounds"][1]
        self.assertEqual(self._entry_keys(second), [(0, 0, 1), (1, 0, 0)])
        self.assertEqual(
            [(e["left"], e["right"]) for e in second["entries"]],
            [(4, 5), (2, 7)],
        )

    def test_long_edge_swaps_back_lr(self):
        # 长边翻转：交换反面左右版面
        result = build_manual_duplex(8, 4, refill_reversed=False, flip_edge="long")
        second = result["booklets"][0]["rounds"][1]
        self.assertEqual(self._entry_keys(second), [(0, 0, 0), (1, 0, 1)])
        self.assertEqual(
            [(e["left"], e["right"]) for e in second["entries"]],
            [(7, 2), (5, 4)],
        )

    def test_reversed_and_long_combined(self):
        # 倒序回填 + 长边翻转：先反序、再交换左右
        result = build_manual_duplex(8, 4, refill_reversed=True, flip_edge="long")
        second = result["booklets"][0]["rounds"][1]
        self.assertEqual(self._entry_keys(second), [(0, 0, 1), (1, 0, 0)])
        self.assertEqual(
            [(e["left"], e["right"]) for e in second["entries"]],
            [(5, 4), (7, 2)],
        )

    def test_first_round_unaffected_by_options(self):
        # 回填与翻转选项只影响第二轮，首轮恒定为外到内正面
        for reversed_ in (False, True):
            for edge in ("short", "long"):
                result = build_manual_duplex(8, 4, reversed_, edge)
                first = result["booklets"][0]["rounds"][0]
                self.assertEqual(
                    [(e["sheet_index"], e["left"], e["right"])
                     for e in first["entries"]],
                    [(0, 8, 1), (1, 6, 3)],
                )

    def test_padding_blanks_stay_null(self):
        # 5 页补齐到 8：S0 back = 2|null，S1 front = null|3、back = 4|5
        result = build_manual_duplex(5, 10, refill_reversed=False, flip_edge="short")
        booklet = result["booklets"][0]
        first, second = booklet["rounds"]
        self.assertEqual(first["entries"][0]["left"], None)
        self.assertEqual(first["entries"][1]["left"], None)
        self.assertEqual(second["entries"][0], {
            "sequence": 0,
            "booklet_index": 0,
            "sheet_index": 0,
            "left": 2,
            "right": None,
        })
        # 长边翻转仅交换左右，null 依然为 null
        result_long = build_manual_duplex(5, 10, True, "long")
        back = result_long["booklets"][0]["rounds"][1]["entries"]
        self.assertEqual(
            [(e["sheet_index"], e["left"], e["right"]) for e in back],
            [(1, 5, 4), (0, None, 2)],
        )

    def test_multi_booklet_each_has_independent_two_rounds(self):
        # 17 页每册 2 张 -> 3 册，册 2 仅 1 张；每册各自两轮、序号独立
        result = build_manual_duplex(17, 2, refill_reversed=False, flip_edge="short")
        self.assertEqual(result["booklet_count"], 3)
        self.assertEqual(result["total_sheets"], 5)
        self.assertEqual([b["index"] for b in result["booklets"]], [0, 1, 2])
        for booklet in result["booklets"]:
            self.assertEqual(len(booklet["rounds"]), 2)
            for round_ in booklet["rounds"]:
                self.assertEqual(len(round_["entries"]), booklet["sheet_count"])
                self.assertEqual(
                    [e["booklet_index"] for e in round_["entries"]],
                    [booklet["index"]] * booklet["sheet_count"],
                )
                self.assertEqual(
                    [e["sequence"] for e in round_["entries"]],
                    list(range(booklet["sheet_count"])),
                )
        # 册 1 首轮：S0 16|9 / S1 14|11（外到内）
        self.assertEqual(
            [(e["sheet_index"], e["left"], e["right"])
             for e in result["booklets"][1]["rounds"][0]["entries"]],
            [(0, 16, 9), (1, 14, 11)],
        )
        # 末册唯一纸：front null|17，倒序与否第二轮都只有该纸
        last = result["booklets"][2]
        self.assertEqual(last["sheet_count"], 1)
        self.assertEqual(
            (last["rounds"][0]["entries"][0]["left"],
             last["rounds"][0]["entries"][0]["right"]),
            (None, 17),
        )

    def test_multi_booklet_reversed_round_two(self):
        result = build_manual_duplex(17, 2, refill_reversed=True, flip_edge="short")
        # 册 0 两张纸，第二轮次序为 1 -> 0
        back = result["booklets"][0]["rounds"][1]["entries"]
        self.assertEqual([e["sheet_index"] for e in back], [1, 0])
        # 末册一张纸，次序不变
        self.assertEqual(
            [e["sheet_index"] for e in result["booklets"][2]["rounds"][1]["entries"]],
            [0],
        )

    def test_invalid_flip_edge_raises(self):
        with self.assertRaises(ValueError):
            build_manual_duplex(8, 4, False, "middle")

    def test_summary_fields_echo_options(self):
        result = build_manual_duplex(8, 4, refill_reversed=True, flip_edge="long")
        self.assertEqual(result["total_pages"], 8)
        self.assertEqual(result["max_sheets_per_booklet"], 4)
        self.assertEqual(result["refill_reversed"], True)
        self.assertEqual(result["flip_edge"], "long")


class TestMinimumSwapSteps(unittest.TestCase):
    """对所有小规模排列穷举校验：交换数最小且步骤序列字典序最小。"""

    @staticmethod
    def _min_swaps(perm):
        return build_binding_check.__globals__["_minimum_swaps_to_identity"](perm)

    @staticmethod
    def _brute_lex_min(perm):
        from collections import deque

        def apply(state, a, b):
            ia, ib = state.index(a), state.index(b)
            s = list(state)
            s[ia], s[ib] = s[ib], s[ia]
            return tuple(s)

        start, target = tuple(perm), tuple(range(len(perm)))
        frontier = deque([(start, [])])
        seen = {start}
        while frontier:
            next_frontier = []
            batch = sorted(frontier, key=lambda item: item[1])
            for state, path in batch:
                if state == target:
                    return path
                n = len(perm)
                for a in range(n):
                    for b in range(a + 1, n):
                        ns = apply(state, a, b)
                        if ns not in seen:
                            seen.add(ns)
                            next_frontier.append((ns, path + [(a, b)]))
            frontier = deque(next_frontier)
        raise AssertionError

    def test_all_permutations_up_to_six(self):
        for n in range(1, 7):
            for perm in itertools.permutations(range(n)):
                steps = self._min_swaps(perm)
                expected = self._brute_lex_min(perm)
                got = [(s["position_a"], s["position_b"]) for s in steps]
                self.assertEqual(got, expected, f"perm={perm}")
                # 逐步执行后应回到恒等排列
                state = list(perm)
                for a, b in got:
                    ia = next(i for i, p in enumerate(state) if p == a)
                    ib = next(i for i, p in enumerate(state) if p == b)
                    state[ia], state[ib] = state[ib], state[ia]
                self.assertEqual(state, list(range(n)))


if __name__ == "__main__":
    unittest.main()
