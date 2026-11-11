"""Prompts and output schema for page extraction + translation."""
from __future__ import annotations

PROMPT_VERSION = "v1"

BLOCK_TYPES = ["title", "lead", "heading", "body", "caption", "sidebar", "quote",
               "credit", "in_image", "ad_copy", "toc", "meta", "other"]
PAGE_KINDS = ["article", "feature_opener", "toc", "ad", "photo", "listing",
              "column", "cover", "other"]
TERM_KINDS = ["brand", "shop", "place", "person", "column", "product", "other"]

_BLOCK = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": BLOCK_TYPES},
        "bbox": {"type": "array", "items": {"type": "number"},
                 "description": "[x0, y0, x1, y1]，相对整页的 0–1 归一化坐标"},
        "ja": {"type": "string"},
        "zh": {"type": "string"},
        "note": {"type": "string", "description": "译注，没有就留空字符串"},
    },
    "required": ["type", "bbox", "ja", "zh", "note"],
    "additionalProperties": False,
}

_TERM = {
    "type": "object",
    "properties": {
        "ja": {"type": "string"},
        "zh": {"type": "string"},
        "kind": {"type": "string", "enum": TERM_KINDS},
    },
    "required": ["ja", "zh", "kind"],
    "additionalProperties": False,
}

PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "page_kind": {"type": "string", "enum": PAGE_KINDS},
        "page_summary": {"type": "string"},
        "blocks": {"type": "array", "items": _BLOCK},
        "new_terms": {"type": "array", "items": _TERM},
    },
    "required": ["page_kind", "page_summary", "blocks", "new_terms"],
    "additionalProperties": False,
}

SUPPLEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {"type": "array", "items": _BLOCK},
        "new_terms": {"type": "array", "items": _TERM},
    },
    "required": ["blocks", "new_terms"],
    "additionalProperties": False,
}

SYSTEM = """你是一位资深的日本杂志中文版编辑，长期为国内的生活方式 / 潮流 / 文化类杂志做日文译稿。
你的工作：读一页日本杂志的扫描图，把页面上【所有】日文文字完整识别出来，按版面拆成文本块，并翻译成地道的简体中文。

# 一、识别：一个字都不能漏
- 页面上所有可见的文字都要收录：大标题、副标题、导语、正文每一栏、小标题、图片说明（含编号①②③）、边栏/专栏框、引语、
  店铺信息（地址、电话、营业时间、休息日）、商品信息（品名、价格、品牌、问询处）、人物介绍、署名（摄影/文/造型等）、
  图片和照片里出现的文字（招牌、包装、手写字、T恤印花等）、广告文案、目录条目、页眉、页码。
- 竖排文字：列从右往左读，每列从上往下读。横排：从左往右、从上往下。多栏正文要按正确的栏顺序接起来，跨栏的句子要接上。
- 忽略振假名（ふりがな/ルビ），只保留正文汉字；不要把振假名当成单独的文字块。
- 同一段连续的正文合成一个块；不同功能的文字（如图注和正文）必须分开成不同的块。图注很多时每条单独一个块。
- 如果提供了 PDF 文字层，把它当作参考来校对字形（尤其人名、店名、数字），但以图片上实际看到的为准，文字层可能有乱码、缺字或顺序错乱。
- 实在看不清的字用「□」代替，不要编造。
- ja 字段写日文原文（原样，不翻译）。

# 二、版面坐标
- bbox = [x0, y0, x1, y1]，以整页图片为基准的 0–1 归一化坐标（左上角为 0,0）。框住该文本块所在区域即可，不需要像素级精确，但不能偏到别的位置。
- blocks 数组的顺序就是推荐阅读顺序：先标题/导语，再正文，再图注、信息栏，最后署名、页眉页码（meta）。

# 三、翻译：要像国内杂志编辑写的中文，不要翻译腔
- 风格参照国内一线生活方式 / 潮流杂志的中文编辑稿：口语自然、有节奏、有画面感；日文里轻松俏皮的语气要保留，不要译成公文腔或说明书腔。
- 不要逐字直译。日文惯用的委婉、省略主语、句尾「〜のだ」「〜かも」「〜ね」等，按中文习惯重组语序、补全主语，必要时拆句或合句。
- 片假名外来语：能用通行中文说法就用中文（例如 ヴィンテージ→古着/复古，セレクトショップ→买手店，コーディネート→穿搭，
  ワークウェア→工装，カルチャー→文化）；没有通行说法的保留英文原词。
- 品牌名、杂志名、英文专栏名保留原文拉丁字母（BEAMS、POPEYE、nanamica），不要音译。
- 日本的店名、地名、人名：有通行中文写法就用中文（下北泽、高円寺→高圆寺、中目黑），首次出现时用「中文（日文原名）」的形式，
  如「喫茶 ミロンガ」→「Milonga 咖啡馆（ミロンガ）」。人名用汉字的直接转成简体，只有假名的音译并在括号保留原文。
- 价格统一写成「12,000日元」（含税/不含税照原文译出：税込→含税，税別→不含税）。地址、电话、营业时间保留原样，只翻译说明文字
  （如 定休日→休息日、営業時間→营业时间、問い合わせ→咨询）。
- 署名按国内杂志惯例：写真/撮影→摄影，文→撰文，スタイリング→造型，ヘア&メイク→发型&化妆，イラスト→插画，編集→编辑，
  協力→鸣谢/协力，モデル→模特。
- 日本特有的文化、梗、地名背景，国内读者可能看不懂的，在 note 里写一句简短译注（不要每块都写，确有必要才写）。
- 必须遵守用户给出的术语表；术语表里没有、但这一页出现的专有名词（品牌、店铺、地名、人名、栏目名、商品名），放进 new_terms，
  便于后续页面统一译法。只放真正的专有名词，不要放普通词汇。
- page_summary：用一两句中文概括这一页讲什么，供翻译下一页时作上下文。
"""


