# 骑马钉小册子拼版 API（Saddle-Stitch Booklet Imposition API）

使用 Python + FastAPI 实现的骑马钉（对折装订）小册子拼版服务。输入**原稿总页数**和
**每册最多纸张数**，服务会：

1. 把连续原稿页按每册容量分册；
2. 将每册（实际只可能是最后一册）版面补齐到 **4 页的倍数**，补位页以空白（JSON `null`）表示；
3. 按纸张**从外到内**返回每张纸**正面（front）/反面（back）各自的左、右页码**，
   双面打印并对折后即可按原稿顺序翻阅。

也支持**按章节分册**（`POST /api/v1/chapter-impose`）：输入有序章节页数数组，
章节不可拆分、仅相邻章节可合册，服务在所有合法分册方案中依次最小化**总纸张数**与
**册数**，并以各册结束章节序号序列的字典序裁决并列。

还支持**装订前核对单册纸张**（`POST /api/v1/binding-check`）：提交某册当前从外到内
叠放的实际纸张，服务按非空页码集合识别每张纸，对缺纸、重复纸、跨册页或无法通过
左右互换 / 正反互换还原的版面返回 **HTTP 400**；核对通过时给出每张纸当前与应在
位置、所需互换操作，以及恢复外到内顺序的最少两两交换步骤。

另可生成**每册独立的两轮手工双面打印清单**（`POST /api/v1/manual-duplex`）：
没有自动双面打印功能时，每册先按外到内打印全部正面，再把首轮出纸堆回填进纸口
打印反面；支持指定**首轮出纸堆是否倒序回填**（倒序则第二轮反转纸张次序）以及
回填时**沿短边 / 长边翻转**（长边交换反面左右版面，短边保持不变）。

还可按彩印 / 黑白单价求**总费用最小的分册方案**（`POST /api/v1/color-plan`）：
输入总页数（1～200）、每册最多纸张数、可为空的彩色页闭区间列表与两种单价（分），
原稿连续有序分册、每册末尾补白到 4 的倍数；**纸上任一非空页属于彩色区间即整纸
按彩印计费**（交叠区间不重复计费），否则按黑白计费。服务在所有合法分册方案中
依次最小化**总费用 → 总纸张数 → 册数**，并以**各册末页页码序列的字典序**裁决
并列，返回总费用及各册页区间、费用、每张纸的印刷方式与正反面页码。

> 1 张纸正反两面共 4 个版面，因此骑马钉册的页数永远是 4 的倍数。

---

## 1. 单条命令启动（Docker Compose）

前置要求：已安装 Docker（含 Compose 插件）。

```bash
docker compose up -d --build
```

依赖安装已纳入**容器启动流程**：`entrypoint.sh` 会在每次容器启动时先执行
`pip install -r requirements.txt`，随后启动 uvicorn（无需在构建镜像时预装依赖）。

停止服务：

```bash
docker compose down
```

查看启动日志（首次启动会先联网安装依赖）：

```bash
docker compose logs -f
```

## 2. 必要配置

所有配置均有默认值，直接 `docker compose up -d --build` 即可运行：

| 配置项 | 位置 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `HOST_PORT` | Compose 环境变量（宿主机） | `8000` | 宿主机映射端口。端口被占用时可临时改端口：`HOST_PORT=9000 docker compose up -d --build` |
| `PORT` | `docker-compose.yml` 容器环境变量 | `8000` | 容器内 uvicorn 监听端口，一般无需修改 |

如需持久化自定义端口，可在项目根目录创建 `.env` 文件：

```env
HOST_PORT=9000
```

## 3. API 地址与端口

服务默认监听 **8000 端口**：

| 用途 | 地址 |
| --- | --- |
| 健康检查 | http://localhost:8000/health |
| Swagger 交互文档 | http://localhost:8000/docs |
| ReDoc 文档 | http://localhost:8000/redoc |
| 拼版接口（POST） | `POST http://localhost:8000/api/v1/impose` |
| 拼版接口（GET 便捷形式，可直接在浏览器打开） | `GET http://localhost:8000/api/v1/impose?total_pages=16&max_sheets_per_booklet=4` |
| 补印定位接口（POST） | `POST http://localhost:8000/api/v1/reprint` |
| 按章节分册拼版接口（POST） | `POST http://localhost:8000/api/v1/chapter-impose` |
| 装订核对接口（POST） | `POST http://localhost:8000/api/v1/binding-check` |
| 手工双面打印清单接口（POST） | `POST http://localhost:8000/api/v1/manual-duplex` |
| 彩印计费最优分册接口（POST） | `POST http://localhost:8000/api/v1/color-plan` |

