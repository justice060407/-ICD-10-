# -*- coding: utf-8 -*-
"""
LoRA 领域适配微调（教学版，带降级模式）

算力分级方案（民办本科最常见的现实约束，务必在课上讲清楚）：
  A档 有 24GB 显卡（3090/4090）：
     Qwen2.5-1.5B-Instruct + bf16 + LoRA r=16，单卡 20 分钟跑完
  B档 只有 8GB 显卡：
     Qwen2.5-0.5B-Instruct + 4bit 量化 + LoRA r=8，gradient-accumulation 调大
  C档 纯 CPU（实验室无显卡）：
     不训练，改用"少样本提示 + 规则后处理"完成同样的任务，
     本脚本会输出等价的 Prompt 基线供对比 —— 让学生理解"微调不是唯一解"

依赖：torch / transformers / peft / datasets（已写入 requirements.txt，
      镜像中标注为可选 extras；C档机器可以完全不装）

用法：
  python src/train_lora.py --data out/sft_train.jsonl --epochs 3 --gpu auto
  python src/train_lora.py --data out/sft_train.jsonl --cpu-fallback
"""

from __future__ import annotations

import argparse
import json
import os
import sys

BASE_MODEL_DEFAULT = "Qwen/Qwen2.5-0.5B-Instruct"

PROMPT = "Below is an instruction that describes a task.\n\n### Instruction:\n{instruction}\n\n### Input:\n{input}\n\n### Response:\n"


def build_text(sample: dict) -> str:
    return PROMPT.format(
        instruction=sample.get("instruction", ""),
        input=sample.get("input", ""),
    ) + sample.get("output", "")


def has_gpu() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def run_lora(data_path: str, model_name: str, epochs: int, rank: int,
             lr: float, out_dir: str, four_bit: bool) -> dict:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer
    from peft import LoraConfig, get_peft_model
    from datasets import Dataset

    rows = [json.loads(l) for l in open(data_path, encoding="utf-8") if l.strip()]
    ds = Dataset.from_list([{"text": build_text(r)} for r in rows])

    tok = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    def tokenize(batch):
        out = tok(batch["text"], truncation=True, max_length=1024, padding="max_length")
        out["labels"] = out["input_ids"].copy()
        return out

    ds = ds.map(tokenize, batched=True, remove_columns=["text"])

    load_kwargs = {"trust_remote_code": True}
    if four_bit:
        load_kwargs.update({"load_in_4bit": True, "device_map": "auto"})
        model = AutoModelForCausalLM.from_pretrained(model_name, **load_kwargs)
    else:
        model = AutoModelForCausalLM.from_pretrained(
            model_name, torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            **load_kwargs)

    config = LoraConfig(
        r=rank, lora_alpha=rank * 2, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    model = get_peft_model(model, config)
    model.print_trainable_parameters()

    args = TrainingArguments(
        output_dir=out_dir,
        num_train_epochs=epochs,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=lr,
        logging_steps=5,
        save_strategy="epoch",
        fp16=not four_bit and torch.cuda.is_available(),
        report_to=[],
    )
    trainer = Trainer(model=model, args=args, train_dataset=ds)
    trainer.train()
    model.save_pretrained(out_dir)
    tok.save_pretrained(out_dir)
    return {"模式": "LoRA训练完成", "输出目录": out_dir, "样本数": len(rows),
            "可训练参数占比": "见上方 print_trainable_parameters 输出"}


def run_cpu_fallback(data_path: str, out_dir: str) -> dict:
    """无显卡时的等价方案：生成少样本提示基线，供学生做 A/B 对比"""
    os.makedirs(out_dir, exist_ok=True)
    rows = [json.loads(l) for l in open(data_path, encoding="utf-8") if l.strip()]
    by_task: dict = {}
    for r in rows:
        by_task.setdefault(r.get("task", "其他"), []).append(r)

    few_shot = {}
    for task, items in by_task.items():
        shots = items[:3]
        few_shot[task] = [
            {"instruction": s["instruction"], "input": s["input"][:200], "output": s["output"][:300]}
            for s in shots
        ]

    path = os.path.join(out_dir, "fewshot_prompt_baseline.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(few_shot, f, ensure_ascii=False, indent=2)

    return {
        "模式": "CPU降级：少样本提示基线",
        "说明": "无显卡环境下不训练权重，改用 3-shot 提示 + 规则后处理完成任务，"
                "学生需对比其与 LoRA 微调在格式稳定性上的差异",
        "输出文件": path,
        "样本数": len(rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="LoRA 领域适配微调")
    ap.add_argument("--data", default="out/sft_train.jsonl")
    ap.add_argument("--model", default=BASE_MODEL_DEFAULT)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--rank", type=int, default=8)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--out", dest="out_dir", default="out/lora")
    ap.add_argument("--4bit", dest="four_bit", action="store_true", help="8GB 显卡使用")
    ap.add_argument("--cpu-fallback", dest="cpu_fallback", action="store_true")
    args = ap.parse_args()

    if args.cpu_fallback or not has_gpu():
        if not args.cpu_fallback:
            print("[提示] 未检测到 CUDA，自动切换到 CPU 降级模式")
        result = run_cpu_fallback(args.data, args.out_dir)
    else:
        result = run_lora(args.data, args.model, args.epochs, args.rank,
                          args.lr, args.out_dir, args.four_bit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
