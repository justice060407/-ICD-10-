# -*- coding: utf-8 -*-
"""把 out/ 里的运行结果渲染成一个可看的效果预览网页。

用法：
    python tools\\demo_view.py
    python tools\\demo_view.py --out out --open     # 生成后直接打开

设计约束：
  - **零第三方依赖**（只吃标准库），和整门课的口径一致
  - 生成单个自包含 HTML，不联网、不用 CDN，拷给谁都能看
  - 全部数据来自合成病历，不含任何真实患者信息

这个脚本解决的其实是个教学问题：脱敏、实体识别、ICD 编码这三件事，
光看 `193 / 351 / 1.0` 三个数字，学生根本不知道系统到底干了什么。
把它摊开到眼前 —— 左边原文右边脱敏文、实体着色高亮、ICD 逐条对照 ——
抽象指标才变成可直觉理解的东西。
"""

import argparse
import html
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# 实体类型 -> (显示名, 背景色, 文字色)
ENTITY_STYLE = {
    "SYM": ("症状", "#DBEAFE", "#1E40AF"),
    "DIS": ("疾病", "#FCE7F3", "#9D174D"),
    "BOD": ("部位", "#D1FAE5", "#065F46"),
    "EXA": ("检查", "#FEF3C7", "#92400E"),
    "DRU": ("药物", "#EDE9FE", "#5B21B6"),
}


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def mark_entities(text, entities):
    """把实体在文本里着色高亮。

    实体可能重叠（词典抽词的常见情况），这里按起点排序后跳过重叠项，
    保证产出的 HTML 标签不会交叉嵌套。
    """
    spans = sorted(entities, key=lambda e: (e.get("start", 0), -(e.get("end", 0))))
    out, cursor = [], 0
    for e in spans:
        s, t = e.get("start", 0), e.get("end", 0)
        if s < cursor or s >= t or t > len(text):
            continue
        typ = e.get("type", "")
        label, bg, fg = ENTITY_STYLE.get(typ, (typ, "#F3F4F6", "#374151"))
        out.append(html.escape(text[cursor:s]))
        out.append(
            f'<mark class="ent" data-t="{html.escape(typ)}" style="background:{bg};color:{fg}" '
            f'title="{html.escape(label)}">{html.escape(text[s:t])}'
            f'<sup class="tag">{html.escape(typ)}</sup></mark>'
        )
        cursor = t
    out.append(html.escape(text[cursor:]))
    return "".join(out)


def _boundary_warning(records, ent_by_id):
    """自动找出"边界明显切错"的实体，直接摆在页面上。

    判据很朴素：实体左边紧贴着英文/数字（说明前面还有字符被落下了）。
    真实病例里最常被这样腰斩的是 PCT、CTnI、HbA1c 这类缩写指标。
    与其让学生自己去猜哪里有问题，不如把可疑点直接标出来 ——
    "能从结果里看出自己错在哪"，比"跑出一个漂亮数字"重要得多。
    """
    bad = []
    for rec in records:
        text = rec.get("clean_text", "")
        for e in ent_by_id.get(rec.get("id"), []):
            s, t = e.get("start", 0), e.get("end", 0)
            if s <= 0 or t > len(text):
                continue
            prev = text[s - 1]
            if prev.isalnum() and prev.isascii():
                bad.append((rec.get("id"), text[max(0, s - 6):s], text[s:t], e.get("type")))
    if not bad:
        return ""
    rows = "".join(
        f"<tr><td><code>{html.escape(str(i))}</code></td>"
        f"<td>...<b>{html.escape(prev)}</b></td>"
        f"<td><b>{html.escape(cur)}</b></td><td>{html.escape(str(typ))}</td></tr>"
        for i, prev, cur, typ in bad[:8]
    )
    return (
        '<div class="warn"><b>自动检测到 {} 处疑似边界切错</b>'
        "（实体左侧紧挨着英文字母或数字，说明前半截被落下了）。"
        "这不是渲染问题，是词典匹配真实抽错了 —— 去实验 2 里分析它："
        "<table style='margin-top:8px'><thead><tr><th>病历</th><th>左边的字符</th>"
        f"<th>抽出来的</th><th>类型</th></tr></thead><tbody>{rows}</tbody></table></div>"
    ).format(len(bad))