## 4. 接口说明

### `POST /api/v1/impose`

**请求体（JSON）**

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `total_pages` | 整数 | 必填，正整数 `>= 1` | 原稿总页数 |
| `max_sheets_per_booklet` | 整数 | 必填，正整数 `>= 1` | 每册最多纸张数（每册最多 `4 ×` 该值 个版面） |

非正整数输入（`0`、负数、小数如 `16.5`、布尔值、数字字符串、缺失或多余字段）
一律返回 **HTTP 400** 及明确的中文错误说明。

请求示例：

```bash
curl -X POST http://localhost:8000/api/v1/impose \
  -H "Content-Type: application/json" \
  -d '{"total_pages": 16, "max_sheets_per_booklet": 4}'
```

响应示例（16 页、每册最多 4 张纸，共 1 册 4 张纸）：

```json
{
  "total_pages": 16,
  "max_sheets_per_booklet": 4,
  "booklet_count": 1,
  "total_sheets": 4,
  "booklets": [
    {
      "index": 0,
      "start_page": 1,
      "end_page": 16,
      "original_pages": 16,
      "padded_pages": 16,
      "blank_pages": 0,
      "sheet_count": 4,
      "sheets": [
        {
          "index": 0,
          "front": { "left": 16, "right": 1 },
          "back":  { "left": 2,  "right": 15 }
        },
        {
          "index": 1,
          "front": { "left": 14, "right": 3 },
          "back":  { "left": 4,  "right": 13 }
        },
        {
          "index": 2,
          "front": { "left": 12, "right": 5 },
          "back":  { "left": 6,  "right": 11 }
        },
        {
          "index": 3,
          "front": { "left": 10, "right": 7 },
          "back":  { "left": 8,  "right": 9 }
        }
      ]
    }
  ]
}
```

字段含义：

- `booklets[]`：分册结果，按装订顺序排列；`sheets[]` 为该册纸张，**`index: 0` 是最外侧一张**，依次向内。
- 每张纸给出 `front`（正面，朝外的一面）与 `back`（反面），各自包含 `left` / `right` 页码。
- 页码为**原稿 1-based 页码**；补位空白页用 **`null`** 表示。
- `start_page` / `end_page`：本册覆盖的原稿页区间；`original_pages` 原稿页数；
  `padded_pages` 补齐后页数（4 的倍数）；`blank_pages` 空白页数；`sheet_count` 用纸数。

### 空白页示例（5 页原稿）

5 页会补齐到 8 页（2 张纸），3 个空白页位于外封与内帖尾部：

```bash
curl -X POST http://localhost:8000/api/v1/impose \
  -H "Content-Type: application/json" \
  -d '{"total_pages": 5, "max_sheets_per_booklet": 10}'
```

```json
{
  "sheets": [
    { "index": 0, "front": { "left": null, "right": 1 }, "back": { "left": 2, "right": null } },
    { "index": 1, "front": { "left": null, "right": 3 }, "back": { "left": 4, "right": 5 } }
  ]
}
```

### 错误响应示例

```bash
curl -i -X POST http://localhost:8000/api/v1/impose \
  -H "Content-Type: application/json" \
  -d '{"total_pages": -4, "max_sheets_per_booklet": 2}'
```

```json
{
  "error": "invalid_input",
  "message": "输入参数校验失败：total_pages 与 max_sheets_per_booklet 必须是正整数。",
  "details": [
    { "field": "total_pages", "message": "必须为大于等于 1 的正整数。" }
  ]
}
```

### `POST /api/v1/reprint`（受损页补印定位）

原稿部分页受损时，只需重印承载这些页的**整张纸**。输入与拼版接口相同的分册参数，
再加一个**非空的受损原稿页区间列表**（允许交叠），服务即按现有分册及拼版规则：

- 定位承载受损页的整张纸，**同一张纸只返回一次**（交叠区间自动去重）；
- 结果按**分册序号升序**、同册**纸张从外到内**排序；
- 每项给出分册 / 纸张序号、正反面左右原稿页码（补白为 `null`），
  以及落在该纸上的受损页码（去重并升序）。

