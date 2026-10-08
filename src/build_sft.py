# -*- coding: utf-8 -*-
"""
构建 LoRA 监督微调数据集（教学版）

把脱敏后的病历转成三类指令数据，正好覆盖课程核心链路的三个能力点：
  T1 结构化抽取   病历自由文本 -> 规范 JSON
  T2 ICD 编码     诊断术语     -> ICD-10 编码
  T3 患者教育问答 患者提问     -> 通俗、带免责声明的回答

教学要点：
  - 数据质量 > 数据规模。20 条病历扩出的几百条样本，只要格式一致、
    答案可核对，就足以让学生看到"领域适配"带来的行为变化。
  - T3 的回答必须内置"就医提示"，这是医疗 AI 产品的合规底线，
    也是评分量规里的硬性扣分项。

输出：
  sft_train.jsonl / sft_val.jsonl  （instruction / input / output 三段式）
  qa_eval.jsonl                    （人工评测用的固定问答集）

用法：
  python src/build_sft.py --in out/records_deid.jsonl \
      --entities out/ner_entities.jsonl --out out/ --val-ratio 0.2
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Dict, List

import icd_map

DISCLAIMER = "\n【重要提示】以上内容仅为健康知识科普，不能替代医师面诊。请遵医嘱就诊，如有急症请立即就医。"

STRUCT_INSTRUCTION = (
    "你是一名病历结构化助手。请从下面的脱敏病历中抽取指定字段，"
    "严格输出 JSON，不要输出任何解释性文字。缺失字段用空字符串占位。"
)

ICD_INSTRUCTION = (
    "你是一名病案编码员。请根据给出的临床诊断，给出最匹配的 ICD-10 编码，"
    "只输出编码本身，不要输出解释。"
)

QA_INSTRUCTION = (
    "你是一名患者教育助手。请基于提供的病历摘要，用通俗语言回答患者的问题，"
    "不使用未经病历支持的推测性诊断，并在结尾给出就医提示。"
)


def build_structure_task(rec: dict) -> dict:
    sec = rec.get("sections", {})
    target = {
        "主诉": sec.get("主诉", ""),
        "现病史": sec.get("现病史", ""),
        "既往史": sec.get("既往史", ""),
        "查体": sec.get("查体", ""),
        "辅助检查": sec.get("辅助检查", ""),
        "初步诊断": sec.get("初步诊断", ""),
    }
    return {
        "task": "T1-结构化抽取",
        "instruction": STRUCT_INSTRUCTION,
        "input": rec.get("clean_text", ""),
        "output": json.dumps(target, ensure_ascii=False),
    }


def build_icd_task(rec: dict) -> dict:
    return {
        "task": "T2-ICD编码",
        "instruction": ICD_INSTRUCTION,
        "input": f"科室：{rec.get('dept','')}\n诊断：{rec.get('gold_diagnosis','')}",
        "output": rec.get("gold_icd10", ""),
    }


def build_qa_task(rec: dict, ent: dict | None) -> dict:
    diagnosis = rec.get("gold_diagnosis", "")
    dept = rec.get("dept", "")
    sec = rec.get("sections", {})
    entities = [e["text"] for e in (ent or {}).get("entities", []) if e["type"] == "SYM"][:5]
    symptom = "、".join(entities) if entities else sec.get("主诉", "")

    question = f"医生说我得了“{diagnosis}”，这个病严不严重？我平时要注意什么？"
    answer = (
        f"根据您本次在{sec.get('主诉','') and dept or dept}的就诊记录，"
        f"病历中记载的主要问题是：{diagnosis}。"
        f"记录中提到的相关表现包括：{symptom}。"
        f"从病历看，医师已为您安排了相应检查（{sec.get('辅助检查','')[:60]}…）。"
        f"具体严重程度需要结合检查结果由主管医师判断，"
        f"建议您按时复诊、规律用药，出现病情变化时及时返院。"
        + DISCLAIMER
    )
    return {
        "task": "T3-患者教育问答",
        "instruction": QA_INSTRUCTION,
        "input": f"病历摘要（已脱敏）：\n主诉：{sec.get('主诉','')}\n"
                 f"初步诊断：{sec.get('初步诊断','')}\n患者提问：{question}",
        "output": answer,
    }


def run(infile: str, entfile: str, outdir: str, val_ratio: float, seed: int = 42) -> dict:
    os.makedirs(outdir, exist_ok=True)
    records = [json.loads(l) for l in open(infile, encoding="utf-8") if l.strip()]

    ent_map: Dict[str, dict] = {}
    if entfile and os.path.exists(entfile):
        for l in open(entfile, encoding="utf-8"):
            if l.strip():
                r = json.loads(l)
                ent_map[r.get("id", "")] = r

    samples: List[dict] = []
    qa_eval: List[dict] = []
    for rec in records:
        samples.append(build_structure_task(rec))
        samples.append(build_icd_task(rec))
        qa = build_qa_task(rec, ent_map.get(rec.get("id", "")))
        samples.append(qa)
        qa_eval.append({
            "id": rec.get("id"),
            "question": qa["input"].split("患者提问：")[-1],
            "reference_answer": qa["output"],
            "评分维度": ["医学事实准确性", "有无越界诊断", "通俗性", "是否含就医提示"],
        })

    random.Random(seed).shuffle(samples)
    n_val = max(1, int(len(samples) * val_ratio))
    val, train = samples[:n_val], samples[n_val:]

    for name, rows in (("sft_train.jsonl", train), ("sft_val.jsonl", val)):
        with open(os.path.join(outdir, name), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({
                    "instruction": r["instruction"],
                    "input": r["input"],
                    "output": r["output"],
                    "task": r["task"],
                }, ensure_ascii=False) + "\n")

    with open(os.path.join(outdir, "qa_eval.jsonl"), "w", encoding="utf-8") as f:
        for r in qa_eval:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    stat: Dict[str, int] = {}
    for r in samples:
        stat[r["task"]] = stat.get(r["task"], 0) + 1

    report = {
        "源病历数": len(records),
        "样本总数": len(samples),
        "训练集": len(train),
        "验证集": len(val),
        "任务分布": stat,
        "评测问答数": len(qa_eval),
    }
    with open(os.path.join(outdir, "sft_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 LoRA 微调数据集")
    ap.add_argument("--in", dest="infile", default="out/records_deid.jsonl")
    ap.add_argument("--entities", dest="entfile", default="out/ner_entities.jsonl")
    ap.add_argument("--out", dest="outdir", default="out")
    ap.add_argument("--val-ratio", type=float, default=0.2)
    args = ap.parse_args()
    print(json.dumps(run(args.infile, args.entfile, args.outdir, args.val_ratio),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
