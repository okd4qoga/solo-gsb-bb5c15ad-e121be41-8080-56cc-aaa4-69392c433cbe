"""请求 / 响应数据模型。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationInfo,
    field_validator,
)
from pydantic_core import PydanticCustomError


class ImpositionRequest(BaseModel):
    """拼版请求。

    ``StrictInt`` 会拒绝布尔值、浮点数与数字字符串，只接受真正的整数；
    ``ge=1`` 进一步保证为正整数。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    total_pages: StrictInt = Field(
        ..., ge=1, description="原稿总页数，必须为正整数。", examples=[16]
    )
    max_sheets_per_booklet: StrictInt = Field(
        ...,
        ge=1,
        description="每册最多纸张数（1 张纸正反 4 个版面），必须为正整数。",
        examples=[2],
    )


class SidePages(BaseModel):
    left: int | None = Field(..., description="左侧页码；null 表示空白页。")
    right: int | None = Field(..., description="右侧页码；null 表示空白页。")


class SheetLayout(BaseModel):
    index: int = Field(..., description="纸张序号，0 为最外侧纸张。")
    front: SidePages = Field(..., description="正面（朝外一面）左右页码。")
    back: SidePages = Field(..., description="反面（朝内一面）左右页码。")


class BookletLayout(BaseModel):
    index: int = Field(..., description="分册序号，从 0 开始。")
    start_page: int = Field(..., description="本册起始原稿页码（含）。")
    end_page: int = Field(..., description="本册结束原稿页码（含）。")
    original_pages: int = Field(..., description="本册原稿页数。")
    padded_pages: int = Field(..., description="补齐后的版面页数（4 的倍数）。")
    blank_pages: int = Field(..., description="补位空白页数。")
    sheet_count: int = Field(..., description="本册实际用纸张数。")
    sheets: list[SheetLayout] = Field(..., description="纸张列表，从外到内。")


class ImpositionResponse(BaseModel):
    total_pages: int
    max_sheets_per_booklet: int
    booklet_count: int = Field(..., description="分册数量。")
    total_sheets: int = Field(..., description="全部册合计用纸张数。")
    booklets: list[BookletLayout]


class DamagedRange(BaseModel):
    """一段受损原稿页区间（端点均包含，1-based）。"""

    model_config = ConfigDict(extra="forbid")

    start_page: StrictInt = Field(..., ge=1, description="受损区间起始页（含）。")
    end_page: StrictInt = Field(..., ge=1, description="受损区间结束页（含）。")


class ReprintRequest(BaseModel):
    """补印请求：总页数、每册最多纸张数与非空的受损页区间列表。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    total_pages: StrictInt = Field(
        ..., ge=1, description="原稿总页数，必须为正整数。", examples=[16]
    )
    max_sheets_per_booklet: StrictInt = Field(
        ...,
        ge=1,
        description="每册最多纸张数（1 张纸正反 4 个版面），必须为正整数。",
        examples=[4],
    )
    damaged_ranges: list[DamagedRange] = Field(
        ...,
        min_length=1,
        description="受损原稿页区间列表（至少一项），端点均包含，允许交叠。",
    )

    @field_validator("damaged_ranges")
    @classmethod
    def _validate_ranges(
        cls, ranges: list[DamagedRange], info: ValidationInfo
    ) -> list[DamagedRange]:
        total_pages = info.data.get("total_pages")
        for range_ in ranges:
            if range_.start_page > range_.end_page:
                raise PydanticCustomError(
                    "reversed_range",
                    "受损页区间不能倒置：start_page 必须小于等于 end_page。",
                )
            # total_pages 自身非法（缺失 / 非正整数）时已有对应校验错误，跳过越界检查。
            if total_pages is not None and range_.end_page > total_pages:
                raise PydanticCustomError(
                    "page_out_of_range",
                    "受损页区间越界：页码必须在 1 到 total_pages 之间。",
                )
        return ranges


class ReprintSheet(BaseModel):
    booklet_index: int = Field(..., description="分册序号，从 0 开始。")
    sheet_index: int = Field(..., description="纸张序号，0 为该册最外侧纸张。")
    front: SidePages = Field(..., description="正面（朝外一面）左右页码。")
    back: SidePages = Field(..., description="反面（朝内一面）左右页码。")
    damaged_pages: list[int] = Field(
        ..., description="落在该纸上的受损原稿页码，去重并升序排列。"
    )


class ReprintResponse(BaseModel):
    total_pages: int
    max_sheets_per_booklet: int
    booklet_count: int = Field(..., description="分册数量。")
    reprint_sheet_count: int = Field(..., description="需要补印的整张纸数量（已去重）。")
    sheets: list[ReprintSheet] = Field(
        ..., description="需补印的纸张，按分册序号、纸张从外到内排序。"
    )


class ChapterImpositionRequest(BaseModel):
    """按章节分册的拼版请求。

    ``chapter_pages`` 为非空的有序章节页数数组（章节不可拆分，仅相邻章节可合册）；
    字段定义顺序保证校验 ``chapter_pages`` 时 ``max_sheets_per_booklet`` 已就绪。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    max_sheets_per_booklet: StrictInt = Field(
        ...,
        ge=1,
        description="每册最多纸张数（1 张纸正反 4 个版面），必须为正整数。",
        examples=[2],
    )
    chapter_pages: list[Annotated[StrictInt, Field(ge=1)]] = Field(
        ...,
        min_length=1,
        description="有序章节页数数组（至少一章），每项为正整数页数。",
        examples=[[6, 5, 3]],
    )

    @field_validator("chapter_pages")
    @classmethod
    def _validate_chapters_fit(
        cls, chapters: list[int], info: ValidationInfo
    ) -> list[int]:
        max_sheets = info.data.get("max_sheets_per_booklet")
        # max_sheets_per_booklet 自身非法时已有对应校验错误，跳过容量检查。
        if max_sheets is None:
            return chapters
        capacity = 4 * max_sheets
        for position, pages in enumerate(chapters, start=1):
            if pages > capacity:
                raise PydanticCustomError(
                    "chapter_too_large",
                    "第 {chapter} 章共 {pages} 页，超过单册容量上限 {capacity} 页"
                    "（4 × max_sheets_per_booklet），章节不可拆分。",
                    {
                        "chapter": position,
                        "pages": pages,
                        "capacity": capacity,
                    },
                )
        return chapters