def diff_view(before, after):
    """最简单的"前后对照"：把两侧按行号并排，看得出变了哪些字符即可。

    不做逐词 diff —— 脱敏大多是替换成完全不同形态的值，
    逐词对齐反而会把数字铺满屏幕。
    """
    bl, al = before.split("\n"), after.split("\n")
    rows = []
    n = max(len(bl), len(al))
    for i in range(n):
        b = bl[i] if i < len(bl) else ""
        a = al[i] if i < len(al) else ""
        changed = " changed" if b != a else ""
        rows.append(
            f'<tr class="{changed.strip()}">'
            f'<td class="ln">{i + 1}</td>'
            f'<td class="before">{html.escape(b) or "&nbsp;"}</td>'
            f'<td class="after">{html.escape(a) or "&nbsp;"}</td>'
            f"</tr>"
        )
    return "".join(rows)


BASELINE = {"deid": 193, "ner": 351, "icd_top1": 1.0, "sft": 60}


def donut(pct, color, size=64):
    """返回一段内联 SVG 圆环（零依赖）。pct 取 0~1。

    用圆环直观表示"这一项达标了多少" —— 学生改坏代码导致指标退化时，
    圆环会肉眼可见地掉下去、由绿变黄变红，比看数字更直觉。
    """
    pct = max(0.0, min(1.0, pct))
    r = 32
    circ = 2 * 3.141592653589793 * r
    off = circ * (1 - pct)
    cx = cy = 40
    return (
        f'<svg class="ring" viewBox="0 0 80 80" width="{size}" height="{size}" '
        f'role="img" aria-label="完成度 {int(round(pct*100))}%">'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#E5E3DC" stroke-width="9"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="9" '
        f'stroke-linecap="round" stroke-dasharray="{circ:.2f}" stroke-dashoffset="{off:.2f}" '
        f'transform="rotate(-90 {cx} {cy})"/>'
        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" class="rpct">{int(round(pct*100))}%</text>'
        f'</svg>'
    )


def _ratio(actual, base):
    try:
        v = actual / base
    except Exception:
        return 0.0
    return max(0.0, min(1.0, v))


def _color_for(r):
    if r >= 0.999:
        return "#0F6E56"
    if r >= 0.8:
        return "#B7791F"
    return "#A32D2D"


CSS = """
*{box-sizing:border-box}
body{margin:0;padding:0 20px 60px;background:#F7F7F5;color:#2C2C2A;
 font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.65}
.wrap{max-width:1080px;margin:0 auto}
header{padding:32px 0 20px;border-bottom:1px solid #D3D1C7;margin-bottom:28px}
h1{font-size:22px;font-weight:500;margin:0 0 6px}
.sub{color:#5F5E5A;font-size:13px}
h2{font-size:16px;font-weight:500;margin:36px 0 14px;padding-left:10px;border-left:3px solid #534AB7}
h3{font-size:14px;font-weight:500;margin:22px 0 8px;color:#444441}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}
.card{flex:1;min-width:160px;background:#fff;border:1px solid #D3D1C7;border-radius:12px;padding:14px 16px;display:flex;flex-direction:column;align-items:flex-start}
.card .n{font-size:26px;font-weight:500;color:#26215C;line-height:1.2}
.card .l{font-size:12px;color:#5F5E5A;margin-top:2px}
.card .ok{color:#0F6E56;font-size:11px}
.card .ring{margin-bottom:8px}
.ring{}
.rpct{font-size:15px;font-weight:600;fill:#26215C}
.score{display:flex;gap:18px;align-items:center;background:#fff;border:1px solid #D3D1C7;border-radius:12px;padding:16px 20px;margin:18px 0}
.score-ring{flex:0 0 auto}
.score-n{font-size:34px;font-weight:600;color:#26215C;line-height:1}
.score-l{font-size:13px;color:#2C2C2A;margin-top:2px}
.score-d{font-size:12px;color:#5F5E5A;margin-top:6px;max-width:580px}
.mission{margin-top:12px;font-size:13px;color:#26215C;background:#EEEDFE;border:1px solid #CECBF6;border-radius:10px;padding:11px 14px;line-height:1.65}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
th,td{border:1px solid #E5E3DC;padding:6px 9px;text-align:left;vertical-align:top}
th{background:#F1EFE8;font-weight:500;white-space:nowrap}
tr.changed td.before{background:#FCEBEB;color:#791F1F}
tr.changed td.after{background:#EAF3DE;color:#27500A}
.ln{width:32px;color:#888780;text-align:right;font-size:11px;background:#FAFAF8}
td.before,td.after{white-space:pre-wrap;word-break:break-all;font-family:ui-monospace,Consolas,monospace;font-size:12px}
.pay{padding:12px;background:#fff;border:1px solid #D3D1C7;border-radius:10px;
 font-family:ui-monospace,Consolas,monospace;font-size:12px;white-space:pre-wrap;word-break:break-all;margin:6px 0}
mark.ent{border-radius:3px;padding:1px 2px}
mark.ent sup{font-size:9px;opacity:.62;margin-left:2px;vertical-align:super}
.legend{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 4px}
.legend span{font-size:11px;padding:2px 8px;border-radius:10px;border:1px solid #D3D1C7}
.kv{background:#fff;border:1px solid #D3D1C7;border-radius:10px;padding:10px 14px;font-size:12px}
.kv b{font-weight:500}
.g{color:#0F6E56}.r{color:#A32D2D}.d{color:#5F5E5A}
.note{background:#EEEDFE;border:1px solid #CECBF6;border-radius:10px;padding:12px 14px;font-size:13px;color:#26215C}
.warn{background:#FAEEDA;border:1px solid #EF9F27;border-radius:10px;padding:12px 14px;font-size:13px;color:#633806;margin:8px 0}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:820px){.grid2{grid-template-columns:1fr}}
footer{margin-top:40px;padding-top:16px;border-top:1px solid #D3D1C7;color:#888780;font-size:12px}
code{background:#F1EFE8;padding:1px 5px;border-radius:4px;font-size:12px}
"""