**请求体（JSON）**

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `total_pages` | 整数 | 必填，正整数 `>= 1` | 原稿总页数 |
| `max_sheets_per_booklet` | 整数 | 必填，正整数 `>= 1` | 每册最多纸张数（与拼版接口口径一致） |
| `damaged_ranges` | 区间数组 | 必填，**至少一项** | 受损原稿页闭区间列表，端点均包含，允许交叠 |
| `damaged_ranges[].start_page` | 整数 | 必填，正整数 | 区间起始页（含） |
| `damaged_ranges[].end_page` | 整数 | 必填，正整数 | 区间结束页（含），须 `>= start_page` 且 `<= total_pages` |

以下情况一律返回 **HTTP 400**：区间倒置（`start_page > end_page`）、越界
（`end_page > total_pages` 或 `< 1`）、区间列表为空 / 缺失、端点不是正整数。

请求示例（17 页、每册 2 张纸 → 共 3 册；受损 7–9 页与 16–17 页，区间跨册且与其他区间规则一致）：

```bash
curl -X POST http://localhost:8000/api/v1/reprint \
  -H "Content-Type: application/json" \
  -d '{
        "total_pages": 17,
        "max_sheets_per_booklet": 2,
        "damaged_ranges": [
          {"start_page": 7, "end_page": 9},
          {"start_page": 16, "end_page": 17}
        ]
      }'
```

响应示例（返回 3 张需补印的纸：册 0 最外纸、册 1 最外纸、册 2 最外纸；末册补白为 `null`）：

```json
{
  "total_pages": 17,
  "max_sheets_per_booklet": 2,
  "booklet_count": 3,
  "reprint_sheet_count": 3,
  "sheets": [
    {
      "booklet_index": 0,
      "sheet_index": 0,
      "front": { "left": 8, "right": 1 },
      "back":  { "left": 2, "right": 7 },
      "damaged_pages": [7, 8]
    },
    {
      "booklet_index": 1,
      "sheet_index": 0,
      "front": { "left": 16, "right": 9 },
      "back":  { "left": 10, "right": 15 },
      "damaged_pages": [9, 16]
    },
    {
      "booklet_index": 2,
      "sheet_index": 0,
      "front": { "left": null, "right": 17 },
      "back":  { "left": null, "right": null },
      "damaged_pages": [17]
    }
  ]
}
```

补印错误响应示例（区间倒置）：

```bash
curl -i -X POST http://localhost:8000/api/v1/reprint \
  -H "Content-Type: application/json" \
  -d '{
        "total_pages": 16,
        "max_sheets_per_booklet": 4,
        "damaged_ranges": [{"start_page": 9, "end_page": 7}]
      }'
```

```json
{
  "error": "invalid_input",
  "message": "输入参数校验失败：total_pages 与 max_sheets_per_booklet 必须是正整数，damaged_ranges 必须为非空的有效受损页区间列表。",
  "details": [
    { "field": "damaged_ranges", "message": "受损页区间不能倒置：start_page 必须小于等于 end_page。" }
  ]
}
```

### `POST /api/v1/chapter-impose`（按章节分册拼版）

输入**非空的有序章节页数数组**与每册最多纸张数。规则：

- **章节不可拆分**，只有**相邻章节**可以合入同一册；
- 每册页数不得超过 `4 × max_sheets_per_booklet`；
- 在所有合法分册方案中，依次最小化 **① 总纸张数 → ② 册数**，
  并以 **③ 各册结束章节序号序列的字典序**（小者优先）裁决并列；
- 每册给出章节序号范围（`start_chapter` / `end_chapter`，1-based）、
  **全局原稿页码范围**（`start_page` / `end_page`，跨册连续编号）、
  纸张数与补白数，拼版结构与 `/api/v1/impose` 完全一致（补白为 `null`）。

**请求体（JSON）**

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `chapter_pages` | 整数数组 | 必填，**非空**，每项为正整数 `>= 1` | 有序章节页数（第 1 章、第 2 章……） |
| `max_sheets_per_booklet` | 整数 | 必填，正整数 `>= 1` | 每册最多纸张数（每册最多 `4 ×` 该值 个版面） |

