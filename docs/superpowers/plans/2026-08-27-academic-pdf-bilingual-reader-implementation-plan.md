<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 英文文献翻译阅读 Skill 实现计划

- 日期：2026-08-27
- Skill 名称：`academic-pdf-en-zh-reader`
- 状态：本地候选实现与端到端验收已完成；公开发布仍阻断
- 设计依据：[`../specs/2026-08-27-academic-pdf-bilingual-reader-skill-design.md`](../specs/2026-08-27-academic-pdf-bilingual-reader-skill-design.md)
- 实施原则：风险门槛优先、测试先行、最小实现、逐项验收、失败关闭
- 2026-09-09 政策修订：经用户授权按个人项目精简报告要求；一个真实可用的私密入口可兼顾行为准则与安全问题，取消三个不同地址及独立备用联系人的强制要求。公开维护身份可采用本人确认的 GitHub 公开名；身份、渠道与素材事实不得填造，未知项继续阻断发布。安全、隐私、依赖、版权及 LPAC 真实生产验证不变。

## 1. 目标与本阶段边界

2026-09-10 责任声明修订：落实“原末页无安全空间时追加声明末页并提醒”的新要求。
追加页单独标识，不改变源论文页、译文顺序和注释预算；声明缺失或验证失败时
不得交付 PDF，成功交付后才发出固定用户提示。同步页数、来源绑定、渲染清单、
QA 和回归测试，不以跳过安全检查换取发布。

本计划把已经定稿的产品设计拆成可以逐项执行和验收的工程任务。最终目标是交付一个可开源的 Codex Skill：读取符合输入边界的英文数字版学术 PDF，将每页确定性归一化为 A4，再生成左侧原文、右侧完整中文译文的 A3 横向 PDF，并满足固定字号、镜像栏结构、教学标注、确定性、安全和开源合规要求。

本文件只制定实施步骤，不执行以下操作：

- 不安装依赖或下载字体。
- 不创建 Skill 程序文件。
- 不处理用户论文。
- 不提交、推送、发布或部署任何内容。

## 2. 总体实现策略

采用一条严格的风险优先路径：

```text
仓库与依赖准入
→ 门槛 0：字体测量—绘制闭环
→ 合成测试材料
→ 门槛 1：左页矢量合成
→ 门槛 2：不可信 PDF 安全 Worker
→ 数据契约与作业状态
→ PDF 预检、A4 页面归一化、提取、拓扑和语义单元
→ 翻译与独立复核契约
→ 字体排版、FrameGraph、局部求解和续页
→ 三类标注与纠错记忆
→ PDF 合成、机械 QA 和交付
→ Skill 编排、前向测试和开源发布门禁
```

关键分工保持不变：

- Agent 只做语义任务：结构复核、翻译、独立审校、重点/教学内容选择和有证据的图表解读。
- Python 程序只做确定性任务：文件安全、PDF 解析、结构模型、文本测量、换行、坐标求解、绘制、合成、QA、状态与清理。
- Agent 不直接自由绘制 PDF；程序不自行生成译文。
- v1 不调用额外第三方翻译 API，不加入 OCR，不支持扫描件。
- v1 首先完成 Windows 安全 Worker；其他平台在没有等效适配器时明确失败，不以普通子进程静默降级。

## 3. 执行纪律

除纯文档任务外，每个任务使用同一最小闭环：

1. 先编写能够观察目标行为的失败测试。
2. 运行最小测试集合，确认失败原因正是缺少当前能力。
3. 只实现使该组测试通过的最小代码。
4. 重跑当前测试，再运行已有完整测试。
5. 运行格式、类型、许可证或确定性检查中与本任务相关的部分。
6. 在用户授权实施后，形成一个本地原子提交；推送、Release 和公开发布必须另行确认。

默认命令约定：

```powershell
uv sync --all-groups --locked
uv run pytest <测试路径> -q
uv run ruff check .
reuse lint
```

若命令、版本或许可证审查结果与计划假设不一致，先更新计划或依赖记录，不通过临时安装未锁定包绕过。

## 4. 原型硬门槛

以下六项必须依次通过，任何一项失败都停止后续核心功能实现：

| 门槛 | 必须证明 | 禁止的降级 |
|---|---|---|
| G0 字体 | 指定 Noto 字体可按 SC face 注册、测量、嵌入和稳定复现 | 系统字体、未验证子字体序号、缺字继续输出 |
| G1 矢量合成 | 显示方向 CropBox 先按逐轴规则归一化为 A4：可放入则保持 1:1 并居中补白，任一轴超出则仅做最小统一缩小；归一化页可 1:1 放入 A3 左半页并保留矢量内容 | 左页截图、整页栅格化、放大、裁切、拉伸或非等比缩放 |
| G2 安全 Worker | 不可信 PDF 在受限进程、受控目录和资源上限内处理 | 无隔离运行、自动提高限制、任意路径回传 |
| G3 混合拓扑 | 单栏、双栏、三栏和首页混合 band 能稳定识别并镜像 | 低置信度时静默改为单栏 |
| G4 局部求解 | 固定字号下按最小范围重排，零重叠且顺序不变 | 缩字、删译、乱序、任意全页重排 |
| G5 续页 | 整栏无解后才生成合法续页，内容不重不漏 | 提前续页、在英文断点机械切中文段落 |

## 5. 分阶段任务

### 任务 1：建立开源与依赖准入基线

**创建文件**

- `pyproject.toml`
- `.python-version`
- `uv.lock`
- `.gitignore`
- `.gitattributes`
- `LICENSE`
- `LICENSES/Apache-2.0.txt`
- `LICENSES/CC-BY-SA-4.0.txt`
- `REUSE.toml`
- `NOTICE`
- `THIRD_PARTY_NOTICES.md`
- `UPSTREAMS.md`
- `TEST_DATA_ATTRIBUTION.md`
- `PRIVACY.md`
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `CITATION.cff`
- `compliance/dependencies.json`
- `scripts/check_dependency_policy.py`
- `tests/compliance/test_dependency_policy.py`

**先写失败测试**

- 拒绝许可证为 `Unknown`、`NOASSERTION`、NC、ND 或默认阻断清单中的依赖。
- 要求每项直接、传递、二进制、字体和运行时下载依赖记录名称、版本、来源、许可证、SHA-256 或待取证状态。
- 要求原创代码/文档、合成 fixture 和 Contributor Covenant 分别使用正确的 SPDX 许可证。
- 验证基本社区健康文件存在；开发态允许明确的 `PUBLIC_RELEASE_BLOCKED` 标记，但发布态发现真实公开维护身份或可同时接收行为准则与安全问题的私密入口未配置时必须失败；不再检查独立备用联系人或三个不同地址。

