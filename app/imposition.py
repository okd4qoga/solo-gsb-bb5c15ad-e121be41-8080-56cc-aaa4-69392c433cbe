"""骑马钉（saddle-stitched）小册子拼版核心算法。

规则概述
--------
1. 每张纸正反两面各印 2 页，对折后 1 张纸 = 4 个版面，因此每册页数必须
   补齐到 4 的倍数，补位页为空白页。
2. 原稿按顺序分册：除最后一册外，每册都装满 ``4 * max_sheets_per_booklet`` 页。
3. 对一册已补齐到 ``n`` 页（``n`` 为 4 的倍数）的小册子，纸张从外到内编号
   为 ``i = 0 .. n/4-1``，每张纸：

   - 正面（front，朝外的一面）：左页 ``n - 2i``，右页 ``1 + 2i``
   - 反面（back，朝内的一面）：左页 ``2 + 2i``，右页 ``n - 1 - 2i``

   按此拼版双面打印（沿短边翻转）并对折后，翻开顺序即为 1, 2, 3, ..., n。
4. 补齐的空白页编号为 0，对外用 ``None``（JSON null）表示。

另支持按章节分册（``build_chapter_imposition``）：章节不可拆分、仅相邻章节
可合册，在每册页数不超过 ``4 * max_sheets_per_booklet`` 的所有方案中依次最小化
总纸张数与册数，并以各册结束章节序号序列的字典序裁决并列。

手工双面打印清单（``build_manual_duplex``）：不支持自动双面打印时，每册纸分两轮
手工送纸——

1. 首轮：纸张从外到内依次打印各自正面；
2. 把首轮出纸堆原样（或倒序）回填进纸口，再印反面：
   - 倒序回填（``refill_reversed=True``）时反转第二轮的纸张次序；
   - 沿长边翻转回填时交换反面左右版面，沿短边翻转时左右保持不变。

装订核对（``build_binding_check``）：以拼版结果为基准核对单册实际纸张。
每张物理纸允许的摆放方式仅有四种，构成克莱因四元群：

- 原样（identity）；
- 左右互换（left_right）：同一面左右两版对调（纸张横向调头）；
- 正反互换（front_back）：翻到另一面，左右位置不变（沿短边翻转，与双面
  打印的翻页方式一致）；
- 同时左右、正反互换（left_right_front_back）：上述两操作的组合。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


class ChapterTooLargeError(ValueError):
    """单章页数超过单册容量（章节不可拆分，无法分册）。"""

    def __init__(self, chapter: int, pages: int, capacity: int) -> None:
        self.chapter = chapter
        self.pages = pages
        self.capacity = capacity
        super().__init__(
            f"第 {chapter} 章共 {pages} 页，超过单册容量上限 {capacity} 页"
            "（4 × max_sheets_per_booklet），章节不可拆分。"
        )


class BindingCheckError(ValueError):
    """装订核对失败：纸张缺失 / 重复 / 跨册 / 版面无法还原。

    ``details`` 为若干 ``{"field", "message"}``，与 API 层既有 400 错误
    响应的 details 口径一致。
    """

    def __init__(self, details: Sequence[dict[str, str]]) -> None:
        if not details:
            raise ValueError("BindingCheckError 至少需要一条错误明细")
        self.details = list(details)
        super().__init__(self.details[0]["message"])


@dataclass(frozen=True)
class BookletRange:
    """一册对应的原稿区间（页码均为 1-based，闭区间）。"""

    index: int
    start_page: int
    end_page: int
    padded_pages: int

    @property
    def original_pages(self) -> int:
        return self.end_page - self.start_page + 1

    @property
    def sheet_count(self) -> int:
        return self.padded_pages // 4

    @property
    def blank_pages(self) -> int:
        return self.padded_pages - self.original_pages


@dataclass(frozen=True)
class ChapterBookletRange:
    """按章节分出的一册：章节区间与全局原稿页区间（均为 1-based 闭区间）。"""

    index: int
    start_chapter: int
    end_chapter: int
    start_page: int
    end_page: int
    padded_pages: int

    @property
    def original_pages(self) -> int:
        return self.end_page - self.start_page + 1

    @property
    def sheet_count(self) -> int:
        return self.padded_pages // 4

    @property
    def blank_pages(self) -> int:
        return self.padded_pages - self.original_pages


def _ceil_to_multiple_of_four(value: int) -> int:
    return ((value + 3) // 4) * 4


def split_booklets(total_pages: int, max_sheets_per_booklet: int) -> list[BookletRange]:
    """把连续原稿页分册，并返回每册补齐到 4 的倍数后的页数。

    分册时先装满前面各册（每册 ``4 * max_sheets_per_booklet`` 页），
    剩余页归入最后一册，仅最后一册可能需要补空白页。
    """
    capacity = 4 * max_sheets_per_booklet
    booklets: list[BookletRange] = []
    start = 1
    index = 0
    while start <= total_pages:
        end = min(total_pages, start + capacity - 1)
        original = end - start + 1
        booklets.append(
            BookletRange(
                index=index,
                start_page=start,
                end_page=end,
                padded_pages=_ceil_to_multiple_of_four(original),
            )
        )
        start = end + 1
        index += 1
    return booklets


def impose_padded_booklet(padded_pages: int) -> list[dict[str, dict[str, int]]]:
    """对一册已补齐到 4 的倍数的版面做拼版。

    返回纸张列表（从外到内），每张纸为
    ``{"front": {"left": ..., "right": ...}, "back": {...}}``，
    页码为该册补齐版面上的 1-based 编号，空白页用 0 表示。
    """
    if padded_pages <= 0 or padded_pages % 4 != 0:
        raise ValueError("padded_pages 必须是正的 4 的倍数")

    n = padded_pages
    sheets: list[dict[str, dict[str, int]]] = []
    for i in range(n // 4):
        sheets.append(
            {
                "front": {"left": n - 2 * i, "right": 1 + 2 * i},
                "back": {"left": 2 + 2 * i, "right": n - 1 - 2 * i},
            }
        )
    return sheets


def _to_original(position: int, booklet: BookletRange | ChapterBookletRange) -> int | None:
    """把补齐版面位置（1-based）映射为原稿页码。

    补白页统一追加在原稿之后，因此位置大于本册原稿页数的版面即为空白页。
    """
    if position > booklet.original_pages:
        return None
    return booklet.start_page + position - 1


def _build_booklet_sheets(
    booklet: BookletRange | ChapterBookletRange,
) -> list[dict]:
    """对一册做拼版，返回从外到内的纸张列表（页码为全局原稿页码，补白为 None）。"""
    layout = impose_padded_booklet(booklet.padded_pages)
    return [
        {
            "index": i,
            "front": {
                "left": _to_original(sheet["front"]["left"], booklet),
                "right": _to_original(sheet["front"]["right"], booklet),
            },
            "back": {
                "left": _to_original(sheet["back"]["left"], booklet),
                "right": _to_original(sheet["back"]["right"], booklet),
            },
        }
        for i, sheet in enumerate(layout)
    ]


def build_imposition(total_pages: int, max_sheets_per_booklet: int) -> dict:
    """完整拼版：分册 -> 补齐 -> 每册从外到内给出正反面左右页码。"""
    booklets = split_booklets(total_pages, max_sheets_per_booklet)
    result_booklets: list[dict] = []

    for booklet in booklets:
        result_booklets.append(
            {
                "index": booklet.index,
                "start_page": booklet.start_page,
                "end_page": booklet.end_page,
                "original_pages": booklet.original_pages,
                "padded_pages": booklet.padded_pages,
                "blank_pages": booklet.blank_pages,
                "sheet_count": booklet.sheet_count,
                "sheets": _build_booklet_sheets(booklet),
            }
        )

    return {
        "total_pages": total_pages,
        "max_sheets_per_booklet": max_sheets_per_booklet,
        "booklet_count": len(booklets),
        "total_sheets": sum(b.sheet_count for b in booklets),
        "booklets": result_booklets,
    }


def split_chapter_booklets(
    chapter_pages: Sequence[int], max_sheets_per_booklet: int
) -> list[ChapterBookletRange]:
    """按章节分册：章节不可拆分，仅相邻章节可合入同一册。

    每册页数不得超过 ``4 * max_sheets_per_booklet``。在所有满足该约束的
    分册方案中，依次按以下目标择优：

    1. 总纸张数最小（每册纸张数 = 册内页数补齐到 4 的倍数后 ÷ 4）；
    2. 册数最小；
    3. 各册结束章节序号（1-based）组成的序列字典序最小。

    任一章节页数超过单册容量时抛出 :class:`ChapterTooLargeError`。
    """
    capacity = 4 * max_sheets_per_booklet
    chapters = list(chapter_pages)
    for position, pages in enumerate(chapters, start=1):
        if pages > capacity:
            raise ChapterTooLargeError(position, pages, capacity)

    n = len(chapters)
    prefix = [0] * (n + 1)
    for i, pages in enumerate(chapters, start=1):
        prefix[i] = prefix[i - 1] + pages

    # 动态规划：best[i] 为前 i 章的最优 (总纸张数, 册数, 结束章节序号序列)，
    # 三者按字典序比较即对应上述依次最小化的择优目标。
    best: list[tuple[int, int, tuple[int, ...]] | None] = [None] * (n + 1)
    best[0] = (0, 0, ())
    for i in range(1, n + 1):
        for j in range(i - 1, -1, -1):
            pages = prefix[i] - prefix[j]
            if pages > capacity:
                break
            prev = best[j]
            if prev is None:
                continue
            candidate = (
                prev[0] + _ceil_to_multiple_of_four(pages) // 4,
                prev[1] + 1,
                prev[2] + (i,),
            )
            if best[i] is None or candidate < best[i]:
                best[i] = candidate

    # 单章超容量已在上游拒绝，最优分册必然存在。
    assert best[n] is not None
    end_chapters = best[n][2]

    booklets: list[ChapterBookletRange] = []
    start_chapter = 1
    for index, end_chapter in enumerate(end_chapters):
        start_page = prefix[start_chapter - 1] + 1
        end_page = prefix[end_chapter]
        booklets.append(
            ChapterBookletRange(
                index=index,
                start_chapter=start_chapter,
                end_chapter=end_chapter,
                start_page=start_page,
                end_page=end_page,
                padded_pages=_ceil_to_multiple_of_four(end_page - start_page + 1),
            )
        )
        start_chapter = end_chapter + 1
    return booklets


def build_chapter_imposition(
    chapter_pages: Sequence[int], max_sheets_per_booklet: int
) -> dict:
    """按章节分册的完整拼版：最优分册 -> 补齐 -> 每册从外到内给出正反面左右页码。

    页码为全局原稿页码（跨册连续编号），补白页为 ``None``。
    """
    chapters = list(chapter_pages)
    booklets = split_chapter_booklets(chapters, max_sheets_per_booklet)
    result_booklets: list[dict] = []

    for booklet in booklets:
        result_booklets.append(
            {
                "index": booklet.index,
                "start_chapter": booklet.start_chapter,
                "end_chapter": booklet.end_chapter,
                "start_page": booklet.start_page,
                "end_page": booklet.end_page,
                "original_pages": booklet.original_pages,
                "padded_pages": booklet.padded_pages,
                "blank_pages": booklet.blank_pages,
                "sheet_count": booklet.sheet_count,
                "sheets": _build_booklet_sheets(booklet),
            }
        )

    return {
        "chapter_pages": chapters,
        "max_sheets_per_booklet": max_sheets_per_booklet,
        "chapter_count": len(chapters),
        "total_pages": sum(chapters),
        "booklet_count": len(booklets),
        "total_sheets": sum(b.sheet_count for b in booklets),
        "total_blank_pages": sum(b.blank_pages for b in booklets),
        "booklets": result_booklets,
    }


def build_reprint(
    total_pages: int,
    max_sheets_per_booklet: int,
    damaged_ranges: Iterable[tuple[int, int]],
) -> dict:
    """按现有分册与拼版规则，定位承载受损原稿页的整张纸（补印清单）。

    ``damaged_ranges`` 为若干 ``(start_page, end_page)`` 闭区间（1-based），
    调用方（schema 层）需已保证区间非倒置、未越界且列表非空。

    - 区间允许相互交叠：同一纸张只返回一次；
    - 结果按分册序号升序、同册纸张从外到内（序号升序）排列；
    - 每张纸给出正反面左右原稿页码（补白为 ``None``）以及落在该纸上的、
      去重升序排列的受损页码。
    """
    damaged_pages: set[int] = set()
    for start, end in damaged_ranges:
        damaged_pages.update(range(start, end + 1))

    imposition = build_imposition(total_pages, max_sheets_per_booklet)
    sheets_out: list[dict] = []

    for booklet in imposition["booklets"]:
        for sheet in booklet["sheets"]:
            page_positions = (
                sheet["front"]["left"],
                sheet["front"]["right"],
                sheet["back"]["left"],
                sheet["back"]["right"],
            )
            hit = sorted(p for p in page_positions if p in damaged_pages)
            if not hit:
                continue
            # build_imposition 的输出本就按分册序号、纸张从外到内排列。
            sheets_out.append(
                {
                    "booklet_index": booklet["index"],
                    "sheet_index": sheet["index"],
                    "front": sheet["front"],
                    "back": sheet["back"],
                    "damaged_pages": hit,
                }
            )

    return {
        "total_pages": total_pages,
        "max_sheets_per_booklet": max_sheets_per_booklet,
        "booklet_count": imposition["booklet_count"],
        "reprint_sheet_count": len(sheets_out),
        "sheets": sheets_out,
    }


def build_manual_duplex(
    total_pages: int,
    max_sheets_per_booklet: int,
    refill_reversed: bool,
    flip_edge: str,
) -> dict:
    """根据现有普通拼版结果，生成每册独立的两轮手工双面打印清单。

    每册纸分两轮手工送纸：

    - **第一轮**：纸张按外到内（``sheet_index`` 升序）打印各自正面，
      左右版面与拼版结果一致；
    - **第二轮**：把首轮出纸堆回填进纸口后打印对应反面：
      - ``refill_reversed`` 为真（倒序回填）时，第二轮纸张次序反转，
        即从最内侧纸印到最外侧纸；否则与首轮同序；
      - ``flip_edge="long"``（沿长边翻转回填）时，交换反面的左右版面；
        ``flip_edge="short"``（沿短边翻转回填）时左右保持不变。

    每条记录给出轮次内序号（``sequence``，从 0 开始）、册号
    （``booklet_index``）、纸张序号（``sheet_index``，0 为最外侧纸）与
    该次打印的左右原稿页码，补白沿用 ``None``。
    """
    if flip_edge not in ("short", "long"):
        raise ValueError('flip_edge 必须为 "short"（短边）或 "long"（长边）')

    imposition = build_imposition(total_pages, max_sheets_per_booklet)
    result_booklets: list[dict] = []

    for booklet in imposition["booklets"]:
        sheets = booklet["sheets"]

        # 第一轮：外到内打印正面，版面与拼版结果一致。
        front_entries = [
            {
                "sequence": seq,
                "booklet_index": booklet["index"],
                "sheet_index": sheet["index"],
                "left": sheet["front"]["left"],
                "right": sheet["front"]["right"],
            }
            for seq, sheet in enumerate(sheets)
        ]

        # 第二轮：按回填后的实际入纸顺序打印反面；倒序回填反转纸张次序。
        back_sheets = list(reversed(sheets)) if refill_reversed else sheets
        back_entries: list[dict] = []
        for seq, sheet in enumerate(back_sheets):
            left = sheet["back"]["left"]
            right = sheet["back"]["right"]
            if flip_edge == "long":
                # 沿长边翻转：反面左右版面互换。
                left, right = right, left
            # 沿短边翻转：左右保持不变。
            back_entries.append(
                {
                    "sequence": seq,
                    "booklet_index": booklet["index"],
                    "sheet_index": sheet["index"],
                    "left": left,
                    "right": right,
                }
            )

        result_booklets.append(
            {
                "index": booklet["index"],
                "sheet_count": booklet["sheet_count"],
                "rounds": [
                    {"round": 1, "side": "front", "entries": front_entries},
                    {"round": 2, "side": "back", "entries": back_entries},
                ],
            }
        )

    return {
        "total_pages": total_pages,
        "max_sheets_per_booklet": max_sheets_per_booklet,
        "refill_reversed": refill_reversed,
        "flip_edge": flip_edge,
        "booklet_count": imposition["booklet_count"],
        "total_sheets": imposition["total_sheets"],
        "booklets": result_booklets,
    }


# 纸张四种摆放方式：(正反面是否互换, 每面左右是否互换)。
# 左右互换（横向调头）与正反互换（沿短边翻转）可任意组合且彼此交换。
_ORIENTATION_OPS: tuple[tuple[str, bool, bool], ...] = (
    ("none", False, False),
    ("swap_left_right", False, True),
    ("swap_front_back", True, False),
    ("swap_left_right_front_back", True, True),
)

# 单张纸所需互换操作的固定输出顺序（两操作可交换，仅与摆放方式有关）。
_OPERATION_LABELS = {
    "swap_left_right": ["swap_left_right"],
    "swap_front_back": ["swap_front_back"],
    "swap_left_right_front_back": ["swap_front_back", "swap_left_right"],
}


def _sheet_tuple(sheet: dict) -> tuple:
    """提取纸张四元组 (front.left, front.right, back.left, back.right)。"""
    return (
        sheet["front"]["left"],
        sheet["front"]["right"],
        sheet["back"]["left"],
        sheet["back"]["right"],
    )


def _oriented_tuple(t: tuple, swap_sides: bool, swap_left_right: bool) -> tuple:
    """在四元组上施加正反互换 / 左右互换后的结果。

    正反互换沿短边翻转：正面与反面对调，各自的左右位置保持不变；
    左右互换为同一面左右对调。两者可交换（克莱因四元群）。
    """
    fl, fr, bl, br = t
    if swap_sides:
        fl, fr, bl, br = bl, br, fl, fr
    if swap_left_right:
        fl, fr, bl, br = fr, fl, br, bl
    return fl, fr, bl, br


def _minimum_swaps_to_identity(
    current_position: Sequence[int],
) -> list[dict[str, int]]:
    """求把纸张排列还原为外到内（恒等排列）的最少两两交换步骤。

    ``current_position[k]`` 表示应在位置 ``k`` 的纸当前所在位置（排列 p：
    目标位置 -> 当前位置）。长度为 L 的非平凡循环需且仅需 L-1 次交换，
    故最少交换数为 n - 循环数；交换位置 a、b（即转置排列中的值 a、b）不
    增加剩余交换数，当且仅当 a、b 位于同一非平凡循环。

    每一步在所有可行候选中取 (a, b) 字典序最小者（恒取 a < b），即得到
    等长方案里步骤序列字典序最小的解。
    """
    perm = list(current_position)
    n = len(perm)
    steps: list[dict[str, int]] = []

    def same_cycle(a: int, b: int) -> bool:
        """排列 p 中 a、b 是否同循环（沿 p 从 a 出发能否到达 b）。"""
        cursor = perm[a]
        while cursor != a:
            if cursor == b:
                return True
            cursor = perm[cursor]
        return False

    while True:
        chosen: tuple[int, int] | None = None
        for a in range(n):
            for b in range(a + 1, n):
                if same_cycle(a, b) and (chosen is None or (a, b) < chosen):
                    chosen = (a, b)
        if chosen is None:
            break
        a, b = chosen
        # 互换物理位置 a、b 上的两张纸：转置 perm 中的值 a、b。
        ia = next(i for i, pos in enumerate(perm) if pos == a)
        ib = next(i for i, pos in enumerate(perm) if pos == b)
        perm[ia], perm[ib] = perm[ib], perm[ia]
        steps.append({"position_a": a, "position_b": b})
    return steps


def build_binding_check(
    total_pages: int,
    max_sheets_per_booklet: int,
    booklet_index: int,
    sheets: Sequence[dict],
) -> dict:
    """核对单册待装订纸张与现有拼版结果是否一致。

    ``sheets`` 按**当前从外到内**的叠放顺序给出，每张含
    ``front/back.left/right`` 原稿页码，补白为 ``None``。纸张以
    **非空页码集合**识别；以下情况返回（由调用方转成 HTTP 400 的）
    :class:`BindingCheckError`：

    - 出现不属于该册页码区间的非空页码（跨册页 / 越界页）；
    - 同一基准纸张被重复提交（按非空页码集合）；
    - 某张纸无法通过左右互换、正反互换（及其组合）还原成基准版面；
    - 基准纸张缺失（含提交了基准中不存在的纸面）。

    通过核对时返回每张纸当前位置与应在位置、所需互换操作，以及把整叠纸
    恢复为外到内顺序的最少两两交换步骤（等长方案取字典序最小）。
    """
    imposition = build_imposition(total_pages, max_sheets_per_booklet)
    if not 0 <= booklet_index < imposition["booklet_count"]:
        # schema 层通常已拦截，此处兜底。
        raise BindingCheckError(
            [
                {
                    "field": "booklet_index",
                    "message": (
                        "booklet_index 越界：必须在 0 到"
                        f" {imposition['booklet_count'] - 1} 之间。"
                    ),
                }
            ]
        )

    booklet = imposition["booklets"][booklet_index]
    start_page = booklet["start_page"]
    end_page = booklet["end_page"]
    expected_sheets = booklet["sheets"]
    expected_count = len(expected_sheets)

    # 基准纸张：非空页码集合 -> (应在位置, 标准四元组)。
    expected_by_key: dict[frozenset[int], tuple[int, tuple]] = {}
    for sheet in expected_sheets:
        key = frozenset(p for p in _sheet_tuple(sheet) if p is not None)
        expected_by_key[key] = (sheet["index"], _sheet_tuple(sheet))

    errors: list[dict[str, str]] = []

    # 1) 跨册 / 越界页检查（页码在本册区间之外即判定跨册）。
    cross_indices: set[int] = set()
    for idx, sheet in enumerate(sheets):
        pages = [p for p in _sheet_tuple(sheet) if p is not None]
        foreign = sorted(
            {p for p in pages if not (start_page <= p <= end_page)}
        )
        if foreign:
            cross_indices.add(idx)
            errors.append(
                {
                    "field": f"sheets[{idx}]",
                    "message": (
                        f"sheets[{idx}] 含跨册（或越界）页码 "
                        f"{foreign}：第 {booklet_index} 册的原稿页区间为"
                        f" {start_page}–{end_page}。"
                    ),
                }
            )

    # 2) 按非空页码集合识别纸张：重复纸检查。
    seen_indices: dict[frozenset[int], int] = {}
    duplicate_indices: set[int] = set()
    for idx, sheet in enumerate(sheets):
        if idx in cross_indices:
            continue
        key = frozenset(p for p in _sheet_tuple(sheet) if p is not None)
        if key in seen_indices:
            duplicate_indices.add(idx)
            errors.append(
                {
                    "field": f"sheets[{idx}]",
                    "message": (
                        f"sheets[{idx}] 与 sheets[{seen_indices[key]}] 是同一张纸"
                        "（非空页码集合相同）：纸张不得重复。"
                    ),
                }
            )
        else:
            seen_indices[key] = idx

    # 3) 版面还原检查 + 收集每张有效纸相对基准的摆放方式。
    #    orientation[idx] = (应在位置, 摆放方式名)
    orientation: dict[int, tuple[int, str]] = {}
    # 非空页码集合在本册基准中存在（且未跨册、未重复）即说明物理纸并未缺失，
    # 哪怕版面无法还原也只是“摆错/印错”，不再重复报缺纸。
    present_targets: set[int] = set()
    for idx, sheet in enumerate(sheets):
        if idx in cross_indices or idx in duplicate_indices:
            continue
        key = frozenset(p for p in _sheet_tuple(sheet) if p is not None)
        match = expected_by_key.get(key)
        if match is None:
            # 基准中不存在这组页码：该纸无法归入本册任何一张，按缺/多纸处理。
            errors.append(
                {
                    "field": f"sheets[{idx}]",
                    "message": (
                        f"sheets[{idx}] 的非空页码集合 {sorted(key)} 不属于第"
                        f" {booklet_index} 册的任何基准纸张。"
                    ),
                }
            )
            continue
        target_index, target_tuple = match
        present_targets.add(target_index)
        observed = _sheet_tuple(sheet)
        matched_op: str | None = None
        for name, swap_sides, swap_left_right in _ORIENTATION_OPS:
            if _oriented_tuple(target_tuple, swap_sides, swap_left_right) == observed:
                matched_op = name
                break
        if matched_op is None:
            errors.append(
                {
                    "field": f"sheets[{idx}]",
                    "message": (
                        f"sheets[{idx}] 的版面无法通过左右互换及正反互换还原"
                        f"（应为第 {target_index} 张基准纸）。"
                    ),
                }
            )
            continue
        orientation[idx] = (target_index, matched_op)

    # 4) 缺纸检查：基准纸张中没有被任何有效提交匹配到的。
    for sheet in expected_sheets:
        if sheet["index"] not in present_targets:
            errors.append(
                {
                    "field": "sheets",
                    "message": (
                        f"缺少基准纸张：应在位置 {sheet['index']}"
                        "（从外到内，0 为最外侧）。"
                    ),
                }
            )

    if errors:
        # 明细按字段名（提交位置）稳定排序，缺纸明细集中在 sheets 字段内，
        # 其加入顺序即应在位置升序。
        raise BindingCheckError(errors)

    # 全部识别成功：构建每张纸的当前 / 应在位置与所需互换操作。
    result_sheets: list[dict] = []
    # current_position[k]：应在位置 k 的纸当前所在位置。
    current_position = [0] * expected_count
    for current_index, (target_index, op_name) in orientation.items():
        current_position[target_index] = current_index
        result_sheets.append(
            {
                "current_index": current_index,
                "expected_index": target_index,
                "required_operations": _OPERATION_LABELS.get(op_name, []),
                "front": dict(sheets[current_index]["front"]),
                "back": dict(sheets[current_index]["back"]),
            }
        )

    swap_steps = _minimum_swaps_to_identity(current_position)
    return {
        "total_pages": total_pages,
        "max_sheets_per_booklet": max_sheets_per_booklet,
        "booklet_index": booklet_index,
        "start_page": start_page,
        "end_page": end_page,
        "sheet_count": expected_count,
        "sheets": result_sheets,
        "swap_step_count": len(swap_steps),
        "swap_steps": swap_steps,
    }
