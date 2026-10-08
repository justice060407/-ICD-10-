# 临床大模型与病历挖掘 · Docker 化微课程镜像
#
# 【开题报告硬指标】4.2 节：运用 Docker 容器技术将微课程封装为轻量化单元
#   —— 单镜像体积 < 500MB
#
# 【分层策略】
#   base —— 课程交付镜像。纯 Python 标准库、零第三方依赖，约 135MB。
#           实验 1-4、6-7 全部可完成，无显卡、断网均可跑。对外交付的就是这一层。
#   gpu  —— 仅实验 5（LoRA 训练）使用，含 torch + CUDA，约 6GB。
#           属于运行态工具，不计入课程镜像体积，需向评审单独说明。
#
# 构建：
#   docker build --target base -t clinical-llm-course:base .
#   docker build --target gpu  -t clinical-llm-course:gpu  .
# 体积自检：
#   docker images clinical-llm-course:base

# ---------------------------------------------------------------------------
# 交付层：base
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

WORKDIR /course

COPY src/ ./src/
COPY data/ ./data/
COPY docs/ ./docs/
COPY syllabus/ ./syllabus/
COPY labs/ ./labs/
COPY tools/ ./tools/
COPY README.md .
COPY 开始这里.md .

# 刻意不安装任何 pip 包。
# 脱敏 / 实体识别 / ICD 映射 / 数据构建 全部只用标准库实现，
# 一是压体积满足 500MB 约束，二是保证几年后学生打开仍能原样复现
# （第三方依赖会漂移，纯标准库不会）。
RUN python -c "import sys,compileall; compileall.compile_dir('src', quiet=1); print('stdlib only, python', sys.version.split()[0])"

RUN useradd -m -u 1000 student && chown -R student:student /course
USER student

# 容器一启动就跑通全链路，学生第一次执行立刻看到产出
CMD ["python", "src/run_pipeline.py", "--key", "clinical-llm-course-2026"]

# ---------------------------------------------------------------------------
# 运行态工具层：gpu（仅实验 5 需要，不计入课程镜像体积）
# ---------------------------------------------------------------------------
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04 AS gpu

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /course
COPY src/ ./src/
COPY data/ ./data/

RUN python3 -m pip install --no-cache-dir \
        torch --index-url https://download.pytorch.org/whl/cu121 \
    && python3 -m pip install --no-cache-dir \
        transformers peft datasets accelerate bitsandbytes

CMD ["python3", "src/train_lora.py", "--data", "out/sft_train.jsonl", "--epochs", "3"]
