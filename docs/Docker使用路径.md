# Docker 使用路径（学生视角）

> 写给执行者，不写给评审。目标：**少走弯路，别把时间烧在环境上。**

---

## 0. 先做一个判断：你现在需要 Docker 吗？

```
你要做的事是什么？
│
├─ 写作业 / 改代码 / 跑链路 / 看结果
│     → 不需要 Docker。用 venv，见【路径 A】
│
├─ 训 LoRA（实验 5）
│     → 不需要 Docker。机房用 venv 跑，见【路径 C】
│
├─ 给导师一个"镜像体积 < 500MB"的实测数字
│     → 需要 Docker，一次性。见【路径 B】
│
└─ 把环境发给同学，让他们一键复现
      → 需要 Docker。见【路径 B】，然后把镜像导出分发
```

**结论：你 90% 的时间走路径 A。Docker 只在两个节点出现——验收指标拍照、交付分发。**

---

## 路径 A：日常开发（无 Docker，推荐）

你的笔记本没装 Docker，也不需要装。

```powershell
# 1. 初始化（新开终端跑一次）
D:\AI-Workspace\init.ps1

# 2. 进项目
cd D:\AI-Workspace\projects\clinical-llm-course

# 3. 跑全链路
python src\run_pipeline.py --key "本课程密钥"
```

预期输出（已实测）：

```
  脱敏       193   处替换     [通过]
  实体识别   351   个实体     [通过]
  ICD 编码   1.0   Top-1     [通过]
  微调数据   60    条样本     [通过]
```

产物落在 `out\`，13 个文件。**这是你改代码、调规则、做实验的主循环。**

---

## 路径 B：验证 500MB 硬指标（一次性，必须做）

开题报告 4.2 写了"单镜像 < 500MB"，这是要拿数字交差的。
**不要为了它在自己电脑上装 Docker。** 找一台已经有 Docker 的机器
（实验室服务器、同学的机器、或者机房某台装了的），跑完拿数字就走。

我写好了脚本，一条命令全部做完：

```powershell
cd D:\AI-Workspace\projects\clinical-llm-course
.\tools\docker-check.ps1
```

它会自动做四件事：

1. 检测 Docker 是否可用（不可用会直接告诉你走路径 A，不硬扛）
2. 构建 base 镜像
3. **打印镜像体积** ← 你要的就是这个数字
4. 启动容器跑一遍全链路，确认容器里也能出结果

跑完会生成 `out\docker_evidence.txt`，内容形如：

```
镜像名: clinical-llm-course:base
镜像体积: 137 MB        ← 交差用这个
指标要求: < 500 MB
结论: 通过（余量 363 MB）
容器内全链路: 通过
```

把这个文件直接发给导师，比任何描述都有力。

### 手动版（如果脚本跑不动）

```powershell
docker build --target base -t clinical-llm-course:base .
docker images clinical-llm-course:base          # 记下 SIZE 那一列
docker run --rm -v ${PWD}/out:/course/out clinical-llm-course:base
docker history clinical-llm-course:base         # 看每一层各占多少
```

### 要交付给同学时

```powershell
docker save clinical-llm-course:base -o clinical-llm-course.tar
# 对方：
docker load -i clinical-llm-course.tar
docker run --rm clinical-llm-course:base
```

---

## 路径 C：机房 3090 训 LoRA（不用 Docker）

机房的 3090 机器**大概率没装 Docker，也可能不能联网**。别指望容器，用 venv：

```powershell
cd D:\AI-Workspace\projects\clinical-llm-course
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install transformers peft datasets accelerate

python src\train_lora.py --data out\sft_train.jsonl --model Qwen/Qwen2.5-1.5B-Instruct --epochs 3 --rank 16
```

预期：3090 上 15–25 分钟，显存峰值 12–16 GB。

**断网预案**：在你自己机器上先把轮子打包带走

```powershell
pip download -d wheels torch transformers peft datasets accelerate
# 带 U 盘去机房，然后：
pip install --no-index --find-links wheels torch transformers peft datasets accelerate
```

> ⚠️ 注意别照抄：你笔记本的 5060 是 Blackwell 架构，需要 **torch ≥ 2.7 + cu128**；
> 机房 3090 是 Ampere，用 **cu121**。两个环境不通用。

---

## 路径 D：如果非要在自己机器上装 Docker（不推荐）

真要装，按这个配置把伤害降到最低：

1. 装完立刻改 **Settings → Resources → Advanced → Disk image location**
   改成 `D:\AI-Workspace\docker`（默认在 C 盘 `%LOCALAPPDATA%\Docker`，会吃掉几个 GB）
2. 在 `C:\Users\19354\.wslconfig` 写：

```ini
[wsl2]
memory=4GB
processors=2
swap=2GB
```

不然 WSL2 会默认吃掉你一半内存（你总共 32 GB）。

3. **记住一个坑**：Docker 的虚拟磁盘 `ext4.vhdx` **只增不减**。
   你删掉 10 GB 镜像，C/D 盘一点不释放。必须手动回收：

```powershell
wsl --shutdown
diskpart
# diskpart 里：
#   select vdisk file="D:\AI-Workspace\docker\ext4.vhdx"
#   compact vdisk
```

4. 平时不用就 **Quit Docker Desktop**，别让它常驻后台吃内存。

---

## 三层镜像分别什么时候用

| 层 | 体积 | 用途 | 你要用吗 |
|---|---|---|---|
| `base` | ~137 MB | 实验 1-4、6-7，纯 CPU、可断网 | ✅ **就是它**，指标说的也是它 |
| `gpu` | ~6 GB | 仅实验 5 的 LoRA 训练 | ❌ 机房用 venv 代替 |
| `jupyter` | 临时装 | 课堂演示 | ❌ 演示时临时装，不进交付镜像 |

**答辩口径**：`gpu` 层是运行态工具、不是课程镜像本身，
课程交付单元是 `base` 层 137 MB。这句话要提前想好怎么说，
评审一定会问"你那个 6GB 的怎么回事"。

---

## 常见报错对照

| 报错 | 原因 | 解法 |
|---|---|---|
| `docker: command not found` | 没装 / 没启动 Docker Desktop | 走路径 A |
| `WSL 2 installation is incomplete` | WSL2 后端没装好 | Docker Desktop 里点 Repair，或 `wsl --update` |
| 构建时卡在 `Sending build context` | 上下文太大 | 确认 `.dockerignore` 在，且没把模型放项目里 |
| `no space left on device` | 虚拟磁盘满了 | 走路径 D 第 3 步回收 |
| 容器里输出目录为空 | 没挂 volume | 加 `-v ${PWD}/out:/course/out` |
| LoRA 报 CUDA 版本不对 | torch 与驱动不匹配 | 5060 用 cu128，3090 用 cu121 |

---

## 一条建议

**先别装 Docker。** 把路径 A 跑顺、把代码改对、把 7 个实验做完，
最后找台有 Docker 的机器花 20 分钟跑一次路径 B 拿数字。

顺序反了的话，你会花两天装机、三天调 WSL，而课题真正的难点
（脱敏规则、ICD 映射、能力图谱）一点没推进。
