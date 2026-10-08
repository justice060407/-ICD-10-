# -*- coding: utf-8 -*-
"""
ICD-10 编码映射与评测（教学版）

业务背景（说服企业/学生的关键点）：
  ICD 编码不是学术玩具，它直接决定 DRG/DIP 医保支付分组。编码错了，
  医院要么少收钱要么被认定为欺诈骗保。这是本课程最容易被企业认可的落点。

本模块实现三级匹配策略，恰好对应课程里"从规则到模型"的演进：
  L1 精确匹配   标准术语词典
  L2 同义归一   口语/缩写 -> 标准术语（同义词表）
  L3 模糊匹配   编辑距离 + 科室先验（模拟"语义召回"）

评测指标：Top-1 准确率、Top-3 准确率、按科室分组准确率。
教学要点：让学生自己观察"模糊匹配阈值调高会怎样"——提升召回、牺牲精确。

用法：
  python src/icd_map.py --in out/records_deid.jsonl --out out/
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 1. ICD-10 术语-编码词典（教学示例，正式项目请用《ICD-10 北京临床版 v2.0》）
# ---------------------------------------------------------------------------
ICD10: Dict[str, str] = {
    "2型糖尿病伴多个并发症": "E11.7",
    "2型糖尿病伴肾病": "E11.2",
    "2型糖尿病伴周围神经病变": "E11.4",
    "2型糖尿病不伴有并发症": "E11.9",
    "2型糖尿病": "E11.9",
    "1型糖尿病": "E10.9",
    "糖尿病周围神经病变": "E11.4",
    "糖尿病肾病": "E11.2",
    "糖尿病视网膜病变": "E11.3",
    "高血压": "I10.x",
    "高血压1级": "I10.x",
    "高血压2级": "I10.x",
    "高血压3级": "I10.x",
    "社区获得性肺炎": "J18.9",
    "支气管肺炎": "J18.0",
    "肺炎": "J18.9",
    "慢性支气管炎": "J42",
    "慢性阻塞性肺疾病伴有急性加重": "J44.1",
    "慢性阻塞性肺疾病": "J44.9",
    "肺气肿": "J43.9",
    "呼吸衰竭": "J96.9",
    "慢性肺源性心脏病": "I27.9",
    "急性ST段抬高型心肌梗死": "I21.0",
    "急性心肌梗死": "I21.9",
    "心力衰竭": "I50.9",
    "慢性心力衰竭急性加重": "I50.9",
    "扩张型心肌病": "I42.0",
    "心房颤动": "I48",
    "脑梗死": "I63.9",
    "短暂性脑缺血发作": "G45.9",
    "急性阑尾炎": "K35.9",
    "局限性腹膜炎": "K65.9",
    "股骨颈骨折": "S72.0",
    "骨质疏松症": "M81.9",
    "重度骨质疏松症": "M81.9",
    "胃窦恶性肿瘤": "C16.3",
    "胃窦腺癌": "C16.3",
    "胃癌": "C16.9",
    "慢性萎缩性胃炎": "K29.4",
    "幽门螺杆菌感染": "B98.0",
    "上消化道出血": "K92.2",
    "中度贫血": "D64.9",
    "贫血": "D64.9",
    "甲状腺功能亢进症": "E05.9",
    "弥漫性毒性甲状腺肿": "E05.0",
    "肝硬化": "K74.6",
    "乙型肝炎肝硬化失代偿期": "K74.6",
    "食管胃底静脉曲张破裂出血": "I85.0",
    "食管胃底静脉曲张": "I85.9",
    "门静脉高压": "K76.6",
    "脾功能亢进": "D73.1",
    "慢性乙型病毒性肝炎": "B18.1",
    "急性肾盂肾炎": "N10",
    "泌尿系感染": "N39.0",
    "类风湿关节炎": "M06.9",
    "复发性抑郁障碍": "F33.9",
    "复发性抑郁障碍中度发作": "F33.1",
    "复发性抑郁障碍重度发作": "F33.2",
    "抑郁障碍": "F32.9",
    "失眠障碍": "F51.0",
    "老年性白内障": "H25.9",
    "白内障": "H25.9",
    "胆囊结石伴其他胆囊炎": "K80.1",
    "慢性胆囊炎急性发作": "K80.1",
    "胆囊结石": "K80.2",
    "过敏性紫癜": "D69.0",
    "紫癜性肾炎": "N08.2",
    "带状疱疹": "B02.9",
    "妊娠期高血压": "O13",
    "胎儿生长受限": "O36.5",
}

# 同义词 / 口语归一（L2）
SYNONYM: Dict[str, str] = {
    "糖尿病": "2型糖尿病",
    "DM": "2型糖尿病",
    "慢阻肺": "慢性阻塞性肺疾病",
    "慢阻肺急性加重": "慢性阻塞性肺疾病伴有急性加重",
    "COPD": "慢性阻塞性肺疾病",
    "心梗": "急性心肌梗死",
    "急性心梗": "急性心肌梗死",
    "脑梗": "脑梗死",
    "脑血栓": "脑梗死",
    "阑尾炎": "急性阑尾炎",
    "骨折": "股骨颈骨折",
    "甲亢": "甲状腺功能亢进症",
    "肝硬化失代偿期": "肝硬化",
    "乙肝肝硬化": "乙型肝炎肝硬化失代偿期",
    "泌尿道感染": "泌尿系感染",
    "尿路感染": "泌尿系感染",
    "类风湿": "类风湿关节炎",
    "抑郁症": "抑郁障碍",
    "老年性白内障": "白内障",
    "年龄相关性白内障": "老年性白内障",
    "胆结石": "胆囊结石",
    "胆囊炎": "慢性胆囊炎急性发作",
    "紫癜": "过敏性紫癜",
    "蛇缠腰": "带状疱疹",
    "妊高症": "妊娠期高血压",
    "心衰": "心力衰竭",
}

# 科室 -> ICD 章节先验（模糊匹配时用于打分加权）
DEPT_CHAPTER_PRIOR: Dict[str, str] = {
    "内分泌科": "E", "呼吸与危重症医学科": "J", "心血管内科": "I",
    "神经内科": "G", "普通外科": "K", "胃肠外科": "K", "骨科": "S",
    "消化内科": "K", "肾内科": "N", "风湿免疫科": "M", "精神心理科": "F",
    "眼科": "H", "儿科": "J", "产科": "O", "皮肤科": "L",
}

# 需要剥离的修饰成分（避免"右股骨颈骨折（Garden III型）"匹配失败）
NOISE = re.compile(r"[（(][^）)]*[）)]|，|。|；|、|\s|待排|待查")


# ---------------------------------------------------------------------------
# 2. 归一化
# ---------------------------------------------------------------------------
def normalize(name: str) -> str:
    """归一化：主要诊断优先 + 剥离修饰成分 + 同义映射

    病案编码的真实规则是"主要诊断取第一诊断"。早期版本直接删掉逗号，
    把"社区获得性肺炎，未指明病原体"粘成"社区获得性肺炎未指明病原体"，
    导致与词典术语的相似度被稀释到阈值以下 —— 这是一个典型的
    "看起来像清洗、实则是破坏"的预处理错误。
    """
    parts = [p.strip() for p in re.split(r"[，,；;、\n]", name) if p.strip()]
    if not parts:
        return ""
    s = NOISE.sub("", parts[0]).replace("（", "").replace("）", "").strip()

    # 细粒度编码补全：主诊断 + 修饰段（如"复发性抑郁障碍"+"中度发作" -> F33.1）
    # 这是真实编码员的日常工作 —— 诊断描述不完整，编码就只能落在"未特指"档
    if len(parts) > 1:
        tail = NOISE.sub("", parts[1]).replace("（", "").replace("）", "").strip()
        combined = s + tail
        if combined in ICD10:
            s = combined
    if s in SYNONYM:
        s = SYNONYM[s]
    return s


# ---------------------------------------------------------------------------
# 3. 三级匹配
# ---------------------------------------------------------------------------
def match(name: str, dept: Optional[str] = None, cutoff: float = 0.72
          ) -> Tuple[List[Tuple[str, str, float, str]], str]:
    """返回 [(术语, 编码, 分数, 命中层级)]，按分数降序"""
    cands: List[Tuple[str, str, float, str]] = []
    key = normalize(name)

    # L1 精确
    if key in ICD10:
        cands.append((key, ICD10[key], 1.0, "L1-精确"))
    # L2 同义归一 / 术语包含（"2型糖尿病伴肾病" 命中 "2型糖尿病"）
    for syn, std in SYNONYM.items():
        if syn in name and std in ICD10:
            cands.append((std, ICD10[std], 0.95, "L2-同义"))
    for term, code in ICD10.items():
        if len(term) >= 3 and term in key and term != key:
            cands.append((term, code, 0.90, "L2-包含"))

    # L3 模糊（编辑距离 + 科室先验加权）
    prior = DEPT_CHAPTER_PRIOR.get(dept or "", "")
    for term, code in ICD10.items():
        ratio = difflib.SequenceMatcher(None, key, term).ratio()
        if ratio >= cutoff:
            score = ratio
            if prior and code.startswith(prior):
                score = min(1.0, score + 0.08)
            cands.append((term, code, round(score, 4), "L3-模糊"))

    # 去重取最高分
    best: Dict[str, Tuple[str, str, float, str]] = {}
    for term, code, score, level in cands:
        if code not in best or score > best[code][2]:
            best[code] = (term, code, score, level)
    ranked = sorted(best.values(), key=lambda x: -x[2])
    return ranked, key


# ---------------------------------------------------------------------------
# 4. 评测
# ---------------------------------------------------------------------------
def run(infile: str, outdir: str, cutoff: float = 0.72) -> dict:
    os.makedirs(outdir, exist_ok=True)
    records = [json.loads(l) for l in open(infile, encoding="utf-8") if l.strip()]

    rows = []
    top1_ok = top3_ok = 0
    by_dept: Dict[str, List[int]] = {}
    level_stat: Dict[str, int] = {}

    for rec in records:
        gold = rec.get("gold_icd10", "")
        dept = rec.get("dept")
        name = rec.get("gold_diagnosis", "")
        ranked, key = match(name, dept, cutoff)

        pred1 = ranked[0][1] if ranked else "UNKNOWN"
        pred3 = [r[1] for r in ranked[:3]]
        hit1 = int(pred1 == gold)
        hit3 = int(gold in pred3)
        top1_ok += hit1
        top3_ok += hit3

        if ranked:
            level_stat[ranked[0][3]] = level_stat.get(ranked[0][3], 0) + 1
        by_dept.setdefault(dept or "未分组", []).append(hit1)

        rows.append({
            "id": rec.get("id"), "dept": dept, "原始诊断": name,
            "归一化": key, "gold": gold, "top1": pred1,
            "top3": pred3, "hit1": hit1, "hit3": hit3,
            "命中层级": ranked[0][3] if ranked else "未命中",
        })

    n = len(records)
    report = {
        "样本数": n,
        "Top-1准确率": round(top1_ok / max(n, 1), 4),
        "Top-3准确率": round(top3_ok / max(n, 1), 4),
        "命中层级分布": level_stat,
        "模糊匹配阈值": cutoff,
        "按科室Top-1": {d: f"{sum(v)}/{len(v)}" for d, v in by_dept.items()},
    }
    with open(os.path.join(outdir, "icd_predict.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(outdir, "icd_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="ICD-10 编码映射与评测")
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--out", dest="outdir", default="out")
    ap.add_argument("--cutoff", type=float, default=0.72, help="模糊匹配阈值")
    args = ap.parse_args()
    print(json.dumps(run(args.infile, args.outdir, args.cutoff), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
