# 临床大模型与病历挖掘 · 课程实验仓库

一门面向人工智能专业本科生的方向课，核心链路：

```
合成病历 → 脱敏 → NER → 结构化 → ICD编码 → LoRA微调 → 智能问答
```

**全部数据为合成虚构数据，不含任何真实患者信息。**

---

> ## 👉 你是学生？直接打开 [`开始这里.md`](开始这里.md)
>
> 那份文件写的是**先打开什么、运行什么命令、最后拿到什么**，按顺序做就行。
> 本文件是给老师和助教看的项目说明。

---

## 30 秒跑通（无需显卡、无需联网）

第一次上课推荐用浏览器预览版：

```bash
docker compose up viewer
```

看到日志里出现 `Serving HTTP` 后，打开：

```text
http://localhost:8000/效果预览.html
```

网页里能直接看到四件事：脱敏前后对照、实体高亮、ICD 编码表、LoRA/SFT
样本示例。学生不用先理解所有术语，先看到全链路产物，再回到实验拆开学。

只想在终端里跑全链路：

```bash
docker compose up course
```

不用 Docker 也可以：

```bash
python src/run_pipeline.py --key "本课程密钥"
```

跑完后看 `out/` 目录：

| 文件 | 内容 |
|---|---|
| `records_deid.jsonl` | 20 条脱敏后的病历 |
| `deid_audit.jsonl` | 脱敏审计日志（谁、哪一类、替换成什么） |
| `deid_report.json` | 脱敏统计与残留风险 |
| `ner_entities.jsonl` | 实体识别结果（疾病/症状/药物/检查/部位） |
| `ner_bio.jsonl` | BIO 序列，可导入 Label Studio 人工校正 |
| `icd_predict.jsonl` | ICD-10 预测与 Top-3 候选 |
| `icd_report.json` | Top-1 / Top-3 准确率、按科室分组 |
| `sft_train.jsonl` / `sft_val.jsonl` | LoRA 微调数据（三类任务） |
| `qa_eval.jsonl` | 人工评测用问答集 |

---

## 目录结构

```
clinical-llm-course/
├── data/      合成病历（20 条，虚构）
├── src/       全链路代码
│   ├── run_pipeline.py    一键跑通
│   ├── deid_pipeline.py   脱敏去标识
│   ├── ner_baseline.py    词典实体识别基线
│   ├── icd_map.py         ICD-10 映射与评测
│   ├── build_sft.py       构建微调数据集
│   ├── train_lora.py      LoRA 训练（含 CPU 降级）
│   └── ai_tutor.py        AI 助教（DeepSeek-R1 集成）
├── docs/
│   ├── 合规清单.md                数据合规与红线
│   ├── 课题对齐分析.md            与 2025GXJK0635 的对照纠偏
│   ├── 评价机制与DBR迭代.md        过程性数据与迭代留痕
│   └── 3090机房实操手册.md         上机步骤
├── syllabus/
│   ├── 课程大纲.md                48 学时 16 周
│   └── 能力单元图谱.md            24 个能力单元（三维标签）
├── Dockerfile / docker-compose.yml
└── out/       运行产出（gitignore）
```

## 给零基础学生的学习路线

| 第几步 | 学什么 | 为什么放在这里 |
|---|---|---|
| 0 | 打开 Docker 预览页 | 先看到最终产物，降低陌生感 |
| 1 | 合成病历 + 脱敏审计 | 建立医疗数据红线意识 |
| 2 | NER 实体识别 | 把自由文本里的医学概念圈出来 |
| 3 | 结构化 | 把病历变成固定字段，便于检索和统计 |
| 4 | ICD 编码 | 理解医疗文本如何进入标准编码体系 |
| 5 | LoRA / SFT 数据 | 学会把任务改写成可训练样本 |
| 6 | QA 与护栏 | 看到医疗问答能做什么、不能做什么 |
| 7 | Docker 交付 | 把自己的结果交给别人一键复现 |
 
