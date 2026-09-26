"""API 层测试（需先安装 requirements-dev.txt：fastapi / httpx）。

运行方式：
    pip install -r requirements-dev.txt
    python -m unittest discover -s tests -v
"""

import unittest

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestImposeEndpoint(unittest.TestCase):
    def test_valid_post_request(self):
        resp = client.post(
            "/api/v1/impose",
            json={"total_pages": 16, "max_sheets_per_booklet": 4},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_pages"], 16)
        self.assertEqual(data["booklet_count"], 1)
        self.assertEqual(data["total_sheets"], 4)
        outer = data["booklets"][0]["sheets"][0]
        self.assertEqual(outer["front"], {"left": 16, "right": 1})
        self.assertEqual(outer["back"], {"left": 2, "right": 15})

    def test_blank_page_is_null(self):
        resp = client.post(
            "/api/v1/impose",
            json={"total_pages": 5, "max_sheets_per_booklet": 10},
        )
        self.assertEqual(resp.status_code, 200)
        booklet = resp.json()["booklets"][0]
        self.assertEqual(booklet["blank_pages"], 3)
        self.assertEqual(booklet["sheets"][0]["front"]["left"], None)

    def test_get_equivalent(self):
        resp = client.get("/api/v1/impose?total_pages=8&max_sheets_per_booklet=2")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["booklets"][0]["sheets"][0]["front"], {"left": 8, "right": 1})

    def test_health(self):
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


class TestInvalidInputs(unittest.TestCase):
    def _assert_400(self, payload):
        resp = client.post("/api/v1/impose", json=payload)
        self.assertEqual(resp.status_code, 400, payload)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_input")
        self.assertIn("details", body)
        return body

    def test_zero_rejected(self):
        body = self._assert_400(
            {"total_pages": 0, "max_sheets_per_booklet": 2}
        )
        self.assertIn("total_pages", body["details"][0]["field"])

    def test_negative_rejected(self):
        self._assert_400({"total_pages": 16, "max_sheets_per_booklet": -1})

    def test_float_rejected(self):
        self._assert_400({"total_pages": 16.5, "max_sheets_per_booklet": 2})

    def test_integer_float_rejected(self):
        # 16.0 这类“看起来是整数”的浮点数也必须拒绝
        self._assert_400({"total_pages": 16.0, "max_sheets_per_booklet": 2})

    def test_string_rejected(self):
        self._assert_400({"total_pages": "16", "max_sheets_per_booklet": 2})

    def test_boolean_rejected(self):
        self._assert_400({"total_pages": True, "max_sheets_per_booklet": 2})

    def test_null_rejected(self):
        self._assert_400({"total_pages": None, "max_sheets_per_booklet": 2})

    def test_missing_field_rejected(self):
        self._assert_400({"total_pages": 16})

    def test_extra_field_rejected(self):
        self._assert_400(
            {"total_pages": 16, "max_sheets_per_booklet": 2, "duplex": True}
        )

    def test_get_invalid_query_rejected(self):
        resp = client.get("/api/v1/impose?total_pages=abc&max_sheets_per_booklet=2")
        self.assertEqual(resp.status_code, 400)
        resp = client.get("/api/v1/impose?total_pages=0&max_sheets_per_booklet=2")
        self.assertEqual(resp.status_code, 400)
        resp = client.get("/api/v1/impose?total_pages=16&max_sheets_per_booklet=2.5")
        self.assertEqual(resp.status_code, 400)