class ChapterBookletLayout(BaseModel):
    index: int = Field(..., description="分册序号，从 0 开始。")
    start_chapter: int = Field(..., description="本册起始章节序号（含，从 1 开始）。")
    end_chapter: int = Field(..., description="本册结束章节序号（含，从 1 开始）。")
    start_page: int = Field(..., description="本册起始全局原稿页码（含）。")
    end_page: int = Field(..., description="本册结束全局原稿页码（含）。")
    original_pages: int = Field(..., description="本册原稿页数。")
    padded_pages: int = Field(..., description="补齐后的版面页数（4 的倍数）。")
    blank_pages: int = Field(..., description="补位空白页数。")
    sheet_count: int = Field(..., description="本册实际用纸张数。")
    sheets: list[SheetLayout] = Field(..., description="纸张列表，从外到内。")


class ChapterImpositionResponse(BaseModel):
    chapter_pages: list[int]
    max_sheets_per_booklet: int
    chapter_count: int = Field(..., description="章节数量。")
    total_pages: int = Field(..., description="原稿总页数（各章页数之和）。")
    booklet_count: int = Field(..., description="分册数量。")
    total_sheets: int = Field(..., description="全部册合计用纸张数。")
    total_blank_pages: int = Field(..., description="全部册合计补位空白页数。")
    booklets: list[ChapterBookletLayout]


# 允许 null 的严格正整数页码：拒绝 0、负数、小数、布尔值、数字字符串。
NullableStrictPage = Annotated[StrictInt, Field(ge=1)]


class BindingCheckSidePages(BaseModel):
    """装订核对输入的一面：左右原稿页码，补白为 null。"""

    model_config = ConfigDict(extra="forbid")

    left: NullableStrictPage | None = Field(..., description="左侧页码；null 表示空白页。")
    right: NullableStrictPage | None = Field(..., description="右侧页码；null 表示空白页。")


class BindingCheckSheetInput(BaseModel):
    """待装订纸张：记录当前摆放方式下正反面左右原稿页码。"""

    model_config = ConfigDict(extra="forbid")

    front: BindingCheckSidePages = Field(..., description="当前正面（朝外一面）左右页码。")
    back: BindingCheckSidePages = Field(..., description="当前反面（朝内一面）左右页码。")