**运行并确认失败**

```powershell
uv run pytest tests/compliance/test_dependency_policy.py -q
reuse lint
```

**最小实现**

- 先对拟采用的 `pypdf`、`pdfplumber`、`pdfminer.six`、`reportlab`、`fonttools`、`pypdfium2`、`Pillow`、`jsonschema` 及开发工具做版本和许可证取证，再写入锁文件。
- 依赖台账使用 JSON 而非 YAML，使准入检查只依赖 Python 标准库，不为读取合规元数据再引入一个 YAML 解析依赖。
- `pyproject.toml` 固定 Python `>=3.12,<3.13`；`.python-version` 固定实际验证过的补丁版本。
- `uv.lock` 只在全部直接和传递依赖通过准入后生成。
- 项目原创代码与文档使用 Apache-2.0；Contributor Covenant 3.0 文件保留 CC-BY-SA-4.0。CC0-1.0 与 OFL-1.1 的许可证文本分别等到任务 3 的合成 fixture 和任务 2 的字体实际进入仓库时再加入，避免 REUSE 出现未使用许可证。
- 真实公开维护身份和私密报告入口必须在公开前由维护者确认；本人确认的 GitHub 公开名可作为维护身份，一个确实支持两类报告的入口即可。渠道地址或链接公开，报告内容私密；没有独立处理人时应说明独立复核和申诉的限制，不承诺不存在的受理能力。未知值继续使用机器可识别的 `PUBLIC_RELEASE_BLOCKED` 状态管理，不填造姓名、邮箱或验证记录；开发测试可继续，但发布测试必须失败。

**完成标准**

- 依赖检查与 `reuse lint` 通过，开发态合规检查确认公开发布仍被正确阻断。
- 锁文件中没有未备案或默认阻断许可证。
- 尚未创建任何应用包代码。

**建议本地提交**：`chore: establish compliance and dependency baseline`

### 任务 2：通过字体门槛 G0

**创建文件**

- `assets/fonts/NotoSerifSC-Regular.ttf`
- `assets/fonts/NotoSerifSC-SemiBold.ttf`
- `assets/fonts/NotoSansSymbols2-Regular.ttf`
- `assets/font-manifest.json`
- `scripts/bootstrap_fonts.py`
- `scripts/probe_reportlab_fonts.py`
- `tests/fonts/test_font_manifest.py`
- `tests/fonts/test_reportlab_embedding.py`
- `tests/fonts/test_font_coverage.py`

**先写失败测试**

- manifest 缺少官方 URL/tag 或 commit、archive 成员路径、face/PostScript name、SHA-256、OFL-1.1 或嵌入结果时失败。
- 任一字体哈希、name table 的 SC face 或字符覆盖与 manifest 不一致时失败。
- 使用中文、拉丁字母、希腊字母、常用数学符号、上下标和统计符号生成探针 PDF；字体未嵌入、出现 tofu 或宽度测量与实际绘制不一致时失败。
- 确认测试不读取系统字体目录。

**运行并确认失败**

```powershell
uv run pytest tests/fonts -q
```

**最小实现**

- 先对设计指定的 Noto CJK 静态 OTC 做真实 ReportLab 门禁；已证实 SC face 正确但 CFF 轮廓不受支持，不进入运行时。
- 从同一官方 `Serif2.003` 的区域 TrueType 变量源，以固定 fontTools、轴值和无时间戳重算的方式生成静态 400/600 实例；固定源与输出哈希并如实标记修改。
- 由 fontTools 读取 name table、轮廓格式、字符覆盖和字重；不假设 TTC 索引，不读取系统字体。
- 用 ReportLab 实际注册、测量、绘制、重新打开并检查嵌入字体。
- 把经过验证的最终事实写入 `font-manifest.json`，不填写推测值。

**完成标准**

- 三组字体测试全部通过。
- 生成的探针 PDF 可搜索、可复制，字体已嵌入且无系统 fallback。
- 原 TTC 路线已按门禁停止；替代的同家族、同许可证、官方来源 TrueType 静态实例已经重新固定哈希并通过 ReportLab 门禁。

**建议本地提交**：`test: prove deterministic CJK font embedding`

### 任务 3：建立包骨架和 CC0 合成 fixture 生成器

**创建文件**

- `src/academic_pdf_en_zh_reader/__init__.py`
- `src/academic_pdf_en_zh_reader/constants.py`
- `scripts/generate_synthetic_fixtures.py`
- `tests/fixtures-synthetic/specs/single-column.json`
- `tests/fixtures-synthetic/specs/first-page-mixed.json`
- `tests/fixtures-synthetic/specs/two-column.json`
- `tests/fixtures-synthetic/specs/three-column.json`
- `tests/fixtures-synthetic/specs/cross-column-paragraph.json`
- `tests/fixtures-synthetic/specs/cross-page-paragraph.json`
- `tests/fixtures-synthetic/specs/figures-and-tables.json`
- `tests/fixtures-synthetic/specs/long-translation.json`
- `tests/fixtures-synthetic/specs/active-content.json`
- `tests/fixtures-synthetic/test_fixture_generation.py`

**先写失败测试**

- fixture 覆盖标准 A4、可 1:1 居中补白的裁切期刊页，以及任一轴超过 A4、必须最小等比缩小的页面；每个 fixture 均包含预期的 band、栏、角色和跨栏/跨页真值清单。
- 相同 spec 两次生成得到相同规范化结构；随机内容、当前时间和系统字体不得进入 fixture。
- fixture 文本、图形和表格全部由项目原创生成，并在 `TEST_DATA_ATTRIBUTION.md` 标记为 CC0。

**最小实现**

- 使用固定输入 JSON 生成覆盖设计测试矩阵的合成论文，不复制任何真实论文版式、正文或图表。
- 在 PDF 内嵌稳定的 fixture ID 和预期结构真值，但不让生产解析器依赖这些测试标记。
- 只生成后续门槛需要的最小场景，不提前制作装饰性示例。

**完成标准**

```powershell
uv run pytest tests/fixtures-synthetic/test_fixture_generation.py -q
```

- 所有 fixture 可重复生成且来源清楚。

