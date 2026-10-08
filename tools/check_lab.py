# -*- coding: utf-8 -*-
"""实验自检工具 —— 学生自检 / 教师验收共用

用法：
    python tools/check_lab.py --lab 1 --out out_lab1
    python tools/check_lab.py --lab 1 --out out_lab1 --source my_lab1_records.jsonl
    python tools/check_lab.py --all

设计原则：
  1. 零第三方依赖（和课程主链路一致）
  2. 只做检查，不修改任何数据，不会偷偷给学生加分
  3. 输出同时适合人读和机器解析（可直接重定向成 SELFCHECK.txt）
"""

import argparse
import json
import os
import re as _re
import sys
from typing import Any, Dict, List, Tuple

# 强制 UTF-8 输出：Windows 终端默认 GBK，不加这几行中文会乱码
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

DISCLAIMER_KEYWORDS = ("就医", "就诊", "咨询医生", "线下医疗机构")

Item = Tuple[str, bool, str]  # (检查项, 是否通过, 说明)


def _exists(d: str, name: str) -> bool:
    return os.path.exists(os.path.join(d, name))


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_jsonl(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def _has_disclaimer(text: str) -> bool:
    return any(k in text for k in DISCLAIMER_KEYWORDS)


# ── 各实验检查逻辑 ────────────────────────────────────────────────────

def check_lab0(out: str, **_: Any) -> List[Item]:
    items: List[Item] = []
    need = [
        "records_deid.jsonl", "deid_audit.jsonl", "deid_report.json",
        "ner_entities.jsonl", "ner_bio.jsonl", "ner_report.json",
        "icd_predict.jsonl", "icd_report.json",
        "sft_train.jsonl", "sft_val.jsonl", "sft_report.json",
        "qa_eval.jsonl",
    ]
    have = [n for n in need if _exists(out, n)]
    items.append(("产出文件齐全", len(have) == len(need),
                  f"{len(have)}/{len(need)}"))

    if _exists(out, "deid_report.json"):
        r = _load_json(os.path.join(out, "deid_report.json"))
        items.append(("脱敏残留风险为 0", r.get("残留风险记录数") == 0,
                      f"当前 {r.get('残留风险记录数')}"))
        items.append(("脱敏替换数是合理的量级", 0 < r.get("脱敏替换项总数", 0) < 500,
                      f"当前 {r.get('脱敏替换项总数')} 处"))
    if _exists(out, "icd_report.json"):
        r = _load_json(os.path.join(out, "icd_report.json"))
        items.append(("ICD Top-1 ≥ 0.9", r.get("Top-1准确率", 0) >= 0.9,
                      f"当前 {r.get('Top-1准确率')}"))
    if _exists(out, "ner_report.json"):
        r = _load_json(os.path.join(out, "ner_report.json"))
        items.append(("实体抽取不为空", r.get("实体总数", 0) > 0,
                      f"当前 {r.get('实体总数')} 个"))
    return items


def check_lab1(out: str, source: str = "", **_: Any) -> List[Item]:
    items: List[Item] = []
    if source:
        ok = os.path.exists(source)
        items.append(("自写病历文件存在", ok, source or "未指定"))
        if ok:
            recs = _load_jsonl(source)
            items.append(("至少 5 条病历", len(recs) >= 5, f"当前 {len(recs)} 条"))
            depts = {r.get("dept", "") for r in recs}
            real_depts = {d for d in depts if d and "请填" not in d and "自由" not in d}
            items.append(("覆盖 ≥4 个科室", len(real_depts) >= 4,
                          f"当前 {len(real_depts)} 个：{'、'.join(sorted(real_depts))}"))
            has_psy = any("精神" in d or "心理" in d for d in real_depts)
            items.append(("含精神/心理科病历", has_psy, "必做项"))
            blanks = [r.get("id") for r in recs
                      if not str(r.get("gold_icd10", "")).strip()
                      or not str(r.get("gold_icd10", "")).strip()[0:1].isalpha()]
            items.append(("ICD-10 编码已填且像码", not blanks,
                          f"未填/异常：{blanks}" if blanks else "全部已填"))

    ok_audit = _exists(out, "deid_audit.jsonl")
    items.append(("有审计日志（一票否决项）", ok_audit,
                  "缺失直接不及格" if not ok_audit else "存在"))
    items.append(("有脱敏产出", _exists(out, "records_deid.jsonl"), ""))
    if _exists(out, "deid_report.json"):
        r = _load_json(os.path.join(out, "deid_report.json"))
        items.append(("残留风险尽量低", r.get("残留风险记录数", -1) == 0,
                      f"当前 {r.get('残留风险记录数')}；非 0 必须在报告里归因"))

    # 两个历史 bug 的回归防线（2026-10-07 实测抓到，修完必须守住）
    items += _cross_channel_consistency(out)
    items += _real_replacement(out)
    return items


def _cross_channel_consistency(out: str) -> List[Item]:
    """同一字段在正文与结构化头部里必须得到同一个代理值。

    历史事故：头部读到 '12床'、正文正则抓到 '12'，两个 raw 算出两个代理值，
    实测 20 份里 13 份不一致，而当时所有指标照样显示"通过"。
    下游一旦对不上，同一个病人会变成两个人。
    """
    p = os.path.join(out, "records_deid.jsonl")
    if not os.path.exists(p):
        return []
    bad = {"住院号": 0, "床号": 0}
    for rec in _load_jsonl(p):
        h = rec.get("phi_header_deid", {})
        ct = rec.get("clean_text", "")
        for field, tag in (("住院号", "ZY"), ("床号", "BED")):
            m = _re.search(rf"{tag}-\d+", ct)
            if m and m.group(0) != str(h.get(field, "")).rstrip("床号"):
                bad[field] += 1
    out_items = []
    for field, n in bad.items():
        out_items.append((f"{field} 跨通道一致", n == 0,
                          f"{n} 份不一致：正文与头部对不上" if n else "全部一致"))
    return out_items


def _real_replacement(out: str) -> List[Item]:
    """防止"假替换"：计数了、写了日志，但值压根没变。

    历史事故：age() 只在 >89 岁时泛化，其余原样返回。20/20 份病历年龄
    一字未改，报告和审计日志却照样记了 "20 处替换"。这类失效最危险，
    因为它表现为"通过"。
    """
    p = os.path.join(out, "records_deid.jsonl")
    if not os.path.exists(p):
        return []
    rows = _load_jsonl(p)
    same = 0
    for rec in rows:
        h = rec.get("phi_header_deid", {})
        age = str(h.get("年龄", ""))
        # 精确年龄（形如 '58岁'）不该出现在脱敏结果里
        if _re.fullmatch(r"\d{1,3}\s*岁", age):
            same += 1
    return [("年龄已真正泛化（非假替换）", same == 0,
             f"{same} 份仍是精确年龄" if same else f"样例 {rows[0]['phi_header_deid'].get('年龄')}"
             if rows else "无数据")]


def check_lab2(out: str, **_: Any) -> List[Item]:
    items: List[Item] = []
    for n in ("ner_entities.jsonl", "ner_bio.jsonl", "ner_report.json"):
        items.append((f"产出 {n}", _exists(out, n), ""))
    if _exists(out, "ner_report.json"):
        r = _load_json(os.path.join(out, "ner_report.json"))
        items.append(("实体数 ≥ 200", r.get("实体总数", 0) >= 200,
                      f"当前 {r.get('实体总数')}"))
        types = r.get("按类型统计", {})
        for t in ("SYM", "DIS", "BOD", "EXA", "DRU"):
            items.append((f"{t} 类非空", types.get(t, 0) > 0, f"{types.get(t, 0)} 个"))
    return items


def check_lab3(out: str, **_: Any) -> List[Item]:
    items: List[Item] = []
    ok = _exists(out, "structured.jsonl")
    items.append(("结构化产出存在", ok, ""))
    if ok:
        rows = _load_jsonl(os.path.join(out, "structured.jsonl"))
        items.append(("20 条全部有输出", len(rows) >= 20, f"当前 {len(rows)} 条"))
        none_keys = 0
        total_keys = 0
        for r in rows:
            for k in ("basic", "admission", "chief", "diagnosis", "exam", "medication"):
                total_keys += 1
                if r.get(k) is None:
                    none_keys += 1
        rate = 1 - none_keys / max(total_keys, 1)
        items.append(("字段完整率 ≥ 90%", rate >= 0.90, f"当前 {rate:.1%}"))
    items.append(("有完整率报告", _exists(out, "structure_report.json"), ""))
    return items


def check_lab4(out: str, **_: Any) -> List[Item]:
    items: List[Item] = []
    items.append(("有编码产出", _exists(out, "icd_predict.jsonl"), ""))
    if _exists(out, "icd_report.json"):
        r = _load_json(os.path.join(out, "icd_report.json"))
        items.append(("Top-1 ≥ 0.9", r.get("Top-1准确率", 0) >= 0.9,
                      f"当前 {r.get('Top-1准确率')}"))
        items.append(("Top-3 ≥ Top-1", r.get("Top-3准确率", 0) >= r.get("Top-1准确率", 0), ""))
    items.append(("有错误归因报告", False,
                  "人工检查：报告里需有 ≥3 个案例，且含 DRG 影响分析"))
    return items


def check_lab5(out: str, **_: Any) -> List[Item]:
    items: List[Item] = []
    items.append(("有训练集", _exists(out, "sft_train.jsonl"), ""))
    items.append(("有验证集", _exists(out, "sft_val.jsonl"), ""))
    if _exists(out, "sft_train.jsonl"):
        rows = _load_jsonl(os.path.join(out, "sft_train.jsonl"))
        items.append(("样本数 ≥ 48", len(rows) >= 48, f"当前 {len(rows)} 条"))
        # 只看 task 字段以 T3 开头的行。
        # 注意：绝不能用 json.dumps 里搜 "T3" —— 结构化任务的输出里会出现
        # 真实的 "游离T3"（甲功）和 "cT3N1M0"（TNM 分期），会误报。
        bad = [r for r in rows
               if str(r.get("task", "")).startswith("T3")
               and not _has_disclaimer(r.get("output", ""))]
        items.append(("所有问答类输出都有就医提示（一票否决）", not bad,
                      f"{len(bad)} 条缺失" if bad else "全部通过"))
        ok_json = 0
        for r in rows:
            o = r.get("output", "")
            if o.strip().startswith("{") or "「" in o:
                try:
                    json.loads(o)
                    ok_json += 1
                except Exception:  # noqa: BLE001
                    pass
        items.append(("结构化类输出可被 json 解析", ok_json > 0, f"{ok_json} 条解析成功"))
    return items


def check_lab6(out: str, **_: Any) -> List[Item]:
    items: List[Item] = []
    items.append(("有评测集", _exists(out, "qa_eval.jsonl"), ""))
    items.append(("有交互日志", _exists(out, "tutor_interactions.jsonl"), ""))
    if _exists(out, "tutor_interactions.jsonl"):
        rows = _load_jsonl(os.path.join(out, "tutor_interactions.jsonl"))
        if rows:
            # 只统计评测集跑出来的记录。
            # 日志是追加写的，手动 --ask 的危险提问会永久拉高越界率，
            # 不圈定范围的话这个指标会被历史污染、失去可比性。
            evals = [r for r in rows if r.get("phase") == "eval"]
            scope = evals if evals else rows
            note = "" if evals else "（日志无 phase 字段，已回退到全部记录）"
            cov = sum(1 for r in scope if _has_disclaimer(r.get("answer", ""))) / len(scope)
            items.append(("就医提示覆盖率 100%（一票否决）", cov >= 1.0,
                          f"当前 {cov:.1%}{note}"))
            # guarded 自 2026-09-26 起只表示 G1 越界拦截，不再包含常规免责追加
            guarded = sum(1 for r in scope if r.get("guarded")) / len(scope)
            items.append(("越界拦截率处于健康区间(<50%)", guarded < 0.5,
                          f"当前 {guarded:.1%}{note}；过高说明模型本身在输出危险内容"))
            has_pii_fields = any(k in (rows[0] or {}) for k in ("姓名", "身份证号", "电话"))
            items.append(("日志不含 PII 字段（一票否决）", not has_pii_fields,
                          "禁止在日志里落任何可识别字段"))
    items.append(("有四维评测表", False,
                  "人工检查：≥10 题 × 4 维度，有均值"))
    return items


def check_lab7(out: str, project_root: str = ".", **_: Any) -> List[Item]:
    items: List[Item] = []
    df = os.path.join(project_root, "Dockerfile.mine")
    items.append(("有自己的 Dockerfile", os.path.exists(df), "Dockerfile.mine"))
    if os.path.exists(df):
        txt = open(df, encoding="utf-8").read()
        items.append(("FROM 未使用 latest", ":latest" not in txt, "必须锁版本"))
        items.append(("未使用 COPY . .", "COPY . ." not in txt, "必须精确 COPY"))
        items.append(("使用 slim/alpine 基础镜像",
                      "slim" in txt or "alpine" in txt, ""))
    di = os.path.join(project_root, ".dockerignore")
    items.append(("有 .dockerignore", os.path.exists(di), ""))
    items.append(("镜像体积 < 500MB", False,
                  "必须在有 Docker 的机器上用 docker images 实测，别拿估算值"))
    items.append(("已在另一台机器复现", False, "人工确认项"))
    return items


CHECKS = {
    0: ("环境验证", check_lab0),
    1: ("合成病历与脱敏", check_lab1),
    2: ("临床实体识别", check_lab2),
    3: ("病历结构化", check_lab3),
    4: ("ICD-10 编码与归因", check_lab4),
    5: ("LoRA 领域适配", check_lab5),
    6: ("问答与安全兜底", check_lab6),
    7: ("工程化交付", check_lab7),
}


def run(lab: int, out: str, source: str, project_root: str) -> int:
    name, fn = CHECKS[lab]
    print("=" * 62)
    print(f"实验 {lab} 自检 · {name}")
    print(f"产出目录：{os.path.abspath(out)}")
    if source:
        print(f"源文件　：{os.path.abspath(source)}")
    print("=" * 62)

    items = fn(out=out, source=source, project_root=project_root)

    blocked = 0
    for label, ok, note in items:
        flag = "PASS" if ok else "FAIL"
        extra = f"   {note}" if note else ""
        print(f"  [{flag}] {label}{extra}")
        if not ok and "一票否决" in note:
            blocked += 1

    total = len(items)
    passed = sum(1 for _, ok, _ in items if ok)
    auto = [i for i in items if i[1] or "一票否决" not in i[2]]
    print("-" * 62)
    print(f"  自动检查通过：{passed}/{total}")
    pending = [i[0] for i in items if not i[1] and "人工" in i[2]]
    if pending:
        print(f"  需人工验收：{'、'.join(pending)}")
    if blocked:
        print(f"  ⚠ 命中 {blocked} 项一票否决：本次实验直接不合格，先看这些")
    print("-" * 62)
    return 0 if blocked == 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="实验自检工具")
    ap.add_argument("--lab", type=int, choices=sorted(CHECKS), help="实验编号 0-7")
    ap.add_argument("--all", action="store_true", help="依次检查全部实验")
    ap.add_argument("--out", default="out", help="产出目录")
    ap.add_argument("--source", default="", help="源文件（实验1的自写病历）")
    ap.add_argument("--root", default=".", help="项目根目录（实验7用）")
    args = ap.parse_args()

    if args.all:
        code = 0
        for lab in sorted(CHECKS):
            c = run(lab, args.out, args.source, args.root)
            code = max(code, c)
            print()
        return code

    if args.lab is None:
        ap.print_help()
        return 1
    return run(args.lab, args.out, args.source, args.root)


if __name__ == "__main__":
    sys.exit(main())
