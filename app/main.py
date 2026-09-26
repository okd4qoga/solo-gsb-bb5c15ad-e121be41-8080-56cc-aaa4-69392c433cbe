"""FastAPI 入口：骑马钉小册子拼版 API。"""

from __future__ import annotations

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .imposition import (
    BindingCheckError,
    ChapterTooLargeError,
    build_binding_check,
    build_chapter_imposition,
    build_color_plan,
    build_imposition,
    build_manual_duplex,
    build_reprint,
)
from .schemas import (
    BindingCheckRequest,
    BindingCheckResponse,
    ChapterImpositionRequest,
    ChapterImpositionResponse,
    ColorPlanRequest,
    ColorPlanResponse,
    ImpositionRequest,
    ImpositionResponse,
    ManualDuplexRequest,
    ManualDuplexResponse,
    ReprintRequest,
    ReprintResponse,
)

app = FastAPI(
    title="骑马钉小册子拼版 API",
    description=(
        "输入原稿总页数与每册最多纸张数，按骑马钉（对折装订）方式分册拼版，"
        "返回每张纸正反面各自的左、右页码；补位页以 null（空白）表示。"
        "亦支持按章节分册：章节不可拆分、仅相邻章节可合册，"
        "在所有合法分册方案中依次最小化总纸张数与册数；"
        "还可生成每册独立的两轮手工双面打印清单（倒序回填、短边/长边翻转）；"
        "并可按彩印 / 黑白每张纸单价求总费用最小的分册方案：纸上任一非空页"
        "落入彩色区间即整纸彩印，交叠区间不重复计费。"
    ),
    version="1.4.0",
)


