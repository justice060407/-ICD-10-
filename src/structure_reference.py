# -*- coding: utf-8 -*-
"""实验 3 · 病历结构化 —— 参考实现

用法：
    python src\\structure_reference.py --in out\\records_deid.jsonl --out out_lab3

定位说明（学生先读这段）：
    这是一份**及格线参考实现**，不是标准答案，更不是最优解。
    它的作用是：当你卡住超过 30 分钟、或者想确认"到底该长什么样"时，
    有个能跑通的东西可以对照。

    建议你按这个顺序用：
      1. 先自己写，卡住就查这里对应的那个函数
      2. 写完之后跑一遍这个，对比字段完整率
      3. 如果你的完整率比它高，说明你的设计更好，写进报告里

    它有几个**故意保留的缺陷**，你可以在报告里指出并改进：
      - 症状抽取只做字符串切分，没有用实验 2 的实体结果（会漏、会切错）
      - 用药抽取只认 "药名+数字+单位" 这一种写法
      - 住院天数 los_days 恒为 None（原始数据里确实没有）
"""

import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional

# ── 目标字段清单（一级字段，评分看的是这棵树设计得合不合理）──────────────
FIELDS: List[str] = [
    "basic",        # 基本人口学
    "admission",    # 入院信息
    "chief",        # 主诉
    "diagnosis",    # 诊断
    "exam",         # 检查
    "medication",   # 用药
]

# 检验项单位白名单。用白名单而不是"任意字母"，是为了避免把
# "中度非增殖期" 这种描述里的词误当成单位。
UNITS = ["mmol/L", "umol/L", "mg/g", "mg/dL", "g/L", "mmHg", "%", "ml", "mg", "g"]

VITAL_PATTERNS = [
    ("体温", r"T\s*[:：]?\s*(\d{2}(?:\.\d)?)\s*℃"),
    ("脉搏", r"P\s*[:：]?\s*(\d{2,3})\s*次"),
    ("呼吸", r"R\s*[:：]?\s*(\d{2})\s*次"),
    ("收缩压", r"BP\s*[:：]?\s*(\d{2,3})\s*/\s*(\d{2,3})"),
    ("舒张压", r"BP\s*[:：]?\s*\d{2,3}\s*/\s*(\d{2,3})"),
]

DRUG_PATTERN = re.compile(
    r"([一-龥]{2,12}?)\s*(\d+(?:\.\d+)?)\s*(mg|g|ml|IU|μg)"
)


