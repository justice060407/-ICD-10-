# -*- coding: utf-8 -*-
"""
临床实体识别基线（词典最大匹配法，教学版）

为什么先讲"规则基线"而不是直接上 BERT？
  1. 让学生先建立一个可解释、可 debug 的下限（baseline），再理解模型提升了多少
  2. 医疗场景里规则基线仍有工程价值：它是"兜底"和"造预训练标注"的手段
  3. 真实项目中，规则基线的输出常被用作预标注，人工只做校正

实体类型（对应后续结构化 Schema）：
  DIS 疾病/诊断   SYM 症状/体征   DRU 药物   EXA 检查检验   BOD 身体部位   LAB 检验值

输出：
  - BIO 序列（可导入 Label Studio / doccano 做人工校正）
  - 结构化候选（供 ICD 编码与结构化环节消费）

用法：
  python src/ner_baseline.py --in out/records_deid.jsonl --out out/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# 1. 术语词典（真实项目中应替换为权威词表：ICD-10 中文版、ATC、LOINC 等）
# ---------------------------------------------------------------------------
DICT: Dict[str, List[str]] = {
    "DIS": [
        "2型糖尿病", "1型糖尿病", "糖尿病", "糖尿病周围神经病变", "糖尿病肾病",
        "糖尿病视网膜病变", "高血压", "社区获得性肺炎", "支气管肺炎", "肺炎",
        "慢性支气管炎", "慢性阻塞性肺疾病", "肺气肿", "呼吸衰竭", "肺源性心脏病",
        "急性心肌梗死", "心肌梗死", "心力衰竭", "扩张型心肌病", "心房颤动",
        "脑梗死", "短暂性脑缺血发作", "急性阑尾炎", "腹膜炎", "股骨颈骨折",
        "骨质疏松症", "胃窦腺癌", "胃癌", "慢性萎缩性胃炎", "幽门螺杆菌感染",
        "甲状腺功能亢进症", "甲状腺毒症", "肝硬化", "食管胃底静脉曲张",
        "门静脉高压", "脾功能亢进", "慢性乙型病毒性肝炎", "急性肾盂肾炎",
        "泌尿系感染", "类风湿关节炎", "复发性抑郁障碍", "抑郁障碍", "失眠障碍",
        "白内障", "胆囊结石", "慢性胆囊炎", "过敏性紫癜", "紫癜性肾炎",
        "带状疱疹", "贫血", "上消化道出血", "胎儿生长受限",
    ],
    "SYM": [
        "多饮", "多尿", "体重下降", "消瘦", "麻木", "发热", "寒战", "咳嗽", "咳痰",
        "气促", "呼吸困难", "胸痛", "胸闷", "大汗", "恶心", "呕吐", "腹痛", "头痛",
        "头晕", "乏力", "水肿", "腹胀", "呕血", "黑便", "血尿", "尿频", "尿急",
        "尿痛", "腰痛", "心悸", "怕热", "多汗", "手抖", "晨僵", "关节肿痛",
        "视力下降", "皮疹", "疼痛", "黄疸", "意识障碍", "抽搐", "食欲减退",
        "情绪低落", "兴趣减退", "睡眠障碍", "早醒", "关节畸形", "便血",
    ],
    "DRU": [
        "二甲双胍", "氨氯地平", "硝酸甘油", "头孢", "布地奈德福莫特罗",
        "沙库巴曲缬沙坦", "螺内酯", "美托洛尔", "阿司匹林", "阿托伐他汀",
        "胰岛素", "格列美脲", "泮托拉唑", "左氧氟沙星", "莫西沙星",
        "低分子肝素", "氯吡格雷", "呋塞米", "甲钴胺", "阿卡波糖",
    ],
    "EXA": [
        "血常规", "尿常规", "肝功能", "肾功能", "胸部CT", "头颅CT", "头颅MRI",
        "腹部CT", "腹部超声", "胃镜", "心电图", "超声心动图", "肌电图",
        "眼底照相", "肺功能", "骨密度", "胸部X线", "X线", "B超", "CT", "MRI",
        "冠脉造影", "活检", "病理", "血气分析", "胎心监护", "裂隙灯检查",
        "关节超声", "呼吸道病原体核酸检测", "动态心电图", "眼压",
    ],
    "BOD": [
        "双肺", "右下肺", "左下肺", "胸骨后", "右下腹", "上腹部", "右上腹",
        "脐周", "右髋部", "股骨颈", "胃窦", "甲状腺", "胆囊", "肝脏", "脾脏",
        "双肾", "肾区", "双手", "双腕", "双膝", "双眼", "晶状体", "食管",
        "胃底", "双下肢", "右侧胸背部", "颈静脉", "心脏", "冠状动脉",
        "前降支", "大脑中动脉", "基底节区", "膀胱", "输尿管", "前列腺",
    ],
}

# 检验值：数值 + 单位（结构化时用于填充 lab 字段）
LAB_PATTERN = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9\-]{2,12})\s*[:：]?\s*(\d+(?:\.\d+)?)\s*"
    r"(mmol/L|μmol/L|mg/L|ng/ml|g/L|pg/ml|×10\^9/L|10\^9/L|%|mmHg|次/分|U/L|fl|mIU/L|pmol/L|IU/ml|mm/h|mm|cm|kg|ml)"
)

SECTION_ORDER = ["主诉", "现病史", "既往史", "查体", "辅助检查", "初步诊断"]


# ---------------------------------------------------------------------------
# 2. 最大正向匹配
# ---------------------------------------------------------------------------
class MaxMatcher:
    def __init__(self, dictionary: Dict[str, List[str]]):
        # 按长度倒序，保证长词优先（"糖尿病肾病" 优先于 "糖尿病"）
        self.terms: List[Tuple[str, str]] = []
        for tag, words in dictionary.items():
            for w in words:
                self.terms.append((w, tag))
        self.terms.sort(key=lambda x: -len(x[0]))
        self.max_len = max(len(w) for w, _ in self.terms)

    def match(self, text: str) -> List[Tuple[int, int, str, str]]:
        spans: List[Tuple[int, int, str, str]] = []
        i = 0
        n = len(text)
        while i < n:
            hit = None
            for size in range(min(self.max_len, n - i), 0, -1):
                cand = text[i:i + size]
                for w, tag in self.terms:
                    if len(w) == size and w == cand:
                        hit = (i, i + size, w, tag)
                        break
                if hit:
                    break
            if hit:
                s, e, w, tag = hit
                # 丢弃被已命中区间覆盖的短匹配
                if not any(a <= s and b >= e for a, b, _, _ in spans):
                    spans.append((s, e, w, tag))
                    i = e
                else:
                    i += 1
            else:
                i += 1
        return spans


# ---------------------------------------------------------------------------
# 3. BIO 导出（用于人工校正平台）
# ---------------------------------------------------------------------------
def to_bio(text: str, spans: List[Tuple[int, int, str, str]]) -> List[Tuple[str, str]]:
    labels = ["O"] * len(text)
    for s, e, _w, tag in spans:
        for k in range(s, e):
            if k < len(labels):
                labels[k] = "B-" + tag if k == s else "I-" + tag
    return [(ch, labels[i]) for i, ch in enumerate(text)]


# ---------------------------------------------------------------------------
# 4. 主流程
# ---------------------------------------------------------------------------
def run(infile: str, outdir: str) -> dict:
    os.makedirs(outdir, exist_ok=True)
    matcher = MaxMatcher(DICT)

    records = [json.loads(l) for l in open(infile, encoding="utf-8") if l.strip()]

    bio_rows = []
    entity_rows = []
    stat: Dict[str, int] = {}
    total_chars = 0

    for rec in records:
        text = rec.get("clean_text", "")
        total_chars += len(text)
        spans = matcher.match(text)
        for _s, _e, _w, tag in spans:
            stat[tag] = stat.get(tag, 0) + 1
        entity_rows.append({
            "id": rec.get("id"),
            "dept": rec.get("dept"),
            "entities": [
                {"start": s, "end": e, "text": w, "type": tag} for s, e, w, tag in spans
            ],
            "labs": [
                {"item": m.group(1), "value": m.group(2), "unit": m.group(3)}
                for m in LAB_PATTERN.finditer(text)
            ],
        })
        bio_rows.append({"id": rec.get("id"), "bio": to_bio(text, spans)})

    with open(os.path.join(outdir, "ner_entities.jsonl"), "w", encoding="utf-8") as f:
        for r in entity_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(outdir, "ner_bio.jsonl"), "w", encoding="utf-8") as f:
        for r in bio_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    report = {
        "病历数": len(records),
        "总字符数": total_chars,
        "实体总数": sum(stat.values()),
        "按类型统计": dict(sorted(stat.items(), key=lambda x: -x[1])),
        "平均每份病历实体数": round(sum(stat.values()) / max(len(records), 1), 1),
        "说明": "词典基线仅作下限参考；真实项目需人工校正后训练序列标注模型",
    }
    with open(os.path.join(outdir, "ner_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="临床实体识别基线（词典最大匹配）")
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--out", dest="outdir", default="out")
    args = ap.parse_args()
    print(json.dumps(run(args.infile, args.outdir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