def _format_validation_errors(errors: list[dict], path: str = "") -> list[dict]:
    """把 Pydantic 校验错误整理成简洁的中文说明。"""
    formatted: list[dict] = []
    for err in errors:
        loc = [
            str(part)
            for part in err.get("loc", [])
            if part not in ("body", "query")
        ]
        field = ".".join(loc) if loc else "请求体"
        err_type = err.get("type", "")
        message = err.get("msg", "参数不合法。")

        if err_type == "missing":
            message = "缺少必填参数。"
        elif field == "refill_reversed":
            message = "必须为布尔值（true 或 false）。"
        elif field == "flip_edge":
            message = '必须为 "short"（短边翻转）或 "long"（长边翻转）。'
        elif err_type in {
            "int_type",
            "int_parsing",
            "strict_int",
            "int_from_float",
            "bool_type",
        }:
            if field == "booklet_index":
                message = "必须为非负整数（不接受小数、布尔值、字符串或空值）。"
            else:
                message = "必须为正整数（不接受小数、布尔值、字符串或空值）。"
        elif err_type in {"greater_than_equal", "ge"}:
            if field == "booklet_index":
                message = "必须为大于等于 0 的非负整数。"
            else:
                message = "必须为大于等于 1 的正整数。"
        elif err_type in {"less_than_equal", "le"}:
            message = "必须为 1 到 200 之间的整数。"
        elif err_type == "extra_forbidden":
            message = "存在不支持的多余字段。"
        elif err_type in {"model_type", "model_attributes_type"}:
            raw_loc = err.get("loc", ())
            if len(raw_loc) >= 2 and "damaged_ranges" in raw_loc:
                message = "受损页区间列表的每一项都必须是含 start_page、end_page 的对象。"
            elif len(raw_loc) >= 2 and "color_ranges" in raw_loc:
                message = "彩色页区间列表的每一项都必须是含 start_page、end_page 的对象。"
            elif field == "damaged_ranges":
                message = "damaged_ranges 必须是区间对象组成的列表。"
            elif field == "color_ranges":
                message = "color_ranges 必须是区间对象组成的列表（可为空或缺省）。"
            else:
                message = "每一张纸都必须是含 front、back 的对象，且两面各自包含 left、right。"
        elif err_type == "list_type":
            if field == "chapter_pages":
                message = "chapter_pages 必须是由正整数页数组成的数组。"
            elif field == "sheets":
                message = "sheets 必须是纸张对象组成的非空列表。"
            elif field == "color_ranges":
                message = "color_ranges 必须是区间对象组成的列表（可为空或缺省）。"
            else:
                message = "damaged_ranges 必须是区间对象组成的非空列表。"
        elif err_type in {"too_short", "string_too_short"}:
            if field == "chapter_pages":
                message = "chapter_pages 不能为空，至少提供一个章节页数。"
            elif field == "sheets":
                message = "sheets 不能为空，至少提供一张待装订纸张。"
            else:
                message = "damaged_ranges 不能为空，至少提供一个受损页区间。"
        elif err_type in {"reversed_range", "page_out_of_range", "chapter_too_large"}:
            message = message.removeprefix("Value error, ")

        formatted.append({"field": field, "message": message})
    return formatted


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """类型 / 范围 / 区间错误统一返回 HTTP 400 与明确错误信息。"""
    path = request.url.path
    if path.startswith("/api/v1/color-plan"):
        message = (
            "输入参数校验失败：total_pages 必须是 1 到 200 之间的整数，"
            "max_sheets_per_booklet、color_price_per_sheet、bw_price_per_sheet "
            "必须是正整数，color_ranges 必须是有效的彩色页区间列表"
            "（可缺省，区间不得倒置或越界）。"
        )
    elif path.startswith("/api/v1/chapter-impose"):
        message = (
            "输入参数校验失败：chapter_pages 必须为非空的正整数页数数组，"
            "max_sheets_per_booklet 必须是正整数，"
            "且任一章节页数不能超过 4 × max_sheets_per_booklet。"
        )
    elif path.startswith("/api/v1/reprint"):
        message = (
            "输入参数校验失败：total_pages 与 max_sheets_per_booklet 必须是正整数，"
            "damaged_ranges 必须为非空的有效受损页区间列表。"
        )
    elif path.startswith("/api/v1/binding-check"):
        message = (
            "输入参数校验失败：total_pages 与 max_sheets_per_booklet 必须是正整数，"
            "booklet_index 必须是非负整数，"
            "sheets 必须是非空的纸张列表，每张纸的非空页码必须为正整数。"
        )
    elif path.startswith("/api/v1/manual-duplex"):
        message = (
            "输入参数校验失败：total_pages 与 max_sheets_per_booklet 必须是正整数，"
            'refill_reversed 必须是布尔值，flip_edge 必须是 "short" 或 "long"。'
        )
    else:
        message = (
            "输入参数校验失败：total_pages 与 max_sheets_per_booklet 必须是正整数。"
        )
    return JSONResponse(
        status_code=400,
        content={
            "error": "invalid_input",
            "message": message,
            "details": _format_validation_errors(exc.errors(), path),
        },
    )


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {
        "service": "saddle-stitch-imposition",
        "docs": "/docs",
        "health": "/health",
        "impose_endpoint": "/api/v1/impose",
        "reprint_endpoint": "/api/v1/reprint",
        "chapter_impose_endpoint": "/api/v1/chapter-impose",
        "binding_check_endpoint": "/api/v1/binding-check",
        "manual_duplex_endpoint": "/api/v1/manual-duplex",
        "color_plan_endpoint": "/api/v1/color-plan",
    }


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/api/v1/impose",
    response_model=ImpositionResponse,
    tags=["imposition"],
    summary="骑马钉拼版",
)
async def impose(payload: ImpositionRequest) -> ImpositionResponse:
    result = build_imposition(
        total_pages=payload.total_pages,
        max_sheets_per_booklet=payload.max_sheets_per_booklet,
    )
    return ImpositionResponse.model_validate(result)


@app.get(
    "/api/v1/impose",
    response_model=ImpositionResponse,
    tags=["imposition"],
    summary="骑马钉拼版（GET 便捷形式，可直接在浏览器中查看）",
)
async def impose_get(
    total_pages: int = Query(..., ge=1, description="原稿总页数，正整数。"),
    max_sheets_per_booklet: int = Query(
        ..., ge=1, description="每册最多纸张数，正整数。"
    ),
) -> ImpositionResponse:
    # 查询参数的类型 / 范围错误同样由 validation_exception_handler 统一返回 400。
    result = build_imposition(total_pages, max_sheets_per_booklet)
    return ImpositionResponse.model_validate(result)