每一步都配有 `tools/check_lab.py` 自检，学生可以先自己跑出 PASS，再提交给老师。

---

## 与总课题的关系

本仓库是纵向科研项目 **2025GXJK0635**（《粤港澳大湾区高校人工智能专业
"微专业群+职业能力图谱"课程重构与评价机制研究》）中
**智慧医疗微专业群**的核心课程交付物，对应"3 门 Docker 化微课程"之一。

总项目**所属学科为教育学**，因此本仓库除技术交付外，还必须产出:
能力单元清单、过程性评价数据、DBR 迭代留痕。
详见 [`docs/课题对齐分析.md`](docs/课题对齐分析.md)。

---

## 三个关键设计（答辩时容易被追问的点）

**1. 为什么第一步是合成病历而不是真实病历？**
使用真实病历要走伦理审查、医院数据审批、签署数据使用协议，周期以月计，
且民办本科很难拿到。合成数据把合规风险降到零，让学生第一周就能动手，
同时"如何构造可信的合成数据"本身就是一项产业技能。

**2. 为什么要做残留风险扫描？**
脱敏最容易失效的方式是"看起来脱完了，其实没脱干净"。
本管道在脱敏后反向扫描一遍，用真实标识符的正则去查自己的输出。
教学上让学生亲自触发一次漏脱，比讲十遍规范都管用。

**3. 为什么代理值要放进专用命名空间？**
早期版本让代理手机号保持 `139xxxxxxxx` 的真实形态，
结果残留扫描器把代理值自己又报了一遍警，无法证明脱敏完成。
现在统一使用 `PH-DEID-xxx`、`ID-DEID-xxx` 这类真实数据不可能出现的命名空间，
牺牲一点"格式保留"换来可验证性。这个取舍本身就是一个很好的课堂讨论题。

---

## 算力分级

| 档位 | 条件 | 实验 5 做法 |
|---|---|---|
| A | 24GB 显卡 | Qwen2.5-1.5B + bf16 + LoRA r=16 |
| B | 8GB 显卡 | Qwen2.5-0.5B + 4bit + LoRA r=8 |
| C | 纯 CPU | 不训练，做 3-shot 提示基线对比（脚本自动切换） |

```bash
python src/train_lora.py --data out/sft_train.jsonl          # 自动判断
python src/train_lora.py --data out/sft_train.jsonl --4bit   # B 档
python src/train_lora.py --data out/sft_train.jsonl --cpu-fallback  # C 档
```

---

## AI 助教

```bash
$env:DEEPSEEK_API_KEY = "sk-xxx"        # 配了就走 DeepSeek-R1
python src/ai_tutor.py --ask "糖尿病患者为什么要查肌电图"
python src/ai_tutor.py --eval           # 跑评测集
```

未配置 Key 时自动降级到规则后端，离线可用。
内置三条不可绕过的护栏：禁止诊断与处方、强制就医提示、交互日志不留个人信息。

---

## 镜像体积（开题报告要求 <500MB）

```bash
docker build --target base -t clinical-llm-course:base .
docker images clinical-llm-course:base
```

交付层刻意不安装任何 pip 包，全部功能由标准库实现。
GPU 层（含 torch）单独一层，属运行态工具，不计入课程镜像体积。

---

## 合规提醒

开课前请完整阅读 [`docs/合规清单.md`](docs/合规清单.md)，
特别是第五条"上机操作禁令"，需要在第一堂课向学生宣读并签署承诺书。

我也补了一份 [`docs/新手友好改进建议.md`](docs/新手友好改进建议.md)，里面列出
当前版本已经具备的优点、还可以继续增强的教学体验，以及适合下一轮迭代的任务。

---

## 许可

教学内容与代码可自由用于教学与教研。合成病历为教学虚构数据，
不得用于任何临床决策、诊疗建议或产品训练以外的用途。
