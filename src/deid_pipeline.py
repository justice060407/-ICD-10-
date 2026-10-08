# -*- coding: utf-8 -*-
"""
临床病历脱敏/去标识化管道（教学版）

教学目标：
  1. 让学生理解"去标识化"与"匿名化"的法律区别（前者可回溯，后者不可逆）
  2. 掌握一致性代理：同一个真实人在全文、全批次中映射到同一个假身份
  3. 理解日期平移：保留临床时序（间隔、先后），只消除绝对日期
  4. 建立审计意识：每一次替换都要留痕，且映射表必须与正文分离保存

设计原则：
  - 纯标准库实现，无网络依赖，Docker 内离线可跑
  - 映射表与脱敏正文物理分离，映射表视为敏感资产单独管控
  - 默认"保守策略"：宁可多脱一点，不可漏脱

用法：
  python src/deid_pipeline.py --in data/synthetic_records_20.jsonl \
      --out out/ --key "2026-临床大模型课程" --audit
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# 1. 假名素材库（全部虚构，仅用于教学）
# ---------------------------------------------------------------------------
FAKE_SURNAMES = ["安", "白", "常", "丁", "方", "甘", "郝", "贾", "康", "龙",
                 "孟", "聂", "欧", "潘", "祁", "任", "宋", "谭", "万", "夏"]
FAKE_GIVEN = ["一鸣", "子墨", "思远", "若彤", "嘉树", "清和", "允行", "知微",
              "望舒", "砚青", "南枝", "临川", "听澜", "向晚", "扶苏", "怀瑾"]
FAKE_DOCTOR_SURNAMES = ["卜", "岑", "邸", "费", "戈", "侯", "冀", "骆", "缪", "冉"]
FAKE_DOCTOR_GIVEN = ["医生A", "医生B", "医生C", "医生D", "医生E",
                     "医生F", "医生G", "医生H", "医生I", "医生J"]

# 18 类个人隐私字段（对应《个人信息保护法》敏感个人信息 + 病历书写规范）
PHI_TYPES = [
    "姓名", "身份证号", "手机号", "固定电话", "住址", "住院号", "门诊号",
    "床号", "主管医师", "医院名称", "电子邮箱", "银行卡号", "医保卡号",
    "车牌号", "IP地址", "URL", "日期", "年龄",
]


# ---------------------------------------------------------------------------
# 2. 确定性代理生成器
# ---------------------------------------------------------------------------
class SurrogateFactory:
    """基于 HMAC-SHA256 的一致性代理生成器。

    同一个 key + 同一个原始值 -> 永远得到同一个代理值。
    换 key 则全部代理值改变，实现"跨批次隔离"。
    """

    def __init__(self, key: str, seed_salt: str = "clinical-llm-course",
                 age_policy: str = "band"):
        self._key = (seed_salt + "|" + key).encode("utf-8")
        # age_policy: band=泛化为年龄段（默认，更严格）
        #             hipaa=HIPAA Safe Harbor 路线（<90 保留原值）
        self._age_policy = age_policy

    def _digest(self, raw: str, domain: str) -> int:
        msg = f"{domain}|{raw}".encode("utf-8")
        return int.from_bytes(hmac.new(self._key, msg, hashlib.sha256).digest()[:8], "big")

    @staticmethod
    def _norm_id(raw: str) -> str:
        """序列号类字段归一化 —— 一致性 bug 的根治点。

        2026-10-07 修复：同一个字段在两条通道里被读成不同的字符串。
          头部字段读到  "12床"（带单位后缀）
          正文正则抓到 "12"   （捕获组不含中文）
        raw 不同 -> HMAC 摘要不同 -> **同一张床算出两个代理值**。
        实测 20 份病历里 13 份出现正文与头部床号不一致，直接破坏了
        "相同原始值映射到同一代理值"的承诺，下游无法跨字段关联。

        归一化：剥掉非字母数字字符，纯数字再去前导零。
        """
        s = re.sub(r"[^A-Za-z0-9]", "", raw or "")
        m = re.match(r"^0*(\d+)$", s)
        if m:
            return m.group(1)
        return s.upper()

    def person_name(self, raw: str) -> str:
        """患者姓名 -> 假名（保留"看起来像人名"的表面特征）"""
        d = self._digest(raw, "name")
        s = FAKE_SURNAMES[d % len(FAKE_SURNAMES)]
        g = FAKE_GIVEN[(d // len(FAKE_SURNAMES)) % len(FAKE_GIVEN)]
        return f"{s}{g}"

    def doctor_name(self, raw: str) -> str:
        d = self._digest(raw, "doctor")
        s = FAKE_DOCTOR_SURNAMES[d % len(FAKE_DOCTOR_SURNAMES)]
        g = FAKE_DOCTOR_GIVEN[(d // len(FAKE_DOCTOR_SURNAMES)) % len(FAKE_DOCTOR_GIVEN)]
        return f"{s}{g}"

    def id_card(self, raw: str) -> str:
        """身份证号 -> 代理标识

        教学要点（重要）：代理值必须落在"真实标识符不可能出现的命名空间"。
        早期版本让代理号保留 18 位数字形态，结果残留风险扫描器把自己生成的
        代理号又报警一次，无法证明脱敏已完成。因此这里改为带 DEID 标记的
        专用命名空间，代价是牺牲了"格式保留"，换来的是可验证性。
        """
        d = self._digest(raw, "idcard")
        # 固定 12 位宽：哈希值位数不固定时，16~19 位会被银行卡号残留规则误报
        return f"ID-DEID-{d % 10**12:012d}"

    def phone(self, raw: str) -> str:
        """手机号 -> 代理标识（同上使用专用命名空间，避免与真实现网号段混淆）"""
        d = self._digest(raw, "phone")
        return f"PH-DEID-{d % 100000000:08d}"

    def landline(self, raw: str) -> str:
        d = self._digest(raw, "landline")
        return f"TEL-DEID-{d % 10000000:07d}"

    def address(self, raw: str) -> str:
        """住址 -> 泛化到区县级，丢弃门牌单元房号

        教学要点：泛化（generalization）是比替换更彻底的手段，
        但会损失信息；保留区县是为了保留流行病学分析价值。
        """
        m = re.match(r"^(.*?[省自治区]?)?([^市]*市)?([^市区县]*(区|县))", raw)
        if m:
            province = m.group(1) or ""
            city = m.group(2) or ""
            district = m.group(3) or ""
            core = f"{province}{city}{district}".strip()
            if core:
                return f"{core}（详细地址已泛化）"
        return "住址已泛化至区县级"

    def serial_no(self, raw: str, tag: str) -> str:
        """住院号/门诊号/床号 -> 稳定流水号

        务必对 raw 做归一化：跨通道一致性全靠这一步。
        直接拿不同形态的 raw 去算摘要，是本项目踩过的真实坑。
        """
        d = self._digest(self._norm_id(raw), tag)
        return f"{tag}-{d % 1000000:06d}"

    def hospital(self, raw: str) -> str:
        d = self._digest(raw, "hospital")
        return f"示例医院{d % 100:02d}号"

    def card_no(self, raw: str) -> str:
        d = self._digest(raw, "card")
        return f"CARD-DEID-{d % 100000000:08d}"

    def license_plate(self, raw: str) -> str:
        d = self._digest(raw, "plate")
        return f"京A·{d % 100000:05d}"

    def ip_addr(self, raw: str) -> str:
        d = self._digest(raw, "ip")
        return f"10.{d % 256}.{(d // 256) % 256}.{(d // 65536) % 256}"

    def email(self, raw: str) -> str:
        d = self._digest(raw, "email")
        return f"patient{d % 100000:05d}@example.edu.cn"

    def url(self, raw: str) -> str:
        return "https://example.edu.cn/redacted"

    def date_shift(self, record_key: str, date_str: str, span_days: int = 0) -> str:
        """日期平移：同一份病历内部使用同一个偏移量

        教学要点：真实诊疗中"入院第3天复查"这类相对时序是有临床意义的，
        直接抹掉日期会破坏时序信息。按病历级统一偏移可同时满足
        "不可回溯到真实就诊日"与"保留时序"。
        """
        d = self._digest(record_key, "dateshift")
        offset = (d % 731) - 365  # -365 ~ +365 天
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_str)
        if not m:
            return "[日期已平移]"
        import datetime as _dt
        try:
            base = _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            shifted = base + _dt.timedelta(days=offset + span_days)
            return shifted.isoformat()
        except ValueError:
            return "[日期已平移]"

    def age(self, raw: str) -> str:
        """年龄处理。两种策略，默认是比 HIPAA 更严的年龄段泛化。

        教学要点（2026-10-07 修订）：
        旧实现只在 >89 岁时泛化，其余**原样返回**。结果 20/20 份病历的
        年龄数值一字未改，审计日志和报告却照样记了 "20 处替换" ——
        这是典型的**假替换**：计数了、日志写了，风险还在原地。
        相比报错而言，这种"看起来做完了"的失效更难被发现。

        band（默认）：泛化为 10 岁一档的年龄段。
            理由有三：① 年龄+性别+科室+入院日期 是重识别的经典四件套，
            精确年龄是准标识符（实验 1 讲义就是这么讲的）；
            ② 国内《健康医疗数据安全指南》比 HIPAA 更严；
            ③ 与 src/structure_reference.py 的 age_band() 口径保持一致，
            管道与下游不要各说各话。
        hipaa：HIPAA Safe Harbor 原教旨路线，<90 保留原值，>=90 泛化为 90+。
            --age-policy hipaa 可切换，用于课堂对比两种口径的差异。
        """
        m = re.search(r"(\d{1,3})", raw or "")
        if not m:
            return raw
        v = int(m.group(1))
        if self._age_policy == "hipaa":
            return "90岁以上" if v > 89 else f"{v}岁"
        if v >= 90:
            return "90岁以上"
        if v >= 75:
            return "75-89岁"
        if v >= 60:
            return "60-74岁"
        if v >= 45:
            return "45-59岁"
        if v >= 18:
            return "18-44岁"
        return "0-17岁"


# ---------------------------------------------------------------------------
# 3. 识别规则（正则 + 上下文）
# ---------------------------------------------------------------------------
RULES: List[Tuple[str, str, str, int]] = [
    # (PHI类型, 正则, 说明, 优先级)  数字越小越精确，越精确越优先
    ("身份证号", r"\b\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b", "18位公民身份号码", 10),
    ("手机号", r"\b1[3-9]\d{9}\b", "中国大陆手机号", 10),
    ("固定电话", r"\b0\d{2,3}-\d{7,8}\b", "固定电话", 10),
    ("银行卡号", r"\b(?:\d{16}|\d{17}|\d{19})\b", "银行卡号（排除18位以避免误吞身份证）", 50),
    ("电子邮箱", r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "电子邮箱", 10),
    ("IP地址", r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "IP地址", 20),
    ("URL", r"https?://[^\s，。；]+", "网址", 20),
    ("医保卡号", r"(?:医保卡号?|社保卡号?)\s*[:：]?\s*([A-Za-z0-9]{8,20})", "医保卡号", 20),
    ("住院号", r"(?:住院号|住院流水号)\s*[:：]?\s*([A-Za-z0-9]{4,20})", "住院号", 20),
    ("门诊号", r"(?:门诊号|就诊号)\s*[:：]?\s*([A-Za-z0-9]{4,20})", "门诊号", 20),
    ("床号", r"(?:床号|床位)\s*[:：]?\s*([A-Za-z0-9\-]{1,12})", "床号", 20),
    ("住址", r"(?:家庭住址|现住址|通讯地址|住址)\s*[:：]?\s*([^\n，。；]{4,60})", "住址", 20),
    ("车牌号", r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼][A-Z][·•]?[A-Za-z0-9]{4,6}", "车牌号", 30),
    ("日期", r"\b(?:19|20)\d{2}[-/年]\d{1,2}[-/月]\d{1,2}日?", "绝对日期", 40),
    ("年龄", r"\b\d{1,3}\s*岁", "年龄", 40),
    ("姓名", r"(?:患者|姓名|病人)\s*[:：]?\s*([\u4e00-\u9fa5]{2,4})(?=[，,、\s。；;])", "患者姓名（上下文触发）", 30),
    ("主管医师", r"(?:主管医师|主治医师|经治医师|接诊医生)\s*[:：]?\s*([\u4e00-\u9fa5]{2,4})", "医师姓名", 30),
    ("医院名称", r"[\u4e00-\u9fa5]{2,10}(?:市|省)?(?:第[一二三四五六七八九十])?(?:人民|中心|儿童|妇幼|肿瘤|精神卫生|眼科)?医院", "医院名称", 30),
]

# 需要"整段替换"而非"局部替换"的类型（没有稳定的边界）
WHOLE_FIELD_TYPES = {"姓名", "住址", "主管医师"}


# ---------------------------------------------------------------------------
# 4. 审计记录
# ---------------------------------------------------------------------------
@dataclass
class AuditEntry:
    record_id: str
    phi_type: str
    raw_len: int
    surrogate: str
    position: int
    method: str


@dataclass
class DeidResult:
    record_id: str
    clean_text: str
    audit: List[AuditEntry] = field(default_factory=list)
    residual_risk: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 5. 核心管道
# ---------------------------------------------------------------------------
class DeidPipeline:
    def __init__(self, key: str, age_policy: str = "band"):
        self.sf = SurrogateFactory(key, age_policy=age_policy)
        self.mapping_store: Dict[str, Dict[str, str]] = {}

    # -- 5.1 从结构化数据装配带 PHI 的原始病历文本 ------------------------
    @staticmethod
    def assemble(record: dict) -> str:
        phi = record.get("phi_header", {})
        sec = record.get("sections", {})
        head = (
            f"【{phi.get('医院名称', '')}住院病历】\n"
            f"姓名：{phi.get('姓名', '')}　性别：{phi.get('性别', '')}　年龄：{phi.get('年龄', '')}\n"
            f"身份证号：{phi.get('身份证号', '')}　联系电话：{phi.get('联系电话', '')}\n"
            f"家庭住址：{phi.get('家庭住址', '')}\n"
            f"住院号：{phi.get('住院号', '')}　床号：{phi.get('床号', '')}　"
            f"入院日期：{phi.get('入院日期', '')}\n"
            f"主管医师：{phi.get('主管医师', '')}\n"
        )
        body = "\n".join(f"{k}：{v}" for k, v in sec.items() if v)
        return head + "\n" + body

    # -- 5.2 结构化字段脱敏 ----------------------------------------------
    def deid_header(self, record: dict) -> dict:
        phi = record.get("phi_header", {})
        rid = record.get("id", "UNKNOWN")
        clean = dict(phi)
        clean["姓名"] = self.sf.person_name(phi.get("姓名", ""))
        clean["身份证号"] = self.sf.id_card(phi.get("身份证号", ""))
        clean["联系电话"] = self.sf.phone(phi.get("联系电话", ""))
        clean["家庭住址"] = self.sf.address(phi.get("家庭住址", ""))
        clean["住院号"] = self.sf.serial_no(phi.get("住院号", ""), "ZY")
        clean["床号"] = self.sf.serial_no(phi.get("床号", ""), "BED")
        clean["入院日期"] = self.sf.date_shift(rid, phi.get("入院日期", ""))
        clean["主管医师"] = self.sf.doctor_name(phi.get("主管医师", ""))
        clean["医院名称"] = self.sf.hospital(phi.get("医院名称", ""))
        if "年龄" in clean:
            clean["年龄"] = self.sf.age(clean["年龄"])
        return clean

    # -- 5.3 自由文本脱敏（正则扫描 + 一致性代理） ------------------------
    def deid_text(self, record_id: str, text: str) -> DeidResult:
        spans: List[Tuple[int, int, str, str, int]] = []  # (start, end, type, matched, priority)

        # 关键：按优先级升序扫描，精确规则先占位；宽泛规则不得覆盖精确规则
        for phi_type, pattern, _desc, prio in sorted(RULES, key=lambda r: r[3]):
            for m in re.finditer(pattern, text):
                # 取捕获组（若有）以获得更精确的值
                # 陷阱警示：正则中所有"仅用于分支"的括号必须写成 (?:...)。
                # 一旦写成 (19|20) 这类捕获组，group(1) 只会拿到 "19"，
                # 于是替换区间的起点/终点被压缩，脱敏只替换了开头两个字符，
                # 而残留扫描又查不出问题 —— 这是脱敏系统最危险的失效模式。
                if m.groups():
                    start, end, raw = m.start(1), m.end(1), m.group(1)
                else:
                    start, end, raw = m.start(), m.end(), m.group(0)

                # 已被更精确（或同优先级）的区间覆盖 -> 放弃本次匹配
                if any(s <= start and e >= end and p <= prio for s, e, _t, _v, p in spans):
                    continue
                # 本次更精确 -> 移除被覆盖的宽泛区间
                spans = [x for x in spans if not (start <= x[0] and end >= x[1])]
                spans.append((start, end, phi_type, raw, prio))

        spans.sort(key=lambda x: x[0])

        out_parts: List[str] = []
        cursor = 0
        audit: List[AuditEntry] = []

        for start, end, phi_type, raw, _prio in spans:
            out_parts.append(text[cursor:start])
            surrogate = self._surrogate(record_id, phi_type, raw)
            out_parts.append(surrogate)
            audit.append(AuditEntry(record_id, phi_type, len(raw), surrogate, start, "regex+hmac"))
            cursor = end
        out_parts.append(text[cursor:])

        clean = "".join(out_parts)
        result = DeidResult(record_id=record_id, clean_text=clean, audit=audit)
        result.residual_risk = self._residual_scan(clean)
        return result

    def _surrogate(self, record_id: str, phi_type: str, raw: str) -> str:
        sf = self.sf
        table = {
            "姓名": lambda v: sf.person_name(v),
            "主管医师": lambda v: sf.doctor_name(v),
            "身份证号": lambda v: sf.id_card(v),
            "手机号": lambda v: sf.phone(v),
            "固定电话": lambda v: sf.landline(v),
            "银行卡号": lambda v: sf.card_no(v),
            "医保卡号": lambda v: sf.card_no(v),
            "住址": lambda v: sf.address(v),
            "住院号": lambda v: sf.serial_no(v, "ZY"),
            "门诊号": lambda v: sf.serial_no(v, "MZ"),
            "床号": lambda v: sf.serial_no(v, "BED"),
            "医院名称": lambda v: sf.hospital(v),
            "电子邮箱": lambda v: sf.email(v),
            "IP地址": lambda v: sf.ip_addr(v),
            "URL": lambda v: sf.url(v),
            "车牌号": lambda v: sf.license_plate(v),
            "日期": lambda v: sf.date_shift(record_id, v),
            "年龄": lambda v: sf.age(v),
        }
        fn = table.get(phi_type)
        return fn(raw) if fn else f"[已脱敏-{phi_type}]"

    # -- 5.4 残留风险扫描（教学亮点：让学生看到"脱敏不彻底"的后果） ------
    @staticmethod
    def _residual_scan(clean_text: str) -> List[str]:
        risks: List[str] = []
        checks = [
            ("疑似18位身份证残留", r"\b\d{6}(19|20)\d{2}\d{4}\d{3}[\dXx]\b"),
            ("疑似手机号残留", r"\b1[3-9]\d{9}\b"),
            ("疑似邮箱残留", r"[\w.+-]+@(?!example\.edu\.cn)[\w-]+\.[\w.-]+"),
            ("疑似银行卡残留", r"\b\d{16,19}\b"),
        ]
        for label, pat in checks:
            if re.search(pat, clean_text):
                risks.append(label)
        return risks


# ---------------------------------------------------------------------------
# 6. 批处理入口
# ---------------------------------------------------------------------------
def run(infile: str, outdir: str, key: str, audit: bool = True,
        age_policy: str = "band") -> dict:
    os.makedirs(outdir, exist_ok=True)
    pipe = DeidPipeline(key, age_policy=age_policy)

    records = []
    with open(infile, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    clean_records = []
    audit_rows = []
    stat: Dict[str, int] = {}
    residual_total = 0

    for rec in records:
        rid = rec["id"]
        raw_text = DeidPipeline.assemble(rec)
        res = pipe.deid_text(rid, raw_text)
        clean_header = pipe.deid_header(rec)

        clean_records.append({
            "id": rid,
            "dept": rec.get("dept"),
            "phi_header_deid": clean_header,
            "sections": rec.get("sections", {}),
            "gold_icd10": rec.get("gold_icd10"),
            "gold_diagnosis": rec.get("gold_diagnosis"),
            "clean_text": res.clean_text,
        })

        for a in res.audit:
            stat[a.phi_type] = stat.get(a.phi_type, 0) + 1
            audit_rows.append({
                "record_id": a.record_id, "phi_type": a.phi_type,
                "position": a.position, "surrogate": a.surrogate, "method": a.method,
            })
        if res.residual_risk:
            residual_total += 1
            audit_rows.append({
                "record_id": rid, "phi_type": "残留风险",
                "position": -1, "surrogate": ";".join(res.residual_risk), "method": "residual_scan",
            })

    with open(os.path.join(outdir, "records_deid.jsonl"), "w", encoding="utf-8") as f:
        for r in clean_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    if audit:
        with open(os.path.join(outdir, "deid_audit.jsonl"), "w", encoding="utf-8") as f:
            for r in audit_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    report = {
        "输入病历数": len(records),
        "脱敏替换项总数": sum(stat.values()),
        "按类型统计": dict(sorted(stat.items(), key=lambda x: -x[1])),
        "残留风险记录数": residual_total,
        "输出目录": outdir,
        "年龄策略": age_policy,
        "一致性说明": (
            "相同原始值（已做形态归一化）在相同 key 下始终映射到同一代理值；"
            "更换 key 即可实现跨批次隔离"
        ),
    }
    with open(os.path.join(outdir, "deid_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="病历脱敏管道（教学版）")
    ap.add_argument("--in", dest="infile", required=True, help="输入 JSONL")
    ap.add_argument("--out", dest="outdir", default="out", help="输出目录")
    ap.add_argument("--key", dest="key", default="default-course-key", help="脱敏密钥（换值即换假名空间）")
    ap.add_argument("--no-audit", dest="audit", action="store_false", help="不写审计日志")
    ap.add_argument(
        "--age-policy", dest="age_policy", choices=["band", "hipaa"], default="band",
        help="年龄处理策略：band=泛化为年龄段（默认，更严格）；"
             "hipaa=HIPAA Safe Harbor 口径（<90 保留原值）。用于课堂对比两种口径。",
    )
    args = ap.parse_args()

    report = run(args.infile, args.outdir, args.key, args.audit, args.age_policy)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