以下情况一律返回 **HTTP 400**：数组为空 / 缺失、页数不是正整数、
`max_sheets_per_booklet` 非法、存在多余字段，以及**任一单章页数超过
`4 × max_sheets_per_booklet`**（章节不可拆分，无法分册）。

请求示例（3 章共 14 页、每册最多 2 张纸即 8 页）：

```bash
curl -X POST http://localhost:8000/api/v1/chapter-impose \
  -H "Content-Type: application/json" \
  -d '{"chapter_pages": [6, 5, 3], "max_sheets_per_booklet": 2}'
```

响应示例（第 1 章单独成册，第 2、3 章合册；共 2 册 4 张纸、2 个补白页）：

```json
{
  "chapter_pages": [6, 5, 3],
  "max_sheets_per_booklet": 2,
  "chapter_count": 3,
  "total_pages": 14,
  "booklet_count": 2,
  "total_sheets": 4,
  "total_blank_pages": 2,
  "booklets": [
    {
      "index": 0,
      "start_chapter": 1,
      "end_chapter": 1,
      "start_page": 1,
      "end_page": 6,
      "original_pages": 6,
      "padded_pages": 8,
      "blank_pages": 2,
      "sheet_count": 2,
      "sheets": [
        { "index": 0, "front": { "left": null, "right": 1 }, "back": { "left": 2, "right": null } },
        { "index": 1, "front": { "left": 6, "right": 3 }, "back": { "left": 4, "right": 5 } }
      ]
    },
    {
      "index": 1,
      "start_chapter": 2,
      "end_chapter": 3,
      "start_page": 7,
      "end_page": 14,
      "original_pages": 8,
      "padded_pages": 8,
      "blank_pages": 0,
      "sheet_count": 2,
      "sheets": [
        { "index": 0, "front": { "left": 14, "right": 7 }, "back": { "left": 8, "right": 13 } },
        { "index": 1, "front": { "left": 12, "right": 9 }, "back": { "left": 10, "right": 11 } }
      ]
    }
  ]
}
```

单章超容量错误响应示例（容量 8 页，第 2 章 9 页）：

```bash
curl -i -X POST http://localhost:8000/api/v1/chapter-impose \
  -H "Content-Type: application/json" \
  -d '{"chapter_pages": [4, 9, 4], "max_sheets_per_booklet": 2}'
```

```json
{
  "error": "invalid_input",
  "message": "输入参数校验失败：chapter_pages 必须为非空的正整数页数数组，max_sheets_per_booklet 必须是正整数，且任一章节页数不能超过 4 × max_sheets_per_booklet。",
  "details": [
    { "field": "chapter_pages", "message": "第 2 章共 9 页，超过单册容量上限 8 页（4 × max_sheets_per_booklet），章节不可拆分。" }
  ]
}
```

### `POST /api/v1/binding-check`（单册装订核对）

实际装订前，把**某一册当前从外到内叠放的一叠纸**提交给服务核对。服务以
`/api/v1/impose` 的现有拼版结果为基准，按每张纸的**非空页码集合**识别纸张，
判定每张纸：

- **当前位置**（`current_index`，0 为最外侧）与**应在位置**（`expected_index`）；
- 还原正确版面所需的互换操作：
  - `swap_left_right`：左右互换（同一面左右两版对调，即纸张横向调头）；
  - `swap_front_back`：正反互换（沿短边翻到另一面，左右位置不变，与双面打印
    的翻页方式一致）；两者可单独或组合出现，无需调整时为空列表；
- 整叠纸恢复为基准**外到内顺序**的**最少两两交换步骤** `swap_steps`
  （交换两个叠放位置，`position_a < position_b`，0 为最外侧）；
  存在多种等长方案时，按**步骤序列字典序**选择（先比第一步，再依次向后）。

**请求体（JSON）**

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `total_pages` | 整数 | 必填，正整数 `>= 1` | 原稿总页数（与拼版接口口径一致） |
| `max_sheets_per_booklet` | 整数 | 必填，正整数 `>= 1` | 每册最多纸张数（与拼版接口口径一致） |
| `booklet_index` | 整数 | 必填，非负整数 | 待核对分册序号，从 0 开始 |
| `sheets` | 纸张数组 | 必填，**至少一张** | 该册纸张，按**当前从外到内**顺序记录 |
| `sheets[].front.left` / `front.right` | 整数或 `null` | 非空时为正整数 | 当前正面左 / 右原稿页码，补白为 `null` |
| `sheets[].back.left` / `back.right` | 整数或 `null` | 非空时为正整数 | 当前反面左 / 右原稿页码，补白为 `null` |