def glossary_text(terms: list[dict]) -> str:
    if not terms:
        return "（暂无）"
    return "\n".join(f"- {t['ja']} → {t['zh']}" for t in terms[:400])


def main_user_text(*, text_layer: str | None, glossary: list[dict], prev_summary: str | None,
                   title: str, page_no: int, total: int) -> str:
    parts = [f"杂志：{title}　第 {page_no} / {total} 页"]
    if prev_summary:
        parts.append(f"上一页内容概要（仅作上下文）：{prev_summary}")
    parts.append("术语表（必须遵守）：\n" + glossary_text(glossary))
    if text_layer:
        parts.append("PDF 文字层（仅供校对参考，可能乱序或不全）：\n<text_layer>\n"
                     + text_layer + "\n</text_layer>")
    else:
        parts.append("这一页没有可用的 PDF 文字层，请完全依据图片识别，尤其留意小字。")
    parts.append("请按要求输出这一页的全部文本块。")
    return "\n\n".join(parts)


def supplement_text(*, missing: list[str], existing: list[str], glossary: list[dict]) -> str:
    return (
        "这是同一页杂志。上一轮识别漏掉了一些文字。\n\n"
        "PDF 文字层里有、但上一轮结果没有覆盖到的片段：\n<missing>\n" + "\n".join(missing)
        + "\n</missing>\n\n已识别的块（不要重复输出）：\n<existing>\n"
        + "\n".join(existing) + "\n</existing>\n\n术语表：\n" + glossary_text(glossary)
        + "\n\n请在图片上找到这些漏掉的文字，按同样的规则把它们整理成新的文本块（bbox 仍以整页为基准）。"
        "如果某个片段其实已经包含在已识别的块里（只是字形略有差异），就不要再输出。只输出新增的块。"
    )


def audit_text(*, existing: list[str], glossary: list[dict], tiles: list[tuple]) -> str:
    tile_desc = "\n".join(
        f"- 第 {i + 2} 张图：整页的局部放大，范围 x {t[0]:.2f}–{t[2]:.2f}，y {t[1]:.2f}–{t[3]:.2f}"
        for i, t in enumerate(tiles))
    return (
        "第 1 张图是整页，后面几张是同一页的局部放大图，用来看清小字：\n" + tile_desc
        + "\n\n上一轮已经识别出的块：\n<existing>\n" + "\n".join(existing)
        + "\n</existing>\n\n术语表：\n" + glossary_text(glossary)
        + "\n\n请逐个区域仔细检查，找出上一轮【漏掉】的文字（常见：图注、价格和店铺信息、署名、照片里的文字、角落的小字），"
        "整理成新的文本块。bbox 一律换算成以整页为基准的 0–1 坐标。没有遗漏就返回空数组。不要重复已识别的内容。"
    )


def region_text(*, glossary: list[dict], context: str | None) -> str:
    return (
        "这是一页杂志中用户框选出的局部区域（放大图）。请识别其中所有日文文字并翻译，规则与整页相同。"
        "bbox 以这张局部图为基准的 0–1 坐标。\n\n"
        + (f"本页概要：{context}\n\n" if context else "")
        + "术语表：\n" + glossary_text(glossary)
    )