class TestReprintEndpoint(unittest.TestCase):
    REPRINT_URL = "/api/v1/reprint"

    def test_valid_reprint_single_range(self):
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "damaged_ranges": [{"start_page": 1, "end_page": 4}],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["reprint_sheet_count"], 2)
        self.assertEqual(
            [
                (s["booklet_index"], s["sheet_index"], s["damaged_pages"])
                for s in data["sheets"]
            ],
            [(0, 0, [1, 2]), (0, 1, [3, 4])],
        )
        outer = data["sheets"][0]
        self.assertEqual(outer["front"], {"left": 16, "right": 1})
        self.assertEqual(outer["back"], {"left": 2, "right": 15})

    def test_overlapping_ranges_dedupe_sheets(self):
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "damaged_ranges": [
                    {"start_page": 1, "end_page": 2},
                    {"start_page": 2, "end_page": 1},
                ],
            },
        )
        # 第二项区间倒置 -> 400
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_input")

        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "damaged_ranges": [
                    {"start_page": 1, "end_page": 3},
                    {"start_page": 2, "end_page": 8},
                ],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        # 并集 1..8 -> 4 张纸，纸张不重复
        self.assertEqual(data["reprint_sheet_count"], 4)
        keys = [(s["booklet_index"], s["sheet_index"]) for s in data["sheets"]]
        self.assertEqual(keys, [(0, 0), (0, 1), (0, 2), (0, 3)])
        self.assertEqual(
            [p for s in data["sheets"] for p in s["damaged_pages"]],
            [1, 2, 3, 4, 5, 6, 7, 8],
        )

    def test_multi_booklet_ordering(self):
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 17,
                "max_sheets_per_booklet": 2,
                "damaged_ranges": [{"start_page": 17, "end_page": 17}],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        sheets = resp.json()["sheets"]
        self.assertEqual(len(sheets), 1)
        self.assertEqual(
            (sheets[0]["booklet_index"], sheets[0]["sheet_index"]), (2, 0)
        )
        self.assertEqual(sheets[0]["damaged_pages"], [17])
        # 最后一册补白以 null 表示
        self.assertIn(None, sheets[0]["front"].values())
        self.assertIn(None, sheets[0]["back"].values())

    def test_empty_ranges_rejected(self):
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "damaged_ranges": [],
            },
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_input")
        self.assertTrue(
            any("damaged_ranges" in d["field"] for d in body["details"])
        )

    def test_reversed_range_rejected(self):
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "damaged_ranges": [{"start_page": 5, "end_page": 3}],
            },
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertTrue(any("倒置" in d["message"] for d in body["details"]))

    def test_out_of_bounds_rejected(self):
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "damaged_ranges": [{"start_page": 15, "end_page": 17}],
            },
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertTrue(any("越界" in d["message"] for d in body["details"]))

    def test_missing_ranges_rejected(self):
        resp = client.post(
            self.REPRINT_URL,
            json={"total_pages": 16, "max_sheets_per_booklet": 4},
        )
        self.assertEqual(resp.status_code, 400)

    def test_bad_range_item_rejected(self):
        # 区间项必须是对象
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "damaged_ranges": [[1, 4]],
            },
        )
        self.assertEqual(resp.status_code, 400)

        # 区间端点必须为正整数
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "damaged_ranges": [{"start_page": 0, "end_page": 4}],
            },
        )
        self.assertEqual(resp.status_code, 400)

        # 多余字段
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "damaged_ranges": [
                    {"start_page": 1, "end_page": 4, "note": "x"}
                ],
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_common_field_validation_still_400(self):
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 0,
                "max_sheets_per_booklet": 4,
                "damaged_ranges": [{"start_page": 1, "end_page": 1}],
            },
        )
        self.assertEqual(resp.status_code, 400)
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": True,
                "damaged_ranges": [{"start_page": 1, "end_page": 1}],
            },
        )
        self.assertEqual(resp.status_code, 400)
        resp = client.post(
            self.REPRINT_URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "damaged_ranges": "1-4",
            },
        )
        self.assertEqual(resp.status_code, 400)