以下情况一律返回 **HTTP 400**（沿用统一错误格式，`details` 可含多条）：

- **缺纸**：基准纸张在提交列表中缺失（或提交了基准中不存在的纸面 / 全白纸）；
- **重复纸**：两张提交记录的非空页码集合相同；
- **跨册页**：出现不属于该册原稿页区间的页码（含超出 `total_pages` 的越界页）；
- **无法还原的版面**：纸张页码集合正确，但版面不能通过左右互换、正反互换
  （及其组合）还原成基准版面。

请求示例（16 页 1 册 4 张纸；当前顺序为第 2、3、1、4 张，即位置
0→应在 1、位置 1→应在 2、位置 2→应在 0、位置 3 已就位）：

```bash
curl -X POST http://localhost:8000/api/v1/binding-check \
  -H "Content-Type: application/json" \
  -d '{
        "total_pages": 16,
        "max_sheets_per_booklet": 4,
        "booklet_index": 0,
        "sheets": [
          { "front": { "left": 14, "right": 3 }, "back": { "left": 4,  "right": 13 } },
          { "front": { "left": 12, "right": 5 }, "back": { "left": 6,  "right": 11 } },
          { "front": { "left": 16, "right": 1 }, "back": { "left": 2,  "right": 15 } },
          { "front": { "left": 10, "right": 7 }, "back": { "left": 8,  "right": 9 } }
        ]
      }'
```

响应示例（3 张错位纸构成一个循环，最少 2 次交换；字典序最小步骤为先交换
位置 0、1，再交换位置 0、2）：

```json
{
  "total_pages": 16,
  "max_sheets_per_booklet": 4,
  "booklet_index": 0,
  "start_page": 1,
  "end_page": 16,
  "sheet_count": 4,
  "sheets": [
    {
      "current_index": 0,
      "expected_index": 1,
      "required_operations": [],
      "front": { "left": 14, "right": 3 },
      "back":  { "left": 4,  "right": 13 }
    },
    {
      "current_index": 1,
      "expected_index": 2,
      "required_operations": [],
      "front": { "left": 12, "right": 5 },
      "back":  { "left": 6,  "right": 11 }
    },
    {
      "current_index": 2,
      "expected_index": 0,
      "required_operations": [],
      "front": { "left": 16, "right": 1 },
      "back":  { "left": 2,  "right": 15 }
    },
    {
      "current_index": 3,
      "expected_index": 3,
      "required_operations": [],
      "front": { "left": 10, "right": 7 },
      "back":  { "left": 8,  "right": 9 }
    }
  ],
  "swap_step_count": 2,
  "swap_steps": [
    { "position_a": 0, "position_b": 1 },
    { "position_a": 0, "position_b": 2 }
  ]
}
```

含互换操作的例子：某张纸被**左右调头**提交为
`front: 1 | 16, back: 15 | 2` 时，`required_operations` 为
`["swap_left_right"]`；被**翻到反面**提交为
`front: 2 | 15, back: 16 | 1` 时为 `["swap_front_back"]`；两者同时发生时为
`["swap_front_back", "swap_left_right"]`。

跨册页错误响应示例（17 页、每册 2 张纸，共 3 册；向第 0 册提交了属于第 1 册的
第 9 页）：

```json
{
  "error": "invalid_input",
  "message": "装订核对失败：存在缺纸、重复纸、跨册页，或无法通过左右互换、正反互换还原的版面。",
  "details": [
    { "field": "sheets[1]", "message": "sheets[1] 含跨册（或越界）页码 [9]：第 0 册的原稿页区间为 1–8。" },
    { "field": "sheets", "message": "缺少基准纸张：应在位置 1（从外到内，0 为最外侧）。" }
  ]
}
```

### `POST /api/v1/manual-duplex`（每册独立的两轮手工双面打印清单）

打印机不支持自动双面时，按本清单**每册分两轮**手工送纸：

- **第一轮**：纸张按**外到内**顺序打印各自**正面**（与 `/api/v1/impose` 的
  正面版面一致）；
