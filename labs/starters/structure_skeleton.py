# -*- coding: utf-8 -*-
"""实验 3 · 病历结构化 —— 学生完成版骨架

用法：
    python src/structure.py --in out/records_deid.jsonl --out out_lab3

任务：把自由文本病历转成规范 JSON，并统计字段完整率。

本文件有 4 个 TODO，逐个填完即可运行。
**不要为了跑通而简化 Schema** —— 本实验评分主要看 Schema 设计。
"""

import json
import os
import re
import argparse
from typing import Any, Dict, List, Optional

# ── 需要抽取的字段清单 ────────────────────────────────────────────────
# TODO 1：按你在实验 3 步骤 2 画的那棵树，定义你的目标字段。
#          要求至少 6 个一级字段：basic / admission / chief / diagnosis
#          / exam / medication
#          缺失字段一律填 None（不要不写这个 key，也不要写空字符串）
FIELDS: List[str] = [
    "basic",
    "admission",
    "chief",
    "diagnosis",
    "exam",
    "medication",
    # 你觉得还该有第 7 个吗？加上，并在报告里说明理由
]


def to_float(s: str) -> Optional[float]:
    """安全转 float。遇到 '未查'、'—'、'阴性' 等一律返回 None。"""
    # TODO 2：实现它。注意至少要挡住这几类：空串、破折号、中文、带%号的
    raise NotImplementedError("TODO 2: 实现 to_float")


def split_exams(text: str) -> List[Dict[str, Any]]:
    """把辅助检查段落拆成结构化检验项。

    目标形态（这是参考答案，你可以有自己的设计，但要说明理由）：
        {"name": "空腹血糖", "value": 11.8, "unit": "mmol/L"}

    提示：
      - 按中文逗号 / 句号切分
      - 用正则抓数值 + 单位：r"([\\d.]+)\\s*(mmol/L|mg/g|%|mmHg|g/L|umol/L)"
      - 数值用 to_float 兜底，**不要直接 float()**
    """
    # TODO 3：实现它
    raise NotImplementedError("TODO 3: 实现 split_exams")


def structure_one(rec: Dict[str, Any]) -> Dict[str, Any]:
    """把一条脱敏后的病历转成结构化 JSON。"""
    sections = rec.get("sections", {})
    # 注意：脱敏后的字段叫 phi_header_deid，不是 phi_header。
    # 写错名字不会报错，只会静默拿到 {} —— 这类"静默失败"比崩溃更难查。
    phi = rec.get("phi_header_deid", {})

    # 主要诊断 = 初步诊断的第一段（按分号/逗号切）
    prelim = sections.get("初步诊断", "")
    diags = [d.strip() for d in re.split(r"[；;，,]", prelim) if d.strip()]
    primary = diags[0] if diags else None
    others = diags[1:] if len(diags) > 1 else []

    # TODO 4：把剩下的字段补完。至少要有：
    #   age_band  —— 年龄段（不是具体年龄！实验1的准标识符知识用上了）
    #   symptoms  —— 复用实验2抽出的 SYM 实体？还是重新抽？你决定
    #   vitals    —— T/P/R/BP 从查体段里正则抓
    #   exams     —— 调用 split_exams
    #   medications —— 从既往史/现病史里找药物（想想为什么实验2的 DRU 只有9个）
    # 注意：每个可能抽不到的字段都要有 None 兜底，不可以抛异常终止
    raise NotImplementedError("TODO 4: 完成 structure_one")


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
    ap = argparse.ArgumentParser(description="病历结构化")
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
            # 单条失败不能拖垮整批：记下来，报告里要交代
            print(f"[WARN] {rec.get('id')} 结构化失败: {e}")
            rows.append({f: None for f in FIELDS} | {"id": rec.get("id"), "error": str(e)})

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
