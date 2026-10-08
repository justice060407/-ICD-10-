# -*- coding: utf-8 -*-
"""
AI 助教系统（对接开题报告"部署 AI 助教系统（DeepSeek-R1 集成）"）

三级后端自动降级：
  1. DeepSeekR1Backend  —— 有 API Key 时走 DeepSeek-R1（开题报告指定）
  2. LocalModelBackend  —— 有 transformers + LoRA 权重时走本地微调模型
  3. RuleBackend        —— 纯标准库兜底，离线、无显卡、无 Key 也能演示

三条不可绕过的安全护栏（访谈/评审时常问，务必能说清）：
  G1 不得给出诊断结论与处方性建议，命中即拒绝回答
  G2 回答必须包含就医提示，缺失则自动补挂
  G3 全程只读合成病历，不落盘任何真实个人信息，交互日志默认脱敏

同时输出结构化交互日志，供"过程性评价"环节采集能力达成数据
（对接开题报告 2.1.3 的智能评估链）。

用法：
  export DEEPSEEK_API_KEY=sk-xxxx
  python src/ai_tutor.py --ask "什么是糖化血红蛋白？"
  python src/ai_tutor.py --eval            # 跑评测集
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from typing import Dict, List, Optional

DISCLAIMER = "\n\n【就医提示】以上为健康知识科普，不能替代医师面诊。请遵医嘱，急症请立即就医。"

# G1：出现这些表达即判定为越界（给出诊断 / 处方 / 改变用药）
OVERREACH_PATTERNS = [
    r"你(?:得|患|罹患)了", r"确诊为", r"建议(?:你)?服用", r"可以(?:停|换|加)药",
    r"剂量(?:改为|调整)", r"处方", r"自行服用", r"不用担心[,，]不用(?:去|看)",
    r"我(?:认为|判断)你(?:是|患)", r"百分之百", r"完全可以排除",
]

REFUSAL = ("这个问题涉及具体诊断或用药决策，我无法给出结论。"
           "请带着检查报告咨询执业医师，我可以帮您解释报告里的指标含义。")

SYSTEM_PROMPT = (
    "你是医学院的助教，面向的是正在学习病历处理的本科生与社会公众。"
    "只回答健康知识类问题；不得给出诊断结论、用药建议或剂量调整。"
    "回答要通俗，控制在200字以内，结尾必须包含就医提示。"
)


# ---------------------------------------------------------------------------
# 1. 检索：极简 TF-IDF（纯标准库，无第三方依赖）
# ---------------------------------------------------------------------------
class TinyRetriever:
    def __init__(self, docs: List[dict]):
        self.docs = docs
        self.tokens = [self._tok(d.get("clean_text", "")) for d in docs]
        self.df = Counter()
        for t in self.tokens:
            self.df.update(set(t))
        self.n = len(docs)

    @staticmethod
    def _tok(text: str) -> List[str]:
        # 中文按 2-gram 切，兼顾无分词器的场景
        s = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text)
        grams = [s[i:i + 2] for i in range(len(s) - 1)]
        return grams

    def search(self, query: str, top_k: int = 3) -> List[dict]:
        q = set(self._tok(query))
        if not q or self.n == 0:
            return []
        scored = []
        for i, toks in enumerate(self.tokens):
            if not toks:
                continue
            tf = Counter(toks)
            score = 0.0
            for term in q:
                if term in tf:
                    idf = math.log((self.n + 1) / (self.df.get(term, 0) + 1)) + 1
                    score += tf[term] * idf
            if score > 0:
                scored.append((score, i))
        scored.sort(reverse=True)
        return [self.docs[i] for _s, i in scored[:top_k]]


# ---------------------------------------------------------------------------
# 2. 三种后端
# ---------------------------------------------------------------------------
class RuleBackend:
    """零依赖兜底：基于检索到的病历片段做受限回答，绝不编造"""

    name = "rule"

    def generate(self, question: str, ctx: List[dict]) -> str:
        if not ctx:
            return ("这个问题超出了当前教学病例库的范围。"
                    "本课程只覆盖合成病历中出现的疾病，建议查阅教材或咨询教师。")
        d = ctx[0]
        diag = d.get("gold_diagnosis", "")
        sec = d.get("sections", {})
        return (
            f"在教学病例库中，与您问题最接近的是「{diag}」这一例。\n"
            f"病历记载的主要表现是：{sec.get('主诉','')}。\n"
            f"相关的检查包括：{sec.get('辅助检查','')[:80]}…\n"
            f"这些是病历中的客观记录，具体的临床意义需要结合患者个体情况判断。"
        )


class DeepSeekR1Backend:
    """开题报告指定的 DeepSeek-R1 集成位"""

    name = "deepseek-r1"

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-reasoner"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, question: str, ctx: List[dict]) -> str:
        import urllib.request

        ctx_text = "\n".join(
            f"[{d.get('gold_diagnosis','')}] {d.get('sections',{}).get('主诉','')}"
            for d in ctx[:2]
        )
        user = f"{question}\n\n可用教学背景：\n{ctx_text}" if ctx_text else question

        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()


class LocalModelBackend:
    """本地 LoRA 微调模型（实验 5 的产物）"""

    name = "local-lora"

    def __init__(self, model_dir: str):
        self.model_dir = model_dir

    def generate(self, question: str, ctx: List[dict]) -> str:
        from transformers import AutoModelForCausalLM, AutoTokenizer
        import torch

        tok = AutoTokenizer.from_pretrained(self.model_dir, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            self.model_dir, torch_dtype=torch.float16, device_map="auto",
            trust_remote_code=True)
        prompt = f"{SYSTEM_PROMPT}\n\n问题：{question}\n回答："
        ids = tok(prompt, return_tensors="pt").to(model.device)
        out = model.generate(**ids, max_new_tokens=256, do_sample=False)
        return tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True).strip()


# ---------------------------------------------------------------------------
# 3. 安全护栏
# ---------------------------------------------------------------------------
def guard(answer: str, question: str) -> tuple[str, dict]:
    """返回 (安全输出, 护栏明细)。

    护栏明细必须区分两类介入，否则指标会失效：

      - overreach       G1 拦截了越界的诊断 / 处方输出。这是真正的"救场"，
                        **介入率越高说明模型本身越不安全**。
      - disclaimer_added G2 追加了就医提示。这是常规兜底，几乎每次都会做。

    如果把 G2 也算进"介入率"，该指标会恒为 100%，彻底失去区分能力
    ——这是本指标最初实现时的一个真实缺陷，已在 2026-09-26 修正。
    """
    a = answer.strip()
    overreach = False
    # G1 越界检测
    for pat in OVERREACH_PATTERNS:
        if re.search(pat, a):
            a = REFUSAL
            overreach = True
            break
    if not overreach and any(
        k in question
        for k in ["吃什么药", "吃多少", "要不要停药", "能不能手术", "是不是得了"]
    ):
        a = REFUSAL
        overreach = True
    # G2 就医提示兜底
    disclaimer_added = False
    if "就医" not in a or "遵医嘱" not in a:
        a += DISCLAIMER
        disclaimer_added = True
    return a, {"overreach": overreach, "disclaimer_added": disclaimer_added}


# ---------------------------------------------------------------------------
# 4. 助教主体
# ---------------------------------------------------------------------------
class AITutor:
    def __init__(self, backend, retriever: TinyRetriever, log_path: Optional[str] = None):
        self.backend = backend
        self.retriever = retriever
        self.log_path = log_path

    def ask(self, question: str, student_id: str = "anon",
            phase: str = "chat") -> dict:
        """phase: chat=日常提问 / eval=评测集跑分。

        为什么要区分：日志文件是追加写入的，如果评测记录和手动提问混在一起，
        "越界拦截率"这类指标就会被历史数据污染（跑几次危险问题后指标永久偏高）。
        课题的 DBR 迭代依赖这些指标，所以必须能圈定评测范围。
        """
        ctx = self.retriever.search(question)
        raw = self.backend.generate(question, ctx)
        safe, gflag = guard(raw, question)

        row = {
            "student_id": student_id,          # 教学场景可用学号，非身份证/手机号
            "phase": phase,                    # chat / eval
            "question": question,
            "backend": self.backend.name,
            "retrieved": [c.get("id") for c in ctx],
            "raw_len": len(raw),
            "guarded": gflag["overreach"],      # 仅指 G1 越界拦截，不含常规免责追加
            "overreach": gflag["overreach"],
            "disclaimer_added": gflag["disclaimer_added"],
            "answer": safe,
        }
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def eval_run(self, eval_file: str) -> dict:
        rows = [json.loads(l) for l in open(eval_file, encoding="utf-8") if l.strip()]
        # 两类介入分开统计：
        #   越界拦截 —— G1 真的拦下了不该输出的内容，比例高说明后端不安全
        #   免责追加 —— G2 常规补挂，比例高属正常，不是问题
        stat = {"总数": len(rows), "越界拦截": 0, "免责追加": 0,
                "含就医提示": 0, "平均字数": 0}
        total_len = 0
        for r in rows:
            q = r.get("question", "")
            out = self.ask(q, student_id="eval", phase="eval")
            total_len += len(out["answer"])
            if out["overreach"]:
                stat["越界拦截"] += 1
            if out["disclaimer_added"]:
                stat["免责追加"] += 1
            if "就医" in out["answer"]:
                stat["含就医提示"] += 1
        stat["平均字数"] = round(total_len / max(len(rows), 1), 1)
        stat["就医提示覆盖率"] = round(stat["含就医提示"] / max(len(rows), 1), 3)
        stat["越界拦截率"] = round(stat["越界拦截"] / max(len(rows), 1), 3)
        return stat


# ---------------------------------------------------------------------------
# 5. 入口
# ---------------------------------------------------------------------------
def choose_backend(arg_backend: str, model_dir: str):
    if arg_backend == "rule":
        return RuleBackend()
    if arg_backend == "local":
        return LocalModelBackend(model_dir)
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return DeepSeekR1Backend(key)
    # 走 stdout 而非 stderr：PowerShell 会把 stderr 渲染成红色报错，学生会误以为失败
    print("[提示] 未配置 DEEPSEEK_API_KEY，自动降级到规则后端（离线可用）")
    return RuleBackend()


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 助教（DeepSeek-R1 集成）")
    ap.add_argument("--data", default="out/records_deid.jsonl")
    ap.add_argument("--eval-file", default="out/qa_eval.jsonl")
    ap.add_argument("--ask", help="单条提问")
    ap.add_argument("--eval", action="store_true", help="跑评测集")
    ap.add_argument("--backend", default="auto", choices=["auto", "rule", "local", "deepseek"])
    ap.add_argument("--model-dir", default="out/lora")
    ap.add_argument("--log", default="out/tutor_interactions.jsonl")
    args = ap.parse_args()

    docs = [json.loads(l) for l in open(args.data, encoding="utf-8") if l.strip()]
    retriever = TinyRetriever(docs)
    backend = choose_backend(args.backend, args.model_dir)
    tutor = AITutor(backend, retriever, args.log)

    print(f"[后端] {backend.name}")

    if args.eval:
        print(json.dumps(tutor.eval_run(args.eval_file), ensure_ascii=False, indent=2))
        return 0

    q = args.ask or "什么是糖化血红蛋白？"
    out = tutor.ask(q)
    print(f"\n问：{out['question']}")
    print(f"检索到：{out['retrieved']}")
    print(f"护栏改写：{out['guarded']}")
    print(f"\n答：{out['answer']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