- **第二轮**：把首轮出纸堆回填进纸口，按**回填后的实际入纸顺序**打印对应**反面**：
  - `refill_reversed: true`（倒序回填）时，第二轮纸张次序**反转**
    （从最内侧纸印到最外侧纸）；`false` 时与首轮同序；
  - `flip_edge: "long"`（沿长边翻转回填）时，**交换**反面的左右版面；
    `flip_edge: "short"`（沿短边翻转回填）时左右**保持不变**。

每册独立成组（`booklets[].rounds` 恒为两轮），每条记录给出**轮次内序号**
（`sequence`，从 0 开始）、**册号**（`booklet_index`）、**纸张序号**
（`sheet_index`，0 为最外侧纸）与本次打印的**左右页码**；补白沿用 `null`。

**请求体（JSON）**

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `total_pages` | 整数 | 必填，正整数 `>= 1` | 原稿总页数（与拼版接口口径一致） |
| `max_sheets_per_booklet` | 整数 | 必填，正整数 `>= 1` | 每册最多纸张数（与拼版接口口径一致） |
| `refill_reversed` | 布尔 | 必填，`true` / `false` | 首轮出纸堆是否倒序回填（倒序则第二轮反转纸张次序） |
| `flip_edge` | 字符串 | 必填，`"short"` 或 `"long"` | 回填时沿短边（左右不变）或长边（交换左右）翻转 |

非法选项一律返回 **HTTP 400**：`flip_edge` 不是 `"short"` / `"long"`、
`refill_reversed` 不是布尔值（`1`、`"true"` 等均不接受）、分册参数非正整数、
缺失或多余字段。

请求示例（8 页、每册最多 4 张纸 → 1 册 2 张纸；倒序回填 + 沿长边翻转）：

```bash
curl -X POST http://localhost:8000/api/v1/manual-duplex \
  -H "Content-Type: application/json" \
  -d '{
        "total_pages": 8,
        "max_sheets_per_booklet": 4,
        "refill_reversed": true,
        "flip_edge": "long"
      }'
```

响应示例（第二轮次序反转为内→外，且反面左右版面互换）：

```json
{
  "total_pages": 8,
  "max_sheets_per_booklet": 4,
  "refill_reversed": true,
  "flip_edge": "long",
  "booklet_count": 1,
  "total_sheets": 2,
  "booklets": [
    {
      "index": 0,
      "sheet_count": 2,
      "rounds": [
        {
          "round": 1,
          "side": "front",
          "entries": [
            { "sequence": 0, "booklet_index": 0, "sheet_index": 0, "left": 8, "right": 1 },
            { "sequence": 1, "booklet_index": 0, "sheet_index": 1, "left": 6, "right": 3 }
          ]
        },
        {
          "round": 2,
          "side": "back",
          "entries": [
            { "sequence": 0, "booklet_index": 0, "sheet_index": 1, "left": 5, "right": 4 },
            { "sequence": 1, "booklet_index": 0, "sheet_index": 0, "left": 7, "right": 2 }
          ]
        }
      ]
    }
  ]
}
```

非法选项错误响应示例（`flip_edge` 取值不合法）：

```bash
curl -i -X POST http://localhost:8000/api/v1/manual-duplex \
  -H "Content-Type: application/json" \
  -d '{"total_pages": 8, "max_sheets_per_booklet": 4, "refill_reversed": false, "flip_edge": "middle"}'
```

```json
{
  "error": "invalid_input",
  "message": "输入参数校验失败：total_pages 与 max_sheets_per_booklet 必须是正整数，refill_reversed 必须是布尔值，flip_edge 必须是 \"short\" 或 \"long\"。",
  "details": [
    { "field": "flip_edge", "message": "必须为 \"short\"（短边翻转）或 \"long\"（长边翻转）。" }
  ]
}
```

### `POST /api/v1/color-plan`（彩印 / 黑白混合计费的最优分册）

按彩印、黑白两种单价（分）求**总费用最小**的分册方案。规则：

- 原稿**连续有序分册**：每册至少一页、至多 `4 × max_sheets_per_booklet` 页，
  每册末尾补白到 4 的倍数，拼版方式与 `/api/v1/impose` 完全一致；