def to_float(s: Any) -> Optional[float]:
    """安全转 float。

    为什么不能直接 float()：真实病历里，"未查"、"—"、"阴性"、"trace"
    这类值到处都是，直接强转会在第 37 条记录上崩掉整批任务。
    """
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip()
    if not t:
        return None
    # 去掉单位与常见噪声
    t = t.replace("℃", "").replace("%", "").strip()
    if t in {"—", "-", "未查", "未见", "阴性", "阳性", "正常", "不详", "无"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def age_band(age_str: Optional[str]) -> Optional[str]:
    """年龄 -> 年龄段。

    为什么存年龄段而不是具体年龄：具体年龄 + 科室 + 入院日期 三件套
    已经足够定位到个人，属于准标识符（实验 1 讲过）。
    """
    v = to_float(age_str)
    if v is None:
        return None
    v = int(v)
    if v < 18:
        return "0-17"
    if v < 45:
        return "18-44"
    if v < 60:
        return "45-59"
    if v < 75:
        return "60-74"
    return "75+"


def split_exams(text: str) -> List[Dict[str, Any]]:
    """把辅助检查段落拆成结构化检验项。

    目标形态：{"name": "空腹血糖", "value": 11.8, "unit": "mmol/L"}
    存成三段而不是 "11.8mmol/L" 一个字符串，是因为下游要做
    "糖化血红蛋白 > 9%" 这种范围查询 —— 字符串没法比大小。
    """
    items: List[Dict[str, Any]] = []
    unit_alt = "|".join(re.escape(u) for u in UNITS)
    pattern = re.compile(
        r"([一-龥A-Za-z]{2,20}?)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(" + unit_alt + r")"
    )
    for m in pattern.finditer(text or ""):
        name = m.group(1).strip()
        # 名字里混入前一个检验项残留（切分不干净的常见症状），截掉
        name = re.sub(r"^.*?[，,。;；]", "", name).strip()
        items.append({
            "name": name,
            "value": to_float(m.group(2)),
            "unit": m.group(3),
        })
    return items


def extract_vitals(text: str) -> Dict[str, Optional[float]]:
    vitals: Dict[str, Optional[float]] = {}
    for label, pat in VITAL_PATTERNS:
        m = re.search(pat, text or "")
        vitals[label] = to_float(m.group(1)) if m else None
    return vitals


def extract_symptoms(chief: str) -> List[str]:
    """从主诉里切症状。

    这是本实现的**已知弱点**：纯按标点切，会把
    "多饮、多尿、体重下降1个月" 切成三个，其中"体重下降1个月"
    还带着时长。实验 2 的 SYM 实体结果能做得更好 —— 留给你改进。
    """
    if not chief:
        return []
    parts = re.split(r"[、，,；;。]", chief)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # 去掉尾部时长修饰，如 "1个月"、"2周"
        p = re.sub(r"\d+\s*(个?月|周|天|日|年|小时)$", "", p).strip()
        if p:
            out.append(p)
    return out


def extract_drugs(text: str) -> List[Dict[str, Any]]:
    drugs: List[Dict[str, Any]] = []
    seen = set()
    for m in DRUG_PATTERN.finditer(text or ""):
        name = m.group(1).strip()
        # "每日三次" 之类的词会被误抓成药名，用白名单长度 + 去重挡掉大部分
        if len(name) < 2 or re.search(r"每日|每次|一天|bid|tid", name):
            continue
        key = (name, m.group(2), m.group(3))
        if key in seen:
            continue
        seen.add(key)
        drugs.append({"name": name, "dose": to_float(m.group(2)), "unit": m.group(3)})
    return drugs


def structure_one(rec: Dict[str, Any]) -> Dict[str, Any]:
    """把一条脱敏后的病历转成结构化 JSON。"""
    sections = rec.get("sections", {})
    phi = rec.get("phi_header_deid", {})   # 注意：脱敏后是这个字段名

    prelim = sections.get("初步诊断", "") or ""
    diags = [d.strip() for d in re.split(r"[；;，,]", prelim) if d.strip()]
    primary = diags[0] if diags else None
    others = diags[1:] if len(diags) > 1 else []

    chief_text = sections.get("主诉", "") or ""
    exam_text = sections.get("辅助检查", "") or ""
    physical = sections.get("查体", "") or ""
    history_text = (sections.get("既往史", "") or "") + " " + (sections.get("现病史", "") or "")

    return {
        "id": rec.get("id"),
        "basic": {
            "sex": phi.get("性别"),
            "age_band": age_band(phi.get("年龄")),
            "dept": rec.get("dept"),
        },
        "admission": {
            "admit_date": phi.get("入院日期"),
            # 原始数据没有出院日期，算不出住院天数。
            # 宁可填 None，也不要编一个 —— 编造的数据下游无法识别。
            "los_days": None,
        },
        "chief": {
            "text": chief_text or None,
            "symptoms": extract_symptoms(chief_text),
        },
        "diagnosis": {
            "primary": primary,
            "others": others,
            "icd10": rec.get("gold_icd10"),
        },
        "exam": {
            "items": split_exams(exam_text),
            "vitals": extract_vitals(physical),
        },
        "medication": {
            "drugs": extract_drugs(history_text),
        },
    }


def completeness(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """统计字段完整率：抽到的字段数 / (记录数 × 字段总数)。"""
    total = len(rows) * len(FIELDS)
    filled = 0
    per_field: Dict[str, int] = {f: 0 for f in FIELDS}
    for r in rows:
        for f in FIELDS:
            if r.get(f) is not None:
                filled += 1
                per_field[f] += 1
    return {
        "记录数": len(rows),
        "字段总数": len(FIELDS),
        "总槽位数": total,
        "已填槽位": filled,
        "字段完整率": round(filled / max(total, 1), 4),
        "各字段填充数": per_field,
        "达标线": 0.90,
        "是否达标": round(filled / max(total, 1), 4) >= 0.90,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="病历结构化（参考实现）")
    ap.add_argument("--in", dest="infile", default="out/records_deid.jsonl")
    ap.add_argument("--out", dest="outdir", default="out_lab3")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    recs = [json.loads(l) for l in open(args.infile, encoding="utf-8") if l.strip()]

    rows: List[Dict[str, Any]] = []
    for rec in recs:
        try:
            rows.append(structure_one(rec))
        except Exception as e:  # noqa: BLE001
            # 单条失败不能拖垮整批
            print(f"[WARN] {rec.get('id')} 结构化失败: {e}")
            rows.append(dict({f: None for f in FIELDS}, id=rec.get("id"), error=str(e)))

    with open(os.path.join(args.outdir, "structured.jsonl"), "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    rep = completeness(rows)
    with open(os.path.join(args.outdir, "structure_report.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=2)

    print(json.dumps(rep, ensure_ascii=False, indent=2))
    print(f"\n输出目录：{os.path.abspath(args.outdir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