@app.post(
    "/api/v1/reprint",
    response_model=ReprintResponse,
    tags=["reprint"],
    summary="按受损页区间定位需补印的整张纸",
)
async def reprint(payload: ReprintRequest) -> ReprintResponse:
    result = build_reprint(
        total_pages=payload.total_pages,
        max_sheets_per_booklet=payload.max_sheets_per_booklet,
        damaged_ranges=[
            (item.start_page, item.end_page) for item in payload.damaged_ranges
        ],
    )
    return ReprintResponse.model_validate(result)


@app.post(
    "/api/v1/chapter-impose",
    response_model=ChapterImpositionResponse,
    tags=["imposition"],
    summary="按章节分册的骑马钉拼版（章节不可拆分，仅相邻章节可合册）",
)
async def chapter_impose(
    payload: ChapterImpositionRequest,
) -> ChapterImpositionResponse:
    try:
        result = build_chapter_imposition(
            chapter_pages=payload.chapter_pages,
            max_sheets_per_booklet=payload.max_sheets_per_booklet,
        )
    except ChapterTooLargeError as exc:
        # 单章超容量在 schema 层已拦截，此处兜底保证仍返回 400 而非 500。
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_input",
                "message": str(exc),
                "details": [{"field": "chapter_pages", "message": str(exc)}],
            },
        )
    return ChapterImpositionResponse.model_validate(result)


@app.post(
    "/api/v1/binding-check",
    response_model=BindingCheckResponse,
    tags=["binding"],
    summary="核对单册待装订纸张（缺纸 / 重复纸 / 跨册页 / 版面互换还原）",
)
async def binding_check(payload: BindingCheckRequest) -> BindingCheckResponse:
    try:
        result = build_binding_check(
            total_pages=payload.total_pages,
            max_sheets_per_booklet=payload.max_sheets_per_booklet,
            booklet_index=payload.booklet_index,
            sheets=[
                {
                    "front": {
                        "left": item.front.left,
                        "right": item.front.right,
                    },
                    "back": {
                        "left": item.back.left,
                        "right": item.back.right,
                    },
                }
                for item in payload.sheets
            ],
        )
    except BindingCheckError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_input",
                "message": (
                    "装订核对失败：存在缺纸、重复纸、跨册页，或无法通过左右互换、"
                    "正反互换还原的版面。"
                ),
                "details": exc.details,
            },
        )
    return BindingCheckResponse.model_validate(result)


@app.post(
    "/api/v1/color-plan",
    response_model=ColorPlanResponse,
    tags=["color-plan"],
    summary="彩印 / 黑白混合计费下总费用最小的分册方案",
)
async def color_plan(payload: ColorPlanRequest) -> ColorPlanResponse:
    result = build_color_plan(
        total_pages=payload.total_pages,
        max_sheets_per_booklet=payload.max_sheets_per_booklet,
        color_ranges=[
            (item.start_page, item.end_page)
            for item in payload.color_ranges or []
        ],
        color_price_per_sheet=payload.color_price_per_sheet,
        bw_price_per_sheet=payload.bw_price_per_sheet,
    )
    return ColorPlanResponse.model_validate(result)


@app.post(
    "/api/v1/manual-duplex",
    response_model=ManualDuplexResponse,
    tags=["imposition"],
    summary="每册独立的两轮手工双面打印清单",
)
async def manual_duplex(payload: ManualDuplexRequest) -> ManualDuplexResponse:
    result = build_manual_duplex(
        total_pages=payload.total_pages,
        max_sheets_per_booklet=payload.max_sheets_per_booklet,
        refill_reversed=payload.refill_reversed,
        flip_edge=payload.flip_edge,
    )
    return ManualDuplexResponse.model_validate(result)