**建议本地提交**：`test: add CC0 synthetic academic PDF fixtures`

### 任务 4：通过左页矢量合成门槛 G1

**创建文件**

- `src/academic_pdf_en_zh_reader/rendering/page_geometry.py`
- `src/academic_pdf_en_zh_reader/rendering/vector_compose.py`
- `tests/rendering/test_page_geometry.py`
- `tests/rendering/test_vector_compose.py`
- `tests/rendering/test_left_page_visual_equivalence.py`

**先写失败测试**

- 输出页面必须精确为 ISO A3 横向；左半页边界为 A4 宽度。
- 对正常 A4、裁切 CropBox、旋转、透明度、Form XObject、矢量线、图片和嵌入字体页面执行确定性归一化：两轴均可放入 A4 时保持 1:1，任一轴超出时只做足以放入的最小统一缩小，并对所有余量居中补白。
- pypdfium2 按已记录的统一变换渲染源页与输出左半页，像素差在固定容差内。
- 检查输出没有覆盖左半页的新增整页图像对象，源文字仍可选择。
- 非 A4 尺寸本身必须通过；无效或不可解释页面盒仍须失败，不能用缩放掩盖结构错误。

**最小实现**

- 使用整数 milli-point 保存 ISO 几何，最终调用 pypdf 时才转为 point。
- 以显示方向的 CropBox 对应轴为准：两轴都不超过 A4 时比例固定为 1；否则取不大于 1 且刚好能同时放入两轴的统一比例。严禁放大、裁切和拉伸。
- 生成不可变的 A4 `normalized-source.pdf` 与绑定原始上传哈希、预检哈希、归一化 PDF 哈希和逐页变换的 `normalization.json`；提取、合成、渲染和 QA 只使用作业内该归一化 PDF。
- 用 pypdf 创建 A3 空白页，将归一化 A4 页以 1:1 合入左半页并保留必要资源。
- 不复制源文档级动作、附件、表单和注释。

**完成标准**

```powershell
uv run pytest tests/rendering/test_page_geometry.py tests/rendering/test_vector_compose.py tests/rendering/test_left_page_visual_equivalence.py -q
```

- 所有矢量矩阵测试通过；任一场景只能靠截图通过时，G1 判定失败并停止。先判断是否应把该类页面明确移出 v1 输入边界；如必须更换 PDF 引擎，则返回任务 1 重新做许可证、分发和安全审查，不能直接加入 PyMuPDF、Ghostscript 或未审计组件。

**建议本地提交**：`feat: preserve source pages as A3 vector left panels`

### 任务 5：通过安全 Worker 门槛 G2

**创建文件**

- `src/academic_pdf_en_zh_reader/security/input_copy.py`
- `src/academic_pdf_en_zh_reader/security/limits.py`
- `src/academic_pdf_en_zh_reader/security/worker_protocol.py`
- `src/academic_pdf_en_zh_reader/security/windows_worker.py`
- `src/academic_pdf_en_zh_reader/security/unsupported_worker.py`
- `scripts/probe_worker_sandbox.py`
- `tests/security/test_input_copy.py`
- `tests/security/test_worker_protocol.py`
- `tests/security/test_windows_worker_limits.py`
- `tests/security/test_unsupported_platform.py`

**先写失败测试**

- 普通文件才能复制；symlink、junction、reparse point、目录、设备文件、TOCTOU 替换和解析后越出临时根的路径被拒绝。
- 副本必须重新计算 SHA-256，并使用仅当前用户可访问的私有临时目录。
- Worker 超时、超内存、超进程数、派生子进程或返回超长/非 schema 数据时被父进程终止。
- Job Object 关闭后子进程全部消失；零 capability AppContainer 不具有多余权限，工作区外文件、回环网络和父进程危险访问必须失败。
- 未实现等效安全适配器的平台必须返回明确错误，不退化成普通 `subprocess`。

**最小实现**

- Windows 生产入口使用零 capability AppContainer、精确句柄白名单、kill-on-close Job Object、进程数/CPU/内存/墙钟限制和仅当前用户与该 AppContainer 可访问的一次性工作目录。
- AppContainer API、profile 创建或任一必需控制不可用时立即失败；不得以 restricted-token-only 或普通子进程继续。restricted token 路径只作为私有测试适配器验证 Job 与令牌机制，不向生产调用方暴露。
- 父子进程只通过限长、版本化 JSON 协议交换受控相对路径和结果摘要。

**完成标准**

```powershell
uv run pytest tests/security -q
uv run python scripts/probe_worker_sandbox.py
```

- 所有安全探针通过；解析库尚未接触用户原始路径。

**建议本地提交**：`feat: add fail-closed PDF worker isolation`

### 任务 6：实现版本化数据契约、规范化哈希和作业状态

**创建文件**

- `src/academic_pdf_en_zh_reader/schema/*.schema.json`
- `src/academic_pdf_en_zh_reader/schema/validate.py`
- `src/academic_pdf_en_zh_reader/job/canonical_json.py`
- `src/academic_pdf_en_zh_reader/job/hashing.py`
- `src/academic_pdf_en_zh_reader/job/state.py`
- `src/academic_pdf_en_zh_reader/job/storage.py`
- `tests/schema/test_contracts.py`
- `tests/job/test_canonical_json.py`
- `tests/job/test_state_machine.py`
- `tests/job/test_resume_revision.py`

**先写失败测试**

- 为 `source`、`units`、`translation`、`review`、`annotations`、`frame-graph`、`layout`、`render-manifest`、`qa` 和 `provenance` 建立版本化 schema。
- 同时冻结个人纠错库向翻译阶段暴露的最小只读建议 schema；此时不实现数据库写入。
- canonical JSON 使用 UTF-8、NFC、排序键、整数几何、固定数组顺序并拒绝 NaN/Infinity。
- 作业状态只能按设计单向推进；前一步哈希不匹配、越级或重复写入时失败。
- 同一 job 可复用已验证阶段；用户“重新运行”必须创建新的 `translation_revision` 并使翻译阶段失效。
- `layout_input_hash` 覆盖设计列出的全部输入、锁文件、字体哈希和运行时指纹。

**最小实现**

- 使用标准库 dataclass/枚举加 JSON Schema 校验；不引入额外对象框架。
- 所有阶段先写临时文件、校验并原子替换。
- 稳定 ID 由页、阅读序、角色和源字符范围生成，不使用随机 UUID 参与布局。