class BindingCheckRequest(BaseModel):
    """装订核对请求：分册参数、册序号与该册当前从外到内的纸张列表。

    ``sheets`` 至少一张；纸张以非空页码集合识别，结构语义（缺纸 / 重复纸 /
    跨册页 / 版面无法还原）在核心算法层核对并返回 400。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    total_pages: StrictInt = Field(
        ..., ge=1, description="原稿总页数，必须为正整数。", examples=[16]
    )
    max_sheets_per_booklet: StrictInt = Field(
        ...,
        ge=1,
        description="每册最多纸张数（1 张纸正反 4 个版面），必须为正整数。",
        examples=[4],
    )
    booklet_index: StrictInt = Field(
        ..., ge=0, description="待核对分册的序号，从 0 开始，必须为非负整数。"
    )
    sheets: list[BindingCheckSheetInput] = Field(
        ...,
        min_length=1,
        description="该册纸张，按当前从外到内顺序记录；每张给出正反面左右页码，补白为 null。",
    )


class BindingCheckSheetResult(BaseModel):
    current_index: int = Field(..., description="该纸当前所在位置（0 为最外侧）。")
    expected_index: int = Field(..., description="该纸按拼版结果应在位置（0 为最外侧）。")
    required_operations: list[str] = Field(
        ...,
        description=(
            "还原该纸版面所需互换操作，仅可能包含 swap_left_right（左右互换）"
            "与 swap_front_back（正反互换）；无需调整时为空列表。"
        ),
    )
    front: SidePages = Field(..., description="该纸当前正面（朝外一面）左右页码。")
    back: SidePages = Field(..., description="该纸当前反面（朝内一面）左右页码。")


class BindingCheckSwapStep(BaseModel):
    position_a: int = Field(..., description="本次两两交换的位置 a（0 为最外侧，恒有 a < b）。")
    position_b: int = Field(..., description="本次两两交换的位置 b。")


class BindingCheckResponse(BaseModel):
    total_pages: int
    max_sheets_per_booklet: int
    booklet_index: int = Field(..., description="核对的分册序号。")
    start_page: int = Field(..., description="该册起始原稿页码（含）。")
    end_page: int = Field(..., description="该册结束原稿页码（含）。")
    sheet_count: int = Field(..., description="该册基准纸张数。")
    sheets: list[BindingCheckSheetResult] = Field(
        ..., description="各纸核对结果，按提交顺序（当前从外到内）排列。"
    )
    swap_step_count: int = Field(..., description="恢复外到内顺序所需最少两两交换次数。")
    swap_steps: list[BindingCheckSwapStep] = Field(
        ...,
        description=(
            "最少两两交换步骤序列（等长方案按步骤序列字典序选择）；"
            "依次执行后整叠纸恢复为基准的从外到内顺序。"
        ),
    )


class ManualDuplexRequest(BaseModel):
    """手工双面打印清单请求：分册参数 + 首轮出纸堆回填方式。

    - ``refill_reversed``：首轮出纸堆是否倒序回填（真则第二轮纸张次序反转）；
    - ``flip_edge``：回填时翻转方向，``short`` 为沿短边翻转（反面左右不变），
      ``long`` 为沿长边翻转（交换反面左右版面）。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    total_pages: StrictInt = Field(
        ..., ge=1, description="原稿总页数，必须为正整数。", examples=[8]
    )
    max_sheets_per_booklet: StrictInt = Field(
        ...,
        ge=1,
        description="每册最多纸张数（1 张纸正反 4 个版面），必须为正整数。",
        examples=[4],
    )
    refill_reversed: StrictBool = Field(
        ...,
        description=(
            "首轮出纸堆是否倒序回填：true 表示倒序回填（第二轮反转纸张次序），"
            "false 表示按首轮出纸顺序原样回填。"
        ),
        examples=[True],
    )
    flip_edge: Literal["short", "long"] = Field(
        ...,
        description=(
            "回填时沿哪条边翻转纸张：short 为沿短边翻转（反面左右版面不变），"
            "long 为沿长边翻转（交换反面左右版面）。"
        ),
        examples=["long"],
    )


class ManualDuplexEntry(BaseModel):
    """单轮中的一条打印记录：某张纸在该轮的入纸位置与左右页码。"""

    sequence: int = Field(..., description="轮次内序号（入纸顺序），从 0 开始。")
    booklet_index: int = Field(..., description="册号（分册序号），从 0 开始。")
    sheet_index: int = Field(..., description="纸张序号，0 为该册最外侧纸张。")
    left: int | None = Field(..., description="本次打印的左侧原稿页码；null 表示空白页。")
    right: int | None = Field(..., description="本次打印的右侧原稿页码；null 表示空白页。")


class ManualDuplexRound(BaseModel):
    """一轮打印清单：第一轮正面、第二轮反面。"""

    round: int = Field(..., description="轮次：1 为首轮（正面），2 为第二轮（反面）。")
    side: Literal["front", "back"] = Field(
        ..., description="本轮打印的面：front 正面 / back 反面。"
    )
    entries: list[ManualDuplexEntry] = Field(
        ..., description="按本轮实际入纸顺序排列的打印记录。"
    )


class ManualDuplexBooklet(BaseModel):
    index: int = Field(..., description="分册序号，从 0 开始。")
    sheet_count: int = Field(..., description="本册实际用纸张数（两轮记录数相同）。")
    rounds: list[ManualDuplexRound] = Field(
        ..., description="该册独立的两轮打印清单：第一轮正面，第二轮反面。"
    )


class ManualDuplexResponse(BaseModel):
    total_pages: int
    max_sheets_per_booklet: int
    refill_reversed: bool = Field(
        ..., description="首轮出纸堆是否倒序回填（回填时是否反转纸张次序）。"
    )
    flip_edge: Literal["short", "long"] = Field(
        ..., description="回填翻转边：short 短边（左右不变）/ long 长边（交换左右）。"
    )
    booklet_count: int = Field(..., description="分册数量。")
    total_sheets: int = Field(..., description="全部册合计用纸张数。")
    booklets: list[ManualDuplexBooklet]