- **计费**：一张纸上任一**非空页**（补白页除外）属于彩色页区间并集，整纸按
  彩印单价计费，否则按黑白单价计费；**交叠区间取并集，不重复计费**；
- **择优**：在所有合法分册方案中依次最小化 **① 总费用 → ② 总纸张数 → ③ 册数**，
  并以 **④ 各册末页页码序列的字典序**（小者优先）裁决并列。

**请求体（JSON）**

| 字段 | 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `total_pages` | 整数 | 必填，`1 ~ 200` | 原稿总页数 |
| `max_sheets_per_booklet` | 整数 | 必填，正整数 `>= 1` | 每册最多纸张数（与其他接口口径一致） |
| `color_ranges` | 区间数组或 `null` | 可缺省、为 `null` 或空列表 | 彩色原稿页闭区间列表，端点均包含，允许交叠；为空表示没有彩色页 |
| `color_ranges[].start_page` | 整数 | 必填，正整数 | 区间起始页（含） |
| `color_ranges[].end_page` | 整数 | 必填，正整数 | 区间结束页（含），须 `>= start_page` 且 `<= total_pages` |
| `color_price_per_sheet` | 整数 | 必填，正整数 `>= 1` | 彩印每张纸单价（分） |
| `bw_price_per_sheet` | 整数 | 必填，正整数 `>= 1` | 黑白每张纸单价（分） |

以下情况一律返回 **HTTP 400**：`total_pages` 不在 1～200、单价非正整数、
区间倒置（`start_page > end_page`）、区间越界（`end_page > total_pages` 或 `< 1`）、
区间项不是对象、缺失或多余字段。

请求示例（16 页、每册最多 4 张纸；第 3～6 页彩色；彩印 50 分 / 黑白 10 分）：

```bash
curl -X POST http://localhost:8000/api/v1/color-plan \
  -H "Content-Type: application/json" \
  -d '{
        "total_pages": 16,
        "max_sheets_per_booklet": 4,
        "color_ranges": [{"start_page": 3, "end_page": 6}],
        "color_price_per_sheet": 50,
        "bw_price_per_sheet": 10
      }'
```

响应示例（拆成 [1–8]、[9–16] 两册，把彩色页集中到 1 张纸：1 彩 3 黑白共 80 分；
若合成一册则需 2 张彩印纸共 120 分）：

```json
{
  "total_pages": 16,
  "max_sheets_per_booklet": 4,
  "color_ranges": [{ "start_page": 3, "end_page": 6 }],
  "color_price_per_sheet": 50,
  "bw_price_per_sheet": 10,
  "booklet_count": 2,
  "total_sheets": 4,
  "color_sheet_count": 1,
  "bw_sheet_count": 3,
  "total_cost": 80,
  "booklets": [
    {
      "index": 0,
      "start_page": 1,
      "end_page": 8,
      "original_pages": 8,
      "padded_pages": 8,
      "blank_pages": 0,
      "sheet_count": 2,
      "color_sheet_count": 1,
      "cost": 60,
      "sheets": [
        { "index": 0, "print_mode": "bw",    "price": 10, "front": { "left": 8, "right": 1 }, "back": { "left": 2, "right": 7 } },
        { "index": 1, "print_mode": "color", "price": 50, "front": { "left": 6, "right": 3 }, "back": { "left": 4, "right": 5 } }
      ]
    },
    {
      "index": 1,
      "start_page": 9,
      "end_page": 16,
      "original_pages": 8,
      "padded_pages": 8,
      "blank_pages": 0,
      "sheet_count": 2,
      "color_sheet_count": 0,
      "cost": 20,
      "sheets": [
        { "index": 0, "print_mode": "bw", "price": 10, "front": { "left": 16, "right": 9 }, "back": { "left": 10, "right": 15 } },
        { "index": 1, "print_mode": "bw", "price": 10, "front": { "left": 14, "right": 11 }, "back": { "left": 12, "right": 13 } }
      ]
    }
  ]
}
```

字段含义：

- `total_cost`：全部册合计费用（分），恒等于各册 `cost` 之和；
- `booklets[].start_page` / `end_page`：本册原稿页区间（`end_page` 即本册末页）；
- `booklets[].cost`：本册费用（分）；`color_sheet_count` 本册彩印纸张数；
- `sheets[].print_mode`：每张纸的印刷方式，`color` 彩印 / `bw` 黑白；
  `price` 为该张纸费用（分）；`front` / `back` 为正反面左右页码，补白为 `null`；