**完成标准**

```powershell
uv run pytest tests/schema tests/job -q
```

- 状态、恢复、新 revision 和确定性哈希测试全部通过。

**建议本地提交**：`feat: define deterministic job and artifact contracts`

### 任务 7：实现安全预检

**创建文件**

- `src/academic_pdf_en_zh_reader/preflight/checks.py`
- `src/academic_pdf_en_zh_reader/preflight/page_boxes.py`
- `src/academic_pdf_en_zh_reader/preflight/pdf_catalog.py`
- `scripts/preflight.py`
- `tests/preflight/test_input_boundaries.py`
- `tests/preflight/test_catalog_inventory.py`
- `tests/preflight/test_resource_limits.py`

**先写失败测试**

- 接受具有可提取英文文本层、未加密且页面盒和声明旋转可解释的英文数字版 PDF；页面不是 A4 本身不构成拒绝理由。
- 扫描件、加密、损坏、无效页面盒、不可解释旋转或正文文本不足时在翻译前停止。
- 记录但不执行 JavaScript、OpenAction、附件、表单、富媒体和提交动作；后续合成不得继承它们。
- 页数、文件大小、对象数、递归深度、解压流和图像总量超限时安全停止。
- 所有检查在 G2 Worker 内对安全副本执行。

**最小实现**

- 输出结构化预检结果和明确错误码，不生成半成品。
- 预检记录显示方向 CropBox 的精确几何，不在预检中改写页面；通过后的受限归一化 worker 按固定规则决定 1:1 补白或最小统一缩小。

**完成标准**

```powershell
uv run pytest tests/preflight -q
```

**建议本地提交**：`feat: reject unsupported and unsafe PDF inputs`

### 任务 8：提取字符、图形与基础内容块

**创建文件**

- `src/academic_pdf_en_zh_reader/extraction/page_objects.py`
- `src/academic_pdf_en_zh_reader/extraction/text_lines.py`
- `src/academic_pdf_en_zh_reader/extraction/blocks.py`
- `src/academic_pdf_en_zh_reader/extraction/repeated_marginals.py`
- `scripts/extract.py`
- `tests/extraction/test_page_objects.py`
- `tests/extraction/test_text_lines.py`
- `tests/extraction/test_repeated_marginals.py`

**先写失败测试**

- 一次提取字符、字体、文字行、矩形、曲线、图像和页面框，坐标统一为整数 milli-point。
- 重复页眉、页脚、页码和水印被显式标记，不进入正文覆盖率。
- 图、表、caption 和正文引用建立稳定对象关系；低置信度图内/表内文本保留置信度而不伪装成正文。
- 相同 fixture 重跑得到相同对象顺序和稳定 ID。

**最小实现**

- 使用 pdfplumber/pdfminer.six 提取；只保留后续拓扑和语义判断实际需要的字段。
- 不在此阶段猜测翻译或最终坐标。

**完成标准**

```powershell
uv run pytest tests/extraction -q
```

**建议本地提交**：`feat: extract deterministic PDF page objects`

### 任务 9：通过混合拓扑门槛 G3

**创建文件**

- `src/academic_pdf_en_zh_reader/topology/projection.py`
- `src/academic_pdf_en_zh_reader/topology/xy_cut.py`
- `src/academic_pdf_en_zh_reader/topology/bands.py`
- `src/academic_pdf_en_zh_reader/topology/roles.py`
- `src/academic_pdf_en_zh_reader/topology/reading_order.py`
- `src/academic_pdf_en_zh_reader/topology/confidence.py`
- `tests/topology/test_xy_cut.py`
- `tests/topology/test_mixed_bands.py`
- `tests/topology/test_roles.py`
- `tests/topology/test_reading_order.py`
- `tests/topology/test_low_confidence.py`

**先写失败测试**

- 单栏、双栏、三栏和“首页上跨栏、下双栏”结构与 fixture 真值一致。
- 标题、摘要、关键词、小标题、正文、caption 与明确不翻译角色正确区分。
- 阅读顺序 DAG 稳定、无环；相同几何按页、栏和源对象序号裁决。
- 低置信度结构返回 `NEEDS_TOPOLOGY_REVIEW`；不得静默单栏化。
- 右侧镜像结构保留每个 band 的栏数、栏宽比例、栏间距和固定栏左边界。

**最小实现**

- 实现确定性占用投影、barrier 检测和递归 XY-cut。
- 只使用可解释的字体层级、位置、编号、关键词与邻接特征；GROBID 不进入默认链。

**完成标准**

```powershell
uv run pytest tests/topology -q
```

- 全部结构 fixture 通过，G3 才算完成。

**建议本地提交**：`feat: detect deterministic academic page topology`

### 任务 10：合并跨栏、跨页语义单元

**创建文件**

- `src/academic_pdf_en_zh_reader/extraction/unit_merge.py`
- `src/academic_pdf_en_zh_reader/extraction/unit_mapping.py`
- `tests/extraction/test_cross_column_units.py`
- `tests/extraction/test_cross_page_units.py`
- `tests/extraction/test_nontranslatable_roles.py`

**先写失败测试**

- 英文段落在栏末或页末断开时合并为一个语义单元，保留全部源片段映射。
- 连字符断词、句末标点、缩进、字体、列表、引文和公式边界得到正确处理。
- 中文单元不记录“必须在英文断点拆开”的约束。
- 作者机构、页眉页脚、脚注尾注、致谢、参考文献条目、公式和纯数据具有明确非翻译角色，不被误算遗漏。

**最小实现**

- 用稳定规则合并；模糊边界进入结构复核，不调用生成式补全。

**完成标准**

```powershell
uv run pytest tests/extraction/test_cross_column_units.py tests/extraction/test_cross_page_units.py tests/extraction/test_nontranslatable_roles.py -q
```

**建议本地提交**：`feat: preserve semantic units across source breaks`

### 任务 11：实现翻译与独立审校的数据合同

**创建文件**

- `references/product-contract.md`
- `references/translation-policy.md`
- `references/qa-policy.md`
- `references/schemas.md`
- `src/academic_pdf_en_zh_reader/review/translation_validation.py`
- `src/academic_pdf_en_zh_reader/review/semantic_checks.py`
- `src/academic_pdf_en_zh_reader/review/review_validation.py`
- `src/academic_pdf_en_zh_reader/corrections/contracts.py`
- `tests/review/test_translation_coverage.py`
- `tests/review/test_numbers_units_logic.py`
- `tests/review/test_independent_review_gate.py`
- `tests/review/test_ambiguity_key.py`