class TestChapterImposeEndpoint(unittest.TestCase):
    URL = "/api/v1/chapter-impose"

    def test_valid_request_merges_adjacent_chapters(self):
        resp = client.post(
            self.URL,
            json={"chapter_pages": [6, 5, 3], "max_sheets_per_booklet": 2},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["chapter_pages"], [6, 5, 3])
        self.assertEqual(data["chapter_count"], 3)
        self.assertEqual(data["total_pages"], 14)
        self.assertEqual(data["booklet_count"], 2)
        self.assertEqual(data["total_sheets"], 4)
        self.assertEqual(data["total_blank_pages"], 2)

        first, second = data["booklets"]
        self.assertEqual((first["start_chapter"], first["end_chapter"]), (1, 1))
        self.assertEqual((first["start_page"], first["end_page"]), (1, 6))
        self.assertEqual(first["sheet_count"], 2)
        self.assertEqual(first["blank_pages"], 2)
        # 补白位为 null
        self.assertIsNone(first["sheets"][0]["front"]["left"])
        self.assertIsNone(first["sheets"][0]["back"]["right"])
        self.assertEqual(first["sheets"][1]["front"], {"left": 6, "right": 3})

        self.assertEqual((second["start_chapter"], second["end_chapter"]), (2, 3))
        self.assertEqual((second["start_page"], second["end_page"]), (7, 14))
        self.assertEqual(second["blank_pages"], 0)
        # 页码为全局原稿页码
        self.assertEqual(second["sheets"][0]["front"], {"left": 14, "right": 7})

    def test_min_sheets_then_booklet_count_then_lexicographic(self):
        # [4,1,4] 容量 8：最优 3 张纸、2 册；[4][1,4] 与 [4,1][4] 并列，
        # 结束章节序号序列 [1,3] < [2,3] -> 选 [4][1,4]
        resp = client.post(
            self.URL,
            json={"chapter_pages": [4, 1, 4], "max_sheets_per_booklet": 2},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["total_sheets"], 3)
        self.assertEqual(data["booklet_count"], 2)
        self.assertEqual(
            [(b["start_chapter"], b["end_chapter"]) for b in data["booklets"]],
            [(1, 1), (2, 3)],
        )

    def test_single_chapter_over_capacity_rejected(self):
        resp = client.post(
            self.URL,
            json={"chapter_pages": [4, 9, 4], "max_sheets_per_booklet": 2},
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_input")
        self.assertTrue(
            any("chapter_pages" in d["field"] for d in body["details"])
        )
        self.assertTrue(
            any("超过单册容量上限" in d["message"] for d in body["details"])
        )

    def test_invalid_payloads_rejected(self):
        cases = [
            {},  # 缺少必填参数
            {"max_sheets_per_booklet": 2},  # 缺 chapter_pages
            {"chapter_pages": [4]},  # 缺 max_sheets_per_booklet
            {"chapter_pages": [], "max_sheets_per_booklet": 2},  # 空数组
            {"chapter_pages": [0, 4], "max_sheets_per_booklet": 2},  # 非正整数页数
            {"chapter_pages": [-1], "max_sheets_per_booklet": 2},  # 负数页数
            {"chapter_pages": [4.5, 4], "max_sheets_per_booklet": 2},  # 小数页数
            {"chapter_pages": [4.0, 4], "max_sheets_per_booklet": 2},  # 整数浮点
            {"chapter_pages": ["4", 4], "max_sheets_per_booklet": 2},  # 数字字符串
            {"chapter_pages": [True, 4], "max_sheets_per_booklet": 2},  # 布尔值
            {"chapter_pages": [4, None], "max_sheets_per_booklet": 2},  # null 项
            {"chapter_pages": "4,5", "max_sheets_per_booklet": 2},  # 非数组
            {"chapter_pages": [4], "max_sheets_per_booklet": 0},  # 上限非正
            {"chapter_pages": [4], "max_sheets_per_booklet": 2.5},  # 上限小数
            {"chapter_pages": [4], "max_sheets_per_booklet": True},  # 上限布尔
            {"chapter_pages": [4], "max_sheets_per_booklet": 2, "duplex": True},  # 多余字段
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                resp = client.post(self.URL, json=payload)
                self.assertEqual(resp.status_code, 400)
                body = resp.json()
                self.assertEqual(body["error"], "invalid_input")
                self.assertIn("details", body)


class TestBindingCheckEndpoint(unittest.TestCase):
    URL = "/api/v1/binding-check"

    # 16 页 1 册 4 张纸的基准拼版
    S0 = {"front": {"left": 16, "right": 1}, "back": {"left": 2, "right": 15}}
    S1 = {"front": {"left": 14, "right": 3}, "back": {"left": 4, "right": 13}}
    S2 = {"front": {"left": 12, "right": 5}, "back": {"left": 6, "right": 11}}
    S3 = {"front": {"left": 10, "right": 7}, "back": {"left": 8, "right": 9}}

    def test_perfect_stack(self):
        resp = client.post(
            self.URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "booklet_index": 0,
                "sheets": [self.S0, self.S1, self.S2, self.S3],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["booklet_index"], 0)
        self.assertEqual((data["start_page"], data["end_page"]), (1, 16))
        self.assertEqual(data["sheet_count"], 4)
        self.assertEqual(
            [(s["current_index"], s["expected_index"], s["required_operations"])
             for s in data["sheets"]],
            [(0, 0, []), (1, 1, []), (2, 2, []), (3, 3, [])],
        )
        self.assertEqual(data["swap_step_count"], 0)
        self.assertEqual(data["swap_steps"], [])

    def test_mixed_orientations_and_swaps(self):
        # 位置 0 放 S1（原样），位置 1 放 S0 但左右互换，
        # 位置 2 放 S2 但正反互换，位置 3 放 S3 且同时互换。
        payload = {
            "total_pages": 16,
            "max_sheets_per_booklet": 4,
            "booklet_index": 0,
            "sheets": [
                self.S1,
                {"front": {"left": 1, "right": 16}, "back": {"left": 15, "right": 2}},
                {"front": {"left": 6, "right": 11}, "back": {"left": 12, "right": 5}},
                {"front": {"left": 9, "right": 8}, "back": {"left": 7, "right": 10}},
            ],
        }
        resp = client.post(self.URL, json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(
            [s["required_operations"] for s in data["sheets"]],
            [
                [],
                ["swap_left_right"],
                ["swap_front_back"],
                ["swap_front_back", "swap_left_right"],
            ],
        )
        self.assertEqual(data["swap_step_count"], 1)
        self.assertEqual(
            data["swap_steps"], [{"position_a": 0, "position_b": 1}]
        )

    def test_three_cycle_swap_sequence(self):
        # 当前顺序 S1, S2, S0, S3 -> 最少 2 步，字典序最小 (0,1),(0,2)
        resp = client.post(
            self.URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "booklet_index": 0,
                "sheets": [self.S1, self.S2, self.S0, self.S3],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["swap_step_count"], 2)
        self.assertEqual(
            [(s["position_a"], s["position_b"]) for s in data["swap_steps"]],
            [(0, 1), (0, 2)],
        )

    def test_null_padding_accepted(self):
        resp = client.post(
            self.URL,
            json={
                "total_pages": 5,
                "max_sheets_per_booklet": 10,
                "booklet_index": 0,
                "sheets": [
                    {"front": {"left": None, "right": 1},
                     "back": {"left": 2, "right": None}},
                    {"front": {"left": None, "right": 3},
                     "back": {"left": 4, "right": 5}},
                ],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["swap_step_count"], 0)

    def test_missing_sheet_400(self):
        resp = client.post(
            self.URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "booklet_index": 0,
                "sheets": [self.S0, self.S1, self.S3],
            },
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_input")
        self.assertTrue(any("缺少基准纸张" in d["message"] for d in body["details"]))

    def test_duplicate_sheet_400(self):
        resp = client.post(
            self.URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "booklet_index": 0,
                "sheets": [self.S0, self.S0, self.S2, self.S3],
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(
            any("同一张纸" in d["message"] for d in resp.json()["details"])
        )

    def test_cross_booklet_page_400(self):
        # 17 页每册 2 张：册 0 区间 1..8，第 9 页属册 1
        resp = client.post(
            self.URL,
            json={
                "total_pages": 17,
                "max_sheets_per_booklet": 2,
                "booklet_index": 0,
                "sheets": [
                    {"front": {"left": 8, "right": 1},
                     "back": {"left": 2, "right": 7}},
                    {"front": {"left": 9, "right": 3},
                     "back": {"left": 4, "right": 5}},
                ],
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(
            any("跨册" in d["message"] for d in resp.json()["details"])
        )

    def test_unrestorable_layout_400(self):
        bad = {"front": {"left": 16, "right": 15}, "back": {"left": 1, "right": 2}}
        resp = client.post(
            self.URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "booklet_index": 0,
                "sheets": [bad, self.S1, self.S2, self.S3],
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(
            any("无法通过左右互换及正反互换还原" in d["message"]
                for d in resp.json()["details"])
        )

    def test_booklet_index_out_of_range_400(self):
        resp = client.post(
            self.URL,
            json={
                "total_pages": 16,
                "max_sheets_per_booklet": 4,
                "booklet_index": 3,
                "sheets": [self.S0, self.S1, self.S2, self.S3],
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["details"][0]["field"], "booklet_index")

    def test_payload_validation_400(self):
        valid = {
            "total_pages": 16,
            "max_sheets_per_booklet": 4,
            "booklet_index": 0,
            "sheets": [self.S0, self.S1, self.S2, self.S3],
        }
        cases = [
            {},  # 缺少必填参数
            {**valid, "total_pages": 0},  # 非正
            {**valid, "max_sheets_per_booklet": -2},  # 负数
            {**valid, "booklet_index": -1},  # 负册序
            {**valid, "booklet_index": 1.0},  # 整数浮点
            {**valid, "total_pages": True},  # 布尔
            {**{**valid, "sheets": []}},  # 空 sheets
            {**valid, "sheets": "x"},  # sheets 非数组
            {**valid, "sheets": [{"front": {"left": 16, "right": 1}}]},  # 缺 back
            {**valid, "sheets": [{"front": {"left": 16, "right": 1},
                                  "back": {"left": 2, "right": 15},
                                  "note": "x"}]},  # 多余字段
            {**valid, "sheets": [
                {"front": {"left": 0, "right": 1},
                 "back": {"left": 2, "right": 15}},
                self.S1, self.S2, self.S3,
            ]},  # 页码 0
            {**valid, "sheets": [
                {"front": {"left": "16", "right": 1},
                 "back": {"left": 2, "right": 15}},
                self.S1, self.S2, self.S3,
            ]},  # 数字字符串页码
            {**valid, "extra": 1},  # 顶层多余字段
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                resp = client.post(self.URL, json=payload)
                self.assertEqual(resp.status_code, 400, payload)
                body = resp.json()
                self.assertEqual(body["error"], "invalid_input")
                self.assertIn("details", body)


class TestManualDuplexEndpoint(unittest.TestCase):
    URL = "/api/v1/manual-duplex"

    def test_short_edge_same_order(self):
        resp = client.post(
            self.URL,
            json={
                "total_pages": 8,
                "max_sheets_per_booklet": 4,
                "refill_reversed": False,
                "flip_edge": "short",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["booklet_count"], 1)
        self.assertEqual(data["total_sheets"], 2)
        self.assertEqual(data["refill_reversed"], False)
        self.assertEqual(data["flip_edge"], "short")
        booklet = data["booklets"][0]
        self.assertEqual(len(booklet["rounds"]), 2)
        first, second = booklet["rounds"]
        self.assertEqual(
            [(e["sequence"], e["booklet_index"], e["sheet_index"])
             for e in first["entries"]],
            [(0, 0, 0), (1, 0, 1)],
        )
        self.assertEqual(
            [(e["left"], e["right"]) for e in first["entries"]],
            [(8, 1), (6, 3)],
        )
        self.assertEqual(
            [(e["sequence"], e["sheet_index"], e["left"], e["right"])
             for e in second["entries"]],
            [(0, 0, 2, 7), (1, 1, 4, 5)],
        )

    def test_reversed_long_edge(self):
        resp = client.post(
            self.URL,
            json={
                "total_pages": 8,
                "max_sheets_per_booklet": 4,
                "refill_reversed": True,
                "flip_edge": "long",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        second = resp.json()["booklets"][0]["rounds"][1]
        self.assertEqual(
            [(e["sequence"], e["sheet_index"], e["left"], e["right"])
             for e in second["entries"]],
            [(0, 1, 5, 4), (1, 0, 7, 2)],
        )

    def test_multi_booklet_independent_rounds_and_null_padding(self):
        resp = client.post(
            self.URL,
            json={
                "total_pages": 17,
                "max_sheets_per_booklet": 2,
                "refill_reversed": True,
                "flip_edge": "long",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        booklets = resp.json()["booklets"]
        self.assertEqual([b["index"] for b in booklets], [0, 1, 2])
        for booklet in booklets:
            self.assertEqual(len(booklet["rounds"]), 2)
        # 册 0 两张纸第二轮反序：S1 back 4|5 -> 长边 5|4；S0 back 2|7 -> 7|2
        back = booklets[0]["rounds"][1]["entries"]
        self.assertEqual(
            [(e["sheet_index"], e["left"], e["right"]) for e in back],
            [(1, 5, 4), (0, 7, 2)],
        )
        # 末册仅一张纸 null|17 / null|null，长边交换反面后仍为 null|null
        last_back = booklets[2]["rounds"][1]["entries"][0]
        self.assertEqual(
            (last_back["sheet_index"], last_back["left"], last_back["right"]),
            (0, None, None),
        )

    def test_invalid_options_400(self):
        valid = {
            "total_pages": 8,
            "max_sheets_per_booklet": 4,
            "refill_reversed": False,
            "flip_edge": "short",
        }
        cases = [
            {**valid, "flip_edge": "middle"},  # 非法翻转边
            {**valid, "flip_edge": "LONG"},  # 大小写不接受
            {**valid, "flip_edge": 1},  # 非字符串
            {**valid, "flip_edge": True},  # 布尔值
            {**valid, "refill_reversed": "true"},  # 非布尔
            {**valid, "refill_reversed": 1},  # 1 不当作 true
            {**valid, "refill_reversed": None},  # null
            {"total_pages": 8, "max_sheets_per_booklet": 4,
             "flip_edge": "short"},  # 缺 refill_reversed
            {"total_pages": 8, "max_sheets_per_booklet": 4,
             "refill_reversed": False},  # 缺 flip_edge
            {"total_pages": 0, **{k: v for k, v in valid.items()
                                 if k != "total_pages"}},  # 总页数非正
            {**valid, "total_pages": 8.0},  # 整数浮点
            {**valid, "max_sheets_per_booklet": 0},  # 每册纸张数非正
            {**valid, "extra": 1},  # 多余字段
            {},  # 空请求体
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                resp = client.post(self.URL, json=payload)
                self.assertEqual(resp.status_code, 400, payload)
                body = resp.json()
                self.assertEqual(body["error"], "invalid_input")
                self.assertIn("details", body)

    def test_invalid_option_messages(self):
        resp = client.post(
            self.URL,
            json={"total_pages": 8, "max_sheets_per_booklet": 4,
                  "refill_reversed": False, "flip_edge": "middle"},
        )
        body = resp.json()
        self.assertTrue(
            any(d["field"] == "flip_edge" for d in body["details"])
        )
        self.assertIn("short", body["details"][0]["message"])

        resp = client.post(
            self.URL,
            json={"total_pages": 8, "max_sheets_per_booklet": 4,
                  "refill_reversed": "yes", "flip_edge": "short"},
        )
        body = resp.json()
        self.assertEqual(body["details"][0]["field"], "refill_reversed")
        self.assertIn("布尔值", body["details"][0]["message"])


class TestColorPlanEndpoint(unittest.TestCase):
    URL = "/api/v1/color-plan"

    VALID = {
        "total_pages": 12,
        "max_sheets_per_booklet": 2,
        "color_ranges": [{"start_page": 9, "end_page": 12}],
        "color_price_per_sheet": 100,
        "bw_price_per_sheet": 10,
    }

    def test_valid_color_plan(self):
        resp = client.post(self.URL, json=self.VALID)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["total_cost"], 120)
        self.assertEqual(data["booklet_count"], 2)
        self.assertEqual(data["total_sheets"], 3)
        self.assertEqual(data["color_sheet_count"], 1)
        self.assertEqual(data["color_price_per_sheet"], 100)
        self.assertEqual(data["bw_price_per_sheet"], 10)
        self.assertEqual(
            data["color_ranges"], [{"start_page": 9, "end_page": 12}]
        )

        first, second = data["booklets"]
        self.assertEqual((first["start_page"], first["end_page"]), (1, 8))
        self.assertEqual(first["cost"], 20)
        self.assertEqual(first["color_sheet_count"], 0)
        self.assertEqual(
            [s["print_mode"] for s in first["sheets"]], ["bw", "bw"]
        )
        self.assertEqual(first["sheets"][0]["front"], {"left": 8, "right": 1})

        self.assertEqual((second["start_page"], second["end_page"]), (9, 12))
        self.assertEqual(second["cost"], 100)
        self.assertEqual(second["color_sheet_count"], 1)
        self.assertEqual([s["print_mode"] for s in second["sheets"]], ["color"])
        self.assertEqual(second["sheets"][0]["front"], {"left": 12, "right": 9})
        self.assertEqual(second["sheets"][0]["back"], {"left": 10, "right": 11})

    def test_color_ranges_optional(self):
        for variant in ({}, {"color_ranges": None}, {"color_ranges": []}):
            payload = {
                "total_pages": 8,
                "max_sheets_per_booklet": 4,
                "color_price_per_sheet": 30,
                "bw_price_per_sheet": 10,
                **variant,
            }
            with self.subTest(payload=payload):
                resp = client.post(self.URL, json=payload)
                self.assertEqual(resp.status_code, 200, resp.text)
                data = resp.json()
                self.assertEqual(data["total_cost"], 20)
                self.assertEqual(data["color_sheet_count"], 0)
                self.assertEqual(data["color_ranges"], [])
                modes = [
                    s["print_mode"] for s in data["booklets"][0]["sheets"]
                ]
                self.assertEqual(modes, ["bw", "bw"])

    def test_overlapping_ranges_not_double_counted(self):
        payload = {
            **self.VALID,
            "color_ranges": [
                {"start_page": 9, "end_page": 11},
                {"start_page": 11, "end_page": 12},
            ],
        }
        resp = client.post(self.URL, json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["total_cost"], 120)
        self.assertEqual(data["color_sheet_count"], 1)

    def test_blank_pages_stay_bw(self):
        resp = client.post(
            self.URL,
            json={
                "total_pages": 5,
                "max_sheets_per_booklet": 10,
                "color_ranges": [{"start_page": 5, "end_page": 5}],
                "color_price_per_sheet": 30,
                "bw_price_per_sheet": 10,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        booklet = resp.json()["booklets"][0]
        self.assertEqual(booklet["blank_pages"], 3)
        self.assertEqual(
            [s["print_mode"] for s in booklet["sheets"]], ["bw", "color"]
        )
        self.assertIsNone(booklet["sheets"][0]["front"]["left"])

    def test_total_pages_upper_bound(self):
        resp = client.post(
            self.URL,
            json={
                "total_pages": 200,
                "max_sheets_per_booklet": 1,
                "color_price_per_sheet": 5,
                "bw_price_per_sheet": 3,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["booklet_count"], 50)
        self.assertEqual(data["total_cost"], 50 * 3)

        resp = client.post(
            self.URL,
            json={
                "total_pages": 201,
                "max_sheets_per_booklet": 1,
                "color_price_per_sheet": 5,
                "bw_price_per_sheet": 3,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "invalid_input")

    def test_reversed_range_rejected(self):
        resp = client.post(
            self.URL,
            json={
                **self.VALID,
                "color_ranges": [{"start_page": 5, "end_page": 3}],
            },
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_input")
        self.assertTrue(any("倒置" in d["message"] for d in body["details"]))

    def test_out_of_bounds_range_rejected(self):
        resp = client.post(
            self.URL,
            json={
                **self.VALID,
                "color_ranges": [{"start_page": 11, "end_page": 13}],
            },
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertTrue(any("越界" in d["message"] for d in body["details"]))

    def test_invalid_payloads_rejected(self):
        cases = [
            {},  # 空请求体
            {"total_pages": 12},  # 缺少必填参数
            {**self.VALID, "total_pages": 0},  # 总页数非正
            {**self.VALID, "total_pages": -3},  # 负数
            {**self.VALID, "total_pages": 201},  # 超过 200 上限
            {**self.VALID, "total_pages": 12.5},  # 小数
            {**self.VALID, "total_pages": 12.0},  # 整数浮点
            {**self.VALID, "total_pages": "12"},  # 数字字符串
            {**self.VALID, "total_pages": True},  # 布尔值
            {**self.VALID, "total_pages": None},  # null
            {**self.VALID, "max_sheets_per_booklet": 0},  # 每册纸张数非正
            {**self.VALID, "max_sheets_per_booklet": 2.5},  # 小数
            {**self.VALID, "max_sheets_per_booklet": True},  # 布尔值
            {**self.VALID, "color_price_per_sheet": 0},  # 彩印单价非正
            {**self.VALID, "color_price_per_sheet": -100},  # 负单价
            {**self.VALID, "color_price_per_sheet": 100.0},  # 整数浮点
            {**self.VALID, "color_price_per_sheet": "100"},  # 数字字符串
            {**self.VALID, "color_price_per_sheet": True},  # 布尔值
            {**self.VALID, "bw_price_per_sheet": 0},  # 黑白单价非正
            {**self.VALID, "bw_price_per_sheet": 1.5},  # 小数单价
            {**self.VALID, "bw_price_per_sheet": None},  # null 单价
            {**self.VALID, "color_ranges": "9-12"},  # 区间列表非数组
            {**self.VALID, "color_ranges": [[9, 12]]},  # 区间项非对象
            {**self.VALID, "color_ranges": [{"start_page": 0, "end_page": 4}]},  # 端点非正
            {**self.VALID, "color_ranges": [{"start_page": 9}]},  # 区间缺端点
            {**self.VALID, "color_ranges": [
                {"start_page": 9, "end_page": 12, "note": "x"}
            ]},  # 区间多余字段
            {**self.VALID, "extra": 1},  # 顶层多余字段
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                resp = client.post(self.URL, json=payload)
                self.assertEqual(resp.status_code, 400, payload)
                body = resp.json()
                self.assertEqual(body["error"], "invalid_input")
                self.assertIn("details", body)


if __name__ == "__main__":
    unittest.main()
