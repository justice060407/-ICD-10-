# -*- coding: utf-8 -*-
"""
一键跑通全链路：合成病历 -> 脱敏 -> NER -> ICD -> 构建微调数据

用法：
  python src/run_pipeline.py --key "本课程密钥"
  python src/run_pipeline.py --key "本课程密钥" --skip-lora-data

教学用法：让学生第一次上课只跑这一条命令，先看到"端到端有输出"，
再逐环节拆开讲原理。先建立整体感，再深入细节。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deid_pipeline  # noqa: E402
import icd_map  # noqa: E402
import ner_baseline  # noqa: E402
import build_sft  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="临床大模型课程全链路一键运行")
    ap.add_argument("--data", default="data/synthetic_records_20.jsonl")
    ap.add_argument("--out", default="out")
    ap.add_argument("--key", default="clinical-llm-course-2026")
    ap.add_argument("--icd-cutoff", type=float, default=0.72)
    args = ap.parse_args()

    t0 = time.time()
    steps = []

    print("=" * 60)
    print("步骤 1/4  合成病历脱敏去标识")
    print("=" * 60)
    r1 = deid_pipeline.run(args.data, args.out, args.key)
    print(json.dumps(r1, ensure_ascii=False, indent=2))
    steps.append(("脱敏", r1["脱敏替换项总数"], "处替换", r1["残留风险记录数"] == 0))

    print("=" * 60)
    print("步骤 2/4  临床实体识别（词典基线）")
    print("=" * 60)
    r2 = ner_baseline.run(os.path.join(args.out, "records_deid.jsonl"), args.out)
    print(json.dumps(r2, ensure_ascii=False, indent=2))
    steps.append(("实体识别", r2["实体总数"], "个实体", True))

    print("=" * 60)
    print("步骤 3/4  ICD-10 编码映射与评测")
    print("=" * 60)
    r3 = icd_map.run(os.path.join(args.out, "records_deid.jsonl"), args.out, args.icd_cutoff)
    print(json.dumps(r3, ensure_ascii=False, indent=2))
    steps.append(("ICD编码", r3["Top-1准确率"], "Top-1准确率", r3["Top-1准确率"] > 0))

    print("=" * 60)
    print("步骤 4/4  构建 LoRA 微调数据集")
    print("=" * 60)
    r4 = build_sft.run(os.path.join(args.out, "records_deid.jsonl"),
                       os.path.join(args.out, "ner_entities.jsonl"), args.out, 0.2)
    print(json.dumps(r4, ensure_ascii=False, indent=2))
    steps.append(("微调数据", r4["样本总数"], "条样本", True))

    print("=" * 60)
    print(f"全链路完成，耗时 {time.time() - t0:.1f} 秒")
    print("-" * 60)
    for name, val, unit, ok in steps:
        flag = "通过" if ok else "需检查"
        print(f"  {name:<8} {val:<10} {unit:<12} [{flag}]")
    print("-" * 60)
    print(f"产出目录：{os.path.abspath(args.out)}")
    print("下一步：python src/train_lora.py --data out/sft_train.jsonl")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