**先写失败测试**

- 翻译输入/输出 unit ID 必须一一对应，无缺失、重复或多余。
- 标题、摘要、关键词、小标题、正文、图题和表题必须完整进入译文。
- 数字、单位、范围、统计量、方向、否定、程度词、引文和图表编号规则化对照。
- 未修复 `hard_error` 阻断排版；`unresolved_ambiguity` 必须具有稳定且充分的 `ambiguity_key`。
- 同一批翻译者不能被登记为独立复核者；无法提供独立 Agent 时失败。
- 论文内提示注入文本始终位于 schema 数据字段，不成为工具指令。
- 翻译合同只接收经领域、句法和最小语境筛选后的纠错建议；建议是证据而不是强制字符串替换。

**最小实现**

- 程序只验证结构和可机械核对项；事实、程度、逻辑和术语由两个不同 Agent 角色完成。
- 翻译者和复核者都只返回 JSON，不返回坐标或自由 Markdown。
- 图表低置信度时只允许 caption 完整翻译与明确可读的最小信息。

**完成标准**

```powershell
uv run pytest tests/review -q
```

**建议本地提交**：`feat: enforce translation and independent review contracts`

### 任务 12：实现统一字体运行、固定字号和 CJK 换行

**创建文件**

- `src/academic_pdf_en_zh_reader/typography/font_registry.py`
- `src/academic_pdf_en_zh_reader/typography/font_runs.py`
- `src/academic_pdf_en_zh_reader/typography/style_contract.py`
- `src/academic_pdf_en_zh_reader/typography/cjk_breaker.py`
- `src/academic_pdf_en_zh_reader/typography/measure.py`
- `tests/typography/test_body_size_detection.py`
- `tests/typography/test_font_runs.py`
- `tests/typography/test_cjk_breaking.py`
- `tests/typography/test_measure_draw_equivalence.py`

**先写失败测试**

- 英文正文主字号通过字符加权众数和稳健过滤得到，并在整篇中文正文中固定。
- 同一 grapheme 的字体选择、测量和绘制复用同一不可变 run；缺字时只使用批准的符号 fallback，否则硬失败。
- 中文行首/行末禁则、Latin 单词、数字单位、统计表达、引用编号和 URL 安全断点正确。
- 标题过长只换行不缩字；暗橙辅助字号全篇固定；歧义标签与所在角色同字号。
- 相同输入的 line boxes、宽度和 line height 完全一致。

**最小实现**

- 所有度量统一使用 ReportLab 已注册字体，不混用 Pillow 或浏览器测量。
- 原型标定一次正文行距、辅助字号下限和角色映射，写入版本化配置。

**完成标准**

```powershell
uv run pytest tests/typography -q
```

**建议本地提交**：`feat: add deterministic CJK typography pipeline`

### 任务 13：构建镜像 FrameGraph

**创建文件**

- `src/academic_pdf_en_zh_reader/layout/frame_graph.py`
- `src/academic_pdf_en_zh_reader/layout/unit_parts.py`
- `src/academic_pdf_en_zh_reader/layout/band_geometry.py`
- `tests/layout/test_frame_graph.py`
- `tests/layout/test_mirrored_columns.py`
- `tests/layout/test_figure_band_height.py`
- `tests/layout/test_unit_parts.py`

**先写失败测试**

- 每个源 band/栏生成稳定原生 frame，边只沿正确阅读顺序连接。
- 多栏的每个译文栏拥有固定左边界；同栏块和重复运行不发生横向漂移。
- 跨栏/跨页同一语义单元通过 `unit_part` 自然延续，只在第一次出现处创建锚点。
- 图表 band 的右侧高度只由 caption 和获准暗橙内容决定，不保留原图等高空白。
- 普通单元不能占用无关下一源页；续页节点初始只作为候选。

**最小实现**

- 先计算固定栏宽下的 line boxes，再求 band 最小内容高度和最终 frame。
- FrameGraph 只表达合法流动和断点，不负责翻译或标注选择。

**完成标准**

```powershell
uv run pytest tests/layout/test_frame_graph.py tests/layout/test_mirrored_columns.py tests/layout/test_figure_band_height.py tests/layout/test_unit_parts.py -q
```

**建议本地提交**：`feat: construct mirrored translation frame graph`

### 任务 14：通过局部求解 G4 与续页 G5

**创建文件**

- `src/academic_pdf_en_zh_reader/layout/window_search.py`
- `src/academic_pdf_en_zh_reader/layout/isotonic.py`
- `src/academic_pdf_en_zh_reader/layout/solver.py`
- `src/academic_pdf_en_zh_reader/layout/continuation_dp.py`
- `tests/layout/test_window_expansion.py`
- `tests/layout/test_bounded_l1_isotonic.py`
- `tests/layout/test_zero_overlap.py`
- `tests/layout/test_continuation_dp.py`
- `tests/layout/test_cross_break_flow.py`
- `tests/layout/test_layout_determinism.py`

**先写失败测试**

- 影响范围严格按“当前块→相邻块→扩大窗口→整栏/对应 band”扩展，窗口外坐标冻结。
- `layout.json` 或内部求解 trace 记录实际采用的释放阶段、窗口边界、最小 `D` 和续页原因，使“最小限度原则”可以机械验收。
- 固定窗口先二分最小最大位移 `D`，再以有界加权 L1 isotonic regression 最小化总位移。
- 固定字号、阅读顺序、镜像拓扑和零重叠是硬约束；首行对齐可上下让步。
- 原生 frame 全部无解后才激活续页；DP 依次最小化续页数、拆分数和断点质量。
- 中文跨英文栏/页断点连续输出，只在实际空间不足时于合法中文行界拆分。
- 孤行、寡行、标题随文违规不可行；相同代价使用稳定 ID 裁决。
- 候选断点或 DP 状态超过版本化复杂度上限时返回 `LAYOUT_COMPLEXITY_LIMIT`；不得静默换成贪心、删译或缩字。
- 同一完整 `layout_input_hash` 两次运行得到完全相同的 `layout.json`。

**最小实现**

- 先实现无拆分 block 的 PAVA，并用小规模穷举对照最优解；再加入 FrameGraph 合法行断点和续页 DP，不引入通用约束求解器。
- 续页紧跟对应源页，左侧重复引用原页内容流，右侧带固定低调“译文续页”标识。