JS = """
document.querySelectorAll('mark.ent').forEach(function(m){
  m.addEventListener('click',function(){
    var s=window.getSelection(),r=document.createRange();
    r.selectNodeContents(m); s.removeAllRanges(); s.addRange(r);
  });
});
"""


def build(out_dir, samples=3):
    recs_raw = load_jsonl(os.path.join(ROOT, "data", "synthetic_records_20.jsonl"))
    recs_deid = load_jsonl(os.path.join(out_dir, "records_deid.jsonl"))
    ents = load_jsonl(os.path.join(out_dir, "ner_entities.jsonl"))
    icd = load_jsonl(os.path.join(out_dir, "icd_predict.jsonl"))
    sft = load_jsonl(os.path.join(out_dir, "sft_train.jsonl"))
    rep = {
        "deid": load_json(os.path.join(out_dir, "deid_report.json")),
        "ner": load_json(os.path.join(out_dir, "ner_report.json")),
        "icd": load_json(os.path.join(out_dir, "icd_report.json")),
        "sft": load_json(os.path.join(out_dir, "sft_report.json")),
    }

    from deid_pipeline import DeidPipeline  # 复用装配逻辑，保证与管道同源

    ent_by_id = {e.get("id"): e.get("entities", []) for e in ents}
    parts = []

    d = rep["deid"]; n = rep["ner"]; c = rep["icd"]; s = rep["sft"]
    # 每项相对基线的"达标率"做成圆环：改坏代码时圆环肉眼可见地掉、变黄变红
    deid_r = _ratio(d["脱敏替换项总数"], BASELINE["deid"])
    ner_r = _ratio(n["实体总数"], BASELINE["ner"])
    icd_r = _ratio(c["Top-1准确率"], BASELINE["icd_top1"])
    sft_r = _ratio(s["样本总数"], BASELINE["sft"])
    overall = (deid_r + ner_r + icd_r + sft_r) / 4
    parts.append(f"""
<div class="score">
  <div class="score-ring">{donut(overall, _color_for(overall), 92)}</div>
  <div class="score-txt">
    <div class="score-n">{int(round(overall * 100))}%</div>
    <div class="score-l">综合运行效果（四项达标率均值）</div>
    <div class="score-d">全绿 = 本次运行各项都达到基线；任意一项掉下去，对应圆环会变黄/红，说明你改动让指标退化了。</div>
  </div>
</div>

<div class="cards">
  <div class="card">{donut(deid_r, _color_for(deid_r), 56)}<div class="n">{d['脱敏替换项总数']}</div><div class="l">处 PHI 替换</div>
    <div class="ok">残留风险 {d['残留风险记录数']} · 基线 {BASELINE['deid']}</div></div>
  <div class="card">{donut(ner_r, _color_for(ner_r), 56)}<div class="n">{n['实体总数']}</div><div class="l">个临床实体</div>
    <div class="ok">平均 {n['平均每份病历实体数']}/份 · 基线 {BASELINE['ner']}</div></div>
  <div class="card">{donut(icd_r, _color_for(icd_r), 56)}<div class="n">{c['Top-1准确率']}</div><div class="l">ICD-10 Top-1 准确率</div>
    <div class="ok">Top-3 {c['Top-3准确率']}</div></div>
  <div class="card">{donut(sft_r, _color_for(sft_r), 56)}<div class="n">{s['样本总数']}</div><div class="l">条微调样本</div>
    <div class="ok">训练 {s['训练集']} / 验证 {s['验证集']} · 基线 {BASELINE['sft']}</div></div>
</div>
<div class="note">下面的每一段都是你的代码刚才真实产出的结果，不是示意。
数据来源 <code>out/</code>，全部为合成虚构病历。</div>
""")

    # ── 1. 脱敏前后 ─────────────────────────────────────────────
    parts.append("<h2>一、脱敏：原文 ⇄ 脱敏后</h2>")
    parts.append(
        "<div class='kv'>左侧是原始病历（含全部假想的 PHI），右侧是管道输出。"
        "<b>标红的行 = 被改过的行</b>。重点看：身份证号、手机号变成了专用命名空间，"
        "住址被泛化到区县级，<b>年龄被泛化成年龄段</b>。</div>"
    )
    for i in range(min(samples, len(recs_raw))):
        before = DeidPipeline.assemble(recs_raw[i])
        after = recs_deid[i].get("clean_text", "")
        parts.append(
            f"<h3>病历 {html.escape(recs_deid[i].get('id',''))} "
            f"· {html.escape(recs_deid[i].get('dept',''))}</h3>"
            f"<table><thead><tr><th></th><th>原始</th><th>脱敏后</th></tr></thead>"
            f"<tbody>{diff_view(before, after)}</tbody></table>"
        )

    # ── 2. 实体识别 ────────────────────────────────────────────
    parts.append("<h2>二、实体识别：词典基线抽到了什么</h2>")
    legend = "".join(
        f'<span style="background:{bg};color:{fg}">{lab} <b>{t}</b></span>'
        for t, (lab, bg, fg) in ENTITY_STYLE.items()
    )
    parts.append(f'<div class="legend">{legend}</div>')
    warn = _boundary_warning(recs_deid, ent_by_id)
    if warn:
        parts.append(warn)
    parts.append(
        "<div class='kv'>这是<b>词典基线</b>的结果 —— 它只能抽到词典里写过的词，"
        "所以是<b>下限</b>而不是成绩。留意漏抽和边界切错的地方，那才是实验 2 要你分析的东西。</div>"
    )
    for i in range(min(2, len(recs_deid))):
        rid = recs_deid[i].get("id")
        text = recs_deid[i].get("clean_text", "")
        # 跳过病历头部，只看临床正文字数足够的一段
        parts.append(
            f"<h3>{html.escape(rid)} · {html.escape(recs_deid[i].get('dept',''))}</h3>"
            f"<div class='pay'>{mark_entities(text, ent_by_id.get(rid, []))}</div>"
        )

    # ── 3. ICD 编码 ────────────────────────────────────────────
    parts.append("<h2>三、ICD-10 编码：每条病历的结果</h2>")
    parts.append(
        "<div class='kv'>Top-1 命中专业的金标准 <code>gold_icd10</code>。"
        "命中层级里 <b>L1-精确</b> 是靠术语字典精确匹配，<b>L2-包含</b> 是靠子串包含。"
        "实验 4 要你故意把阈值调到 0.3 和 0.95，看准确率怎么崩。</div>"
    )
    rows = []
    for r in icd:
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td><code>{}</code></td>"
            "<td>{}</td><td>{}</td></tr>".format(
                html.escape(str(r.get("id", ""))),
                html.escape(str(r.get("dept", ""))),
                html.escape(str(r.get("原始诊断", r.get("归一化", ""))))[:46],
                html.escape(str(r.get("gold", ""))),
                html.escape(str(r.get("top1", ""))),
                '<span class="g">命中</span>' if r.get("hit1") else '<span class="r">未中</span>',
            )
        )
    parts.append(
        "<table><thead><tr><th>ID</th><th>科室</th><th>诊断</th>"
        "<th>标准编码</th><th>预测</th><th>结果</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )

    # ── 4. 微调数据 ────────────────────────────────────────────
    parts.append("<h2>四、微调数据集：三类任务各长什么样</h2>")
    parts.append(
        "<div class='kv'>同一个病历会被改写成三种任务。注意 T3 问答的输出尾部"
        "<b>一律带就医提示</b> —— 这是一票否决项，缺一條实验 5 就不及格。</div>"
    )
    shown = set()
    for r in sft:
        task = str(r.get("task", ""))
        key = task.split("-")[0]
        if key in shown:
            continue
        shown.add(key)
        parts.append(
            f"<h3>{html.escape(task)}</h3>"
            f"<div class='kv'><b>输入</b></div>"
            f"<div class='pay'>{html.escape(str(r.get('instruction',''))[:420])}</div>"
            f"<div class='kv'><b>期望输出</b></div>"
            f"<div class='pay'>{html.escape(str(r.get('output',''))[:520])}</div>"
        )
        if len(shown) >= 3:
            break

    # ── 5. 下一步 ──────────────────────────────────────────────
    parts.append("""
<h2>五、接下来看什么</h2>
<div class="note">
想继续往下跑，按顺序：<br>
<b>实验 2（实体识别 NER）</b> <code>python src\\ner_baseline.py --in out\\records_deid.jsonl --out out_lab2</code><br>
&nbsp;&nbsp;↳ 让程序把病历里的"症状 / 疾病 / 部位 / 检查 / 药物"整圈出来，存成 <code>ner_entities.jsonl</code>。<b>NER</b> = 命名实体识别，就是"从文字里抓关键词并分类"。<br>
<b>实验 6（问答助手 QA）</b> <code>python src\\ai_tutor.py --ask "我有糖尿病，最近脚麻，要不要紧？"</code><br>
&nbsp;&nbsp;↳ 跑一个带<b>护栏</b>的医疗问答小助手：它正常回答，但遇到"开处方"这类危险指令会拒绝——这是故意的，医疗 AI 必须这样。<br>
每做完一个都跑 <code>python tools\\check_lab.py --lab N --out out_labN</code> 自检。<br>
&nbsp;&nbsp;↳ <b>check_lab</b> 是"实验打分器"，检查这一步的产物对不对，全绿才算过。<br>
完整步骤见项目根目录的 <b>开始这里.md</b>（顶部有"术语速查"表，看不懂先翻它）。
</div>
""")

    body = "".join(parts)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>临床大模型课程 · 效果预览</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<header>
  <h1>临床大模型与病历挖掘 · 效果预览</h1>
  <div class="sub">合成病历 → 脱敏 → 实体识别 → 结构化 → ICD 编码 → 微调数据　|
  全部数据为虚构合成，不含真实患者信息</div>
  <div class="mission"><b>这门课在干什么、为了什么：</b>在保护隐私的前提下，把病历变成可计算的数据，
  辅助医生、编码员、科研人员和患者，最终提升医疗效率、质量和安全。</div>
</header>
{body}
<footer>由 <code>tools\\demo_view.py</code> 生成 · 数据来自 <code>out/</code> ·
重新生成：<code>python tools\\demo_view.py</code></footer>
</div><script>{JS}</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="生成课程效果预览网页")
    ap.add_argument("--out", default="out", help="out 目录")
    ap.add_argument("--samples", type=int, default=3, help="脱敏对照展示几份病历")
    ap.add_argument("--open", action="store_true", help="生成后用默认浏览器打开")
    args = ap.parse_args()

    for f in ("records_deid.jsonl", "ner_entities.jsonl", "icd_predict.jsonl", "sft_train.jsonl"):
        if not os.path.exists(os.path.join(args.out, f)):
            print(f"[x] 缺少 {args.out}/{f}，请先跑 python src\\run_pipeline.py --key \"本课程密钥\"")
            return 1

    html_str = build(args.out, args.samples)
    dst = os.path.join(args.out, "效果预览.html")
    os.makedirs(args.out, exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(html_str)
    size = os.path.getsize(dst) / 1024
    print(f"[ok] 已生成 {os.path.abspath(dst)}  ({size:.0f} KB)")
    if args.open:
        try:
            os.startfile(os.path.abspath(dst))
        except Exception:
            subprocess.Popen(["start", "", os.path.abspath(dst)], shell=True)
    else:
        print(f"     用浏览器打开它就能看到效果，或重跑时加 --open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