- `color_sheet_count` / `bw_sheet_count`：全部册合计彩印、黑白纸张数。

错误响应示例（区间倒置）：

```bash
curl -i -X POST http://localhost:8000/api/v1/color-plan \
  -H "Content-Type: application/json" \
  -d '{
        "total_pages": 16,
        "max_sheets_per_booklet": 4,
        "color_ranges": [{"start_page": 9, "end_page": 7}],
        "color_price_per_sheet": 50,
        "bw_price_per_sheet": 10
      }'
```

```json
{
  "error": "invalid_input",
  "message": "输入参数校验失败：total_pages 必须是 1 到 200 之间的整数，max_sheets_per_booklet、彩印与黑白单价必须是正整数，color_ranges 必须为 null 或有效的彩色页区间列表（区间不得倒置或越界）。",
  "details": [
    { "field": "color_ranges", "message": "彩色页区间不能倒置：start_page 必须小于等于 end_page。" }
  ]
}
```

## 5. 拼版规则（打印与装订说明）

对一册补齐到 `n` 页（`n` 为 4 的倍数）的小册子，从外到内第 `i` 张纸
（`i = 0, 1, …, n/4-1`）的版面为：

- 正面 front：左 `n − 2i`，右 `1 + 2i`
- 反面 back：左 `2 + 2i`，右 `n − 1 − 2i`

不变量：每张纸任一面左右页码之和恒为 `n + 1`；偶数页在左、奇数页在右。

打印时选择**双面打印、沿短边翻转（短边装订/flip on short edge）**，
全部印好后把整叠纸**横向对折（书脊在折缝处骑马钉装订）**，翻开顺序即为
1 → 2 → 3 → … 的原稿顺序。

**分册规则**：除最后一册外，每册都装满 `4 × max_sheets_per_booklet` 个版面；
剩余原稿页归入最后一册，并仅对该册补空白页到 4 的倍数。

**按章节分册规则**（`/api/v1/chapter-impose`）：章节不可拆分、仅相邻章节可合册，
每册页数不超过 `4 × max_sheets_per_booklet`；在所有合法方案中依次最小化
总纸张数、册数，并以各册结束章节序号序列的字典序裁决并列。每册独立补齐到
4 的倍数（可能每册都有补白页），页码为跨册连续的全局原稿页码。

**手工双面打印**（`/api/v1/manual-duplex`）：无自动双面功能时，每册分两轮送纸——
首轮按外到内打印正面；随后把出纸堆回填进纸口打印反面。倒序回填时第二轮纸张
次序反转；沿长边翻转回填时交换反面左右版面，沿短边翻转时左右不变。

**彩印计费最优分册**（`/api/v1/color-plan`）：每册至少一页、不超容量，每册末尾
补白到 4 的倍数；纸上任一非空页属于彩色页区间并集即整纸按彩印计费（交叠区间
不重复计费），否则按黑白计费。在所有合法分册方案中依次最小化总费用、总纸张数、
册数，并以各册末页页码序列的字典序裁决并列。

## 6. 本地开发（不使用 Docker）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

运行测试：

```bash
# 核心算法测试仅依赖标准库，无需安装任何第三方包
python -m unittest tests.test_imposition -v

# 完整测试（含 API 层），需要先安装 requirements-dev.txt
python -m unittest discover -s tests -v
```

## 7. 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── imposition.py      # 分册 / 补齐 / 拼版核心算法 + 补印定位 + 按章节最优分册 + 装订核对 + 手工双面清单 + 彩印计费最优分册（纯标准库）
│   ├── schemas.py         # Pydantic 请求与响应模型（正整数 / 受损区间 / 章节容量 / 装订核对 / 手工双面选项 / 彩色区间与单价校验）
│   └── main.py            # FastAPI 路由（拼版、补印、按章节拼版、装订核对、手工双面清单、彩印计费分册）与 400 错误处理
├── tests/
│   ├── test_imposition.py # 核心算法单元测试
│   └── test_api.py        # API 与非法输入测试
├── entrypoint.sh          # 容器入口：启动时安装依赖并启动服务
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── requirements-dev.txt
└── README.md
```