**完成标准**

```powershell
uv run pytest tests/layout -q
```

- 密集页和极端长译文 fixture 在不缩字、不删译、不重叠的前提下通过，G4/G5 才算完成。

**建议本地提交**：`feat: solve minimal reflow and continuation pages`

### 任务 15：实现重点、教学和歧义标注选择

**创建文件**

- `src/academic_pdf_en_zh_reader/annotations/red_emphasis.py`
- `src/academic_pdf_en_zh_reader/annotations/orange_candidates.py`
- `src/academic_pdf_en_zh_reader/annotations/figure_notes.py`
- `src/academic_pdf_en_zh_reader/annotations/ambiguity.py`
- `src/academic_pdf_en_zh_reader/annotations/selection.py`
- `references/layout-policy.md`
- `tests/annotations/test_red_ratio.py`
- `tests/annotations/test_orange_priority.py`
- `tests/annotations/test_first_occurrence_deferral.py`
- `tests/annotations/test_figure_notes.py`
- `tests/annotations/test_ambiguity_marks.py`

**先写失败测试**

- 摘要暗红字符数为零；其余中文主体目标约 6%，但没有最低配额，硬上限 10%。
- 暗红分母排除标题、摘要、关键词、橙色和歧义标签并忽略空白/纯标点。
- 普通教学标注必须位于对应中文译文下方，严格使用 `English original — 中文意思` 格式和全篇固定辅助字号。
- 单次出现的高价值术语优先；会重复的项目在前部拥挤时可推迟到后文首次可容纳位置。
- 普通暗橙候选不能新增续页；图表暗橙优先于普通暗橙，最多 1–3 条且必须有直接证据。
- 必选译文先布局，暗橙候选按稳定顺序试放，冻结候选后才做最终布局，不能形成内容—布局循环。
- 最终未决歧义使用亮红下划线；每个 `ambiguity_key` 仅第一次出现同字号提示。

**最小实现**

- Agent 返回候选与证据；程序负责比例、优先级、重复次数、首次位置、空间试放和硬上限。
- 颜色作为版本化设计令牌固定，不按论文随机变化。

**完成标准**

```powershell
uv run pytest tests/annotations -q
```

**建议本地提交**：`feat: select constrained learning annotations`

### 任务 16：绘制右侧译文、虚线和最终 PDF

**创建文件**

- `src/academic_pdf_en_zh_reader/rendering/text_draw.py`
- `src/academic_pdf_en_zh_reader/rendering/leaders.py`
- `src/academic_pdf_en_zh_reader/rendering/overlay.py`
- `src/academic_pdf_en_zh_reader/rendering/compose.py`
- `src/academic_pdf_en_zh_reader/rendering/metadata.py`
- `scripts/compose_pdf.py`
- `tests/rendering/test_text_styles.py`
- `tests/rendering/test_leaders.py`
- `tests/rendering/test_multicolumn_no_leaders.py`
- `tests/rendering/test_final_composition.py`

**先写失败测试**

- ReportLab 只按不可变 `layout.json` 绘制，不在绘制阶段重新换行或改变坐标。
- 中文译文为可搜索矢量文本；黑、暗红、暗橙和亮红样式只改变规定属性。
- 单栏/跨栏块从源首行到译文首行绘制灰色虚线；多栏 band 一律没有 leader。
- 水平可对齐时直连；上下调整时只在中心通道路由圆角三段折线，线不穿文字、不交叉。
- 左侧仍为 G1 的原文矢量页；续页重复原页且标识不覆盖左侧。
- 时间戳不进入可见内容，元数据按确定性规则规范化。

**最小实现**

- ReportLab 使用 invariant 模式绘制 A3 overlay；pypdf 先合入原页再合入 overlay。
- leader lane 按稳定单元 ID 分配，冲突时失败并返回求解器重排，不偷偷覆盖。

**完成标准**

```powershell
uv run pytest tests/rendering -q
```

**建议本地提交**：`feat: render deterministic bilingual A3 PDFs`

### 任务 17：实现机械 QA、清理和原子交付

**创建文件**

- `src/academic_pdf_en_zh_reader/qa/semantic.py`
- `src/academic_pdf_en_zh_reader/qa/geometry.py`
- `src/academic_pdf_en_zh_reader/qa/fonts.py`
- `src/academic_pdf_en_zh_reader/qa/pdf_structure.py`
- `src/academic_pdf_en_zh_reader/qa/raster_compare.py`
- `src/academic_pdf_en_zh_reader/job/cleanup.py`
- `src/academic_pdf_en_zh_reader/job/deliver.py`
- `scripts/qa_pdf.py`
- `scripts/deliver.py`
- `tests/qa/test_semantic_gate.py`
- `tests/qa/test_geometry_gate.py`
- `tests/qa/test_active_content_rejection.py`
- `tests/qa/test_fonts_and_glyphs.py`
- `tests/job/test_cleanup.py`
- `tests/job/test_atomic_delivery.py`

**先写失败测试**

- 全部应译单元完整、唯一、有序，且独立审校无 hard error。
- glyph、文字、下划线、虚线和辅助块全部在合法 frame 内且零非法重叠。
- A3 页面尺寸、归一化逐轴决策及居中补白、归一化 A4 左页 1:1、镜像栏、固定左边界、leader 规则、续页与固定字号全部正确。
- 字体嵌入、字符覆盖、左页视觉等价和无新增左侧整页栅格图通过。
- 最终 PDF 显式拒绝 `/OpenAction`、`/AA`、`/JavaScript`、`/JS`、`/EmbeddedFiles`、`/Filespec`、`/AcroForm`、`/Launch`、`/RichMedia` 和提交动作。
- 任一 QA 失败时交付目录没有 PDF；全部通过后才原子移动 `<原文件名>.bilingual-a3.zh-CN.pdf`。
- 成功立即清理；失败/取消默认立即清理；明确 resume/debug 最多保留 24 小时并由启动清扫器删除过期作业。

**最小实现**

- 内部生成 `qa.json`、渲染页和接触表，但正常用户交付只包含最终 PDF。
- 日志只记录错误码、哈希前缀和计数，不记录论文全文、作者身份或纠错内容。

**完成标准**

```powershell
uv run pytest tests/qa tests/job/test_cleanup.py tests/job/test_atomic_delivery.py -q
```

**建议本地提交**：`feat: gate and atomically deliver validated PDFs`

### 任务 18：实现个人纠错记忆

**创建文件**

- `src/academic_pdf_en_zh_reader/corrections/database.py`
- `src/academic_pdf_en_zh_reader/corrections/normalize.py`
- `src/academic_pdf_en_zh_reader/corrections/retrieve.py`
- `src/academic_pdf_en_zh_reader/corrections/commands.py`
- `scripts/corrections.py`
- `tests/corrections/test_authorized_write.py`
- `tests/corrections/test_contextual_reuse.py`
- `tests/corrections/test_undo_delete_export.py`
- `tests/corrections/test_privacy.py`

**先写失败测试**

- 只有用户明确更正已经亮红标注的歧义时才允许写入；普通反馈和模型猜测不能写入。
- 数据库位于用户级数据目录，不在 Skill、项目、作业目录或 Git 内。
- 同词/近义表达和跨词性记录只能作为有领域、句法和最小语境约束的建议，不能机械替换。
- 冲突或新语境证据优先触发重新审校/歧义，而不是服从旧记录。
- 查看、导出、逐条删除、撤销和清空可用；日志和导出默认不包含不必要论文片段。

**最小实现**

- 使用 Python 标准库 SQLite；表结构保存规范化表达、用户认可译法、上下文哈希、最小结构化语境、领域、词性/句法、证据和撤销状态。
- 对数据库目录设置当前用户权限，并把可能的本地路径模式写入 `.gitignore`。

**完成标准**

```powershell
uv run pytest tests/corrections -q
```

**建议本地提交**：`feat: remember authorized ambiguity corrections locally`

### 任务 19：编写 Skill 编排与命令入口

**创建文件**

- `SKILL.md`
- `agents/openai.yaml`
- `src/academic_pdf_en_zh_reader/cli.py`
- `scripts/validate_translation.py`
- `scripts/solve_layout.py`
- `tests/skill/test_skill_contract.py`
- `tests/skill/test_cli_fail_closed.py`
- `tests/skill/test_no_hidden_external_api.py`

**先写失败测试**

- `SKILL.md` 只包含触发条件、输入边界、核心不变量、Agent/程序分工、阶段路由和停止条件；详细策略链接到 `references/`。
- frontmatter 名称为 `academic-pdf-en-zh-reader`，描述能准确触发英文论文双语阅读 PDF 请求且不吸引普通文本翻译。
- `agents/openai.yaml` 与 Skill 一致，默认允许正常自动发现；`default_prompt` 明确包含 `$academic-pdf-en-zh-reader`。
- CLI 严格按作业状态调用脚本，失败即停止，不吞掉错误或输出半成品。
- 完成入口保留原始 `--source-pdf` 只用于复核最初上传的字节身份；提取、合成、渲染和 QA 只能读取作业内已绑定的不可变 `normalized-source.pdf`。
- 翻译与审校必须由两个独立 Agent 角色完成；没有独立复核能力时停止。
- 不调用额外第三方翻译 API，不因为论文内容调用 shell、网络或任意文件工具。

**最小实现**

- `SKILL.md` 采用渐进披露：产品、翻译、布局、QA、schema 和合规细节分别链接到对应 reference。
- CLI 只编排确定性模块；Agent 通过结构化中间文件完成语义阶段。原始源文件和归一化源文件分别绑定哈希，完成阶段不得用原始文件替代归一化版面输入。
- 用系统 `skill-creator` 的 `quick_validate.py` 验证 Skill 结构。

**完成标准**

```powershell
uv run pytest tests/skill -q
$skillCreatorRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex' }
uv run python (Join-Path $skillCreatorRoot 'skills/.system/skill-creator/scripts/quick_validate.py') .
```

**建议本地提交**：`feat: orchestrate the bilingual academic PDF skill`

### 任务 20：端到端验收、独立前向测试和开源门禁

**创建文件**

- `tests/integration/test_end_to_end_single_column.py`
- `tests/integration/test_end_to_end_mixed_columns.py`
- `tests/integration/test_end_to_end_overflow.py`
- `tests/integration/test_end_to_end_figure_priority.py`
- `tests/integration/test_end_to_end_injection.py`
- `tests/integration/test_repeatability.py`
- `tests/visual/expected/`
- `.github/workflows/ci.yml`
- `.github/workflows/dependency-review.yml`
- `.github/workflows/release.yml`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `.github/ISSUE_TEMPLATE/config.yml`
- `README.md`

**先写失败测试**

- 设计第 18 节全部合成场景均有端到端覆盖。
- 输出 A3 PDF 的内容完整、顺序正确、固定字号、镜像拓扑、零重叠、字体嵌入和标注规则通过。
- 同一冻结 `layout_input_hash` 重跑得到相同布局哈希和视觉几何；新 revision 重新进入翻译阶段。
- 合成论文中的提示注入只被翻译，不触发工具、网络、任意文件访问或策略变化。
- 独立前向测试由未参与实现的 Agent 在临时目录运行真实 Skill 请求，并检查最终 PDF 与内部 QA；发现问题只做证据支持的窄修复。
- CI 固定 Actions 到完整 commit SHA，执行测试、ruff、REUSE、依赖/许可证检查和安全扫描。
- Release 生成源码包与发布包的 SPDX SBOM、资产清单、NOTICE 核对和 SHA-256；未知许可、缺失署名、严重漏洞或隐私泄漏阻断发布。

**最小实现**

- 只使用 CC0 合成论文制作 README 截图、测试和演示。
- README 明确输入边界、数据流、独立社区身份、上游致谢、用户论文/译文不受项目许可证覆盖，以及生成译文不等于取得公开传播权。
- PR 模板包含 DCO、AI-assisted、影响文件、人工检查、测试、public-code match 和机密/用户论文确认字段。
- 真实公开维护身份、私密报告入口及所有必要版权署名与素材权利未确认前，Release 工作流保持机器可验证的阻断状态；报告入口可同时处理行为准则与安全问题，不强制独立备用联系人。LPAC 真实生产验证、安全、依赖和隐私检查仍须通过，不得把开发态 `PUBLIC_RELEASE_BLOCKED` 标记带入发布包。

**完成标准**

```powershell
uv run pytest -q
uv run ruff check .
reuse lint
uv run python scripts/check_dependency_policy.py
```

- 独立前向测试通过。
- 所有设计验收项均有自动证据或明确的人工视觉证据。
- 仅在用户另行明确确认后，才执行推送、正式 Release 或公开发布。

**建议本地提交**：`test: complete end-to-end and release gates`

## 6. 任务依赖和停止点

| 任务 | 依赖 | 失败后的动作 |
|---|---|---|
| 1 | 无 | 修正许可证/版本/来源；不生成锁文件 |
| 2 | 1 | 更换合规官方字体构建或停止 |
| 3 | 2 | 修正 fixture 生成器；不使用真实论文替代 |
| 4 | 3 | 修正 pypdf 合入路线；不截图兜底 |
| 5 | 1 | 修正 Windows 隔离；不允许无隔离解析 |
| 6–10 | 2、3、5 | 保持在确定性解析层，不进入 Agent 翻译 |
| 11 | 6、10 | 缺独立复核能力则停止 |
| 12–14 | 2、6、9、10 | 不通过缩字、删译或乱序绕过 |
| 15 | 11–14 | 删除/推迟普通橙色，而非牺牲正文 |
| 16 | 4、12–15 | 不产生半成品 PDF |
| 17 | 4、12–16 | 不交付未通过机械 QA 的 PDF |
| 18 | 11 | 纠错授权边界不清时不写入 |
| 19 | 1–18 | Skill 校验失败则不安装/不发布 |
| 20 | 全部 | 任一门禁失败则不推送、不 Release |

可并行的范围仅限相互不改同一契约的工作：任务 4 与任务 5 可在任务 3 后并行；社区文档完善可与中后期代码并行。Schema、字体、FrameGraph 和布局合同属于共享核心，必须串行冻结后再让下游依赖。

## 7. 用户核心要求追踪

| 核心要求 | 主要实现任务 | 主要验收证据 |
|---|---|---|
| 支持的原论文经确定性 A4 归一化后转为 A3 左原文右译文 | 4、7、16、17 | 原始/归一化哈希、逐页变换、A3 几何、左页视觉等价、矢量内容检查 |
| 首页混合分栏及多栏镜像 | 9、13、20 | mixed/two/three-column fixture |
| 多栏无虚线；单栏首行引线 | 13、16、17 | leader 与 no-leader 测试 |
| 所有译文栏固定左边界 | 9、13、17 | mirrored-column 坐标断言 |
| 完整译文、固定字号、正确顺序、零重叠 | 10–14、17、20 | 覆盖率、字号、阅读序和几何 QA |
| 仅在必要时上下重排、整栏、续页 | 14 | 窗口扩展与 continuation DP 测试 |
| 英文跨栏/跨页不强迫中文同点拆分 | 10、13、14 | cross-break-flow 测试 |
| 图题表题完整；图表橙色优先 | 11、15、20 | figure-priority 端到端测试 |
| 普通暗橙不挤占正文，单次词优先 | 15 | 候选试放、推迟与优先级测试 |
| 暗红约 6%、硬上限 10%、摘要不标 | 15、17 | 精确分母和比例门禁 |
| 事实/程度/逻辑/数据/术语准确 | 11、17 | 双 Agent 审校与机械对照 |
| 亮红歧义及首次提示 | 11、15、17 | `ambiguity_key` 和视觉 span 测试 |
| 用户更正可跨论文参考但不机械替换 | 18 | 授权写入与语境适配测试 |
| 多次运行布局稳定、无乱码 | 2、6、12、14、17、20 | hash、字体、视觉重复运行测试 |
| 正常只交付最终 PDF、无质量报告 | 17、19、20 | 原子交付与目录内容测试 |
| 安全、隐私和开源合规 | 1、5、7、17、20 | Worker、清理、REUSE、SBOM 与发布门禁 |

## 8. 完整验收定义

只有同时满足以下条件，才能宣布 v1 实现完成：

1. G0–G5 六个原型门槛全部有可重复的测试证据。
2. 设计第 18 节测试矩阵全部落入自动测试或明确的独立视觉审查。
3. 输入边界内的标准 A4、裁切期刊页和需最小等比缩小的合成论文均生成合格 A3 双语 PDF；非 A4 尺寸本身不停止，真正越界或不安全的输入安全停止。
4. 中文译文完整准确、顺序正确、固定字号且零重叠。
5. 单栏 leader、多栏无 leader、镜像栏和续页规则全部正确。
6. 暗红、暗橙和亮红标注符合比例、优先级和空间规则。
7. 用户纠错只有经授权才写入，并位于仓库外的个人数据库。
8. 同一冻结布局输入的 JSON 哈希和视觉几何稳定。
9. 最终 PDF 无活动内容、附件、表单、未嵌入字体或左页栅格替换。
10. 用户正常只收到最终 PDF；临时数据按生命周期清理。
11. Skill 结构、触发描述和渐进披露通过验证与独立前向测试。
12. 依赖、字体、测试素材、许可证、NOTICE、REUSE 和 SBOM 门禁通过。
13. 未经用户最后明确确认，不进行推送、Release 或公开发布。

## 9. 实施结束后的交付物

实施完成但尚未公开发布时，应向用户交付：

- 完整 Skill 源码与锁定依赖。
- 全部 CC0 合成 fixture 和测试结果。
- 至少一份单栏、一份混合分栏和一份续页的内部验收 PDF。
- 字体、依赖、许可证、SBOM 和来源清单。
- 独立前向测试结论及仍存在的工程限制。
- 明确的“可本地试用 / 尚未公开发布”状态。

公开发布仍是独立阶段，必须在维护联系人、品牌、法律触发项、发布资产和用户授权全部就绪后另行执行。

## 10. 计划自审结论

本计划已完成两类独立复核并吸收修订：

1. 需求追踪复核确认全部核心用户要求均已映射到具体任务和可观察验收证据；特别补强了多栏零虚线、摘要零暗红、跨栏/跨页段落连续、橙色两阶段选择和正常交付仅含 PDF 等负向断言。
2. 技术顺序复核确认应先证明字体、矢量合成、安全 Worker、混合拓扑、局部求解和续页六个不可替代门槛，再实现 Agent 编排、教学标注和纠错库；特别补强了求解阶段 trace、DP 复杂度上限、TOCTOU、pypdf 失败路径和发布联系人阻断状态。

当前没有需要再次向用户确认的产品或技术歧义。实现阶段仍需以真实测试结果裁决字体 TTC、复杂 PDF 矢量合入和 Windows 隔离能力；门槛失败时按本计划停止或回到依赖准入步骤，不擅自降低核心要求。
