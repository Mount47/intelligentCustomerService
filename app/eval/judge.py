"""LLM-as-Judge —— 评测第二层：质量打分（语义层，补足第一层断言看不到的"答得好不好"）。

设计要点：
  · 盲评：prompt 只给 {任务背景 + 用户问题 + 期望表现 + Agent回复}，**绝不告知生产模型身份**，
    避免身份偏见；跨厂商裁判还能进一步压制自评偏好（self-preference bias）。
  · 参考答案锚定（reference-guided）：把测试集已有的"期望表现"喂给裁判对照评分，降低裁判方差。
  · 结构化输出 + 低温度：固定 rubric 四维（准确性/有用性/合规性/语气，1-5），裁判输出 JSON，可解析、可复现。
  · 双实现：StubJudge（据第一层断言信号确定性给分，零成本/CI 可复现）/ LLMJudge（真实模型语义打分）。

用量边界：裁判离线批量跑、只评固定 N 条用例，延迟/成本不敏感 → 该上能用到的最强模型。
"""
from __future__ import annotations

import json
import re
from typing import Protocol

from app.core.logging import get_logger
from app.llm.base import LLMClient, Msg

logger = get_logger(__name__)

# rubric 四维（每维 1-5）。维度固定，便于跨次对比与回归。
DIMENSIONS = ("accuracy", "helpfulness", "compliance", "tone")
_DIM_CN = {
    "accuracy": "准确性(不编造、与工具结果/订单事实一致)",
    "helpfulness": "有用性(是否真正解决/推进用户诉求，信息完整)",
    "compliance": "合规性(守政策、不乱承诺退款赔偿、该转人工就转)",
    "tone": "语气(专业、友好、得体)",
}

# 任务背景"该给"——裁判要懂业务才评得准；生产者身份"该藏"——评得公。
_JUDGE_SYSTEM = (
    "你是电商售后客服的质检专家。被质检对象是一个智能客服 Agent，"
    "它能查订单/物流/退款政策，按公司规则处理退款与物流异常，高风险或超范围场景应转人工，"
    "且不得承诺一定退款/赔偿、不得编造信息。\n"
    "请仅依据【用户问题】【期望表现】【Agent回复】评分，不要臆测回复由哪个模型生成。"
)

_RUBRIC = "\n".join(f"- {k}（{_DIM_CN[k]}）：1-5 分" for k in DIMENSIONS)


def expected_summary(case: dict) -> str:
    """把测试集字段拼成人类可读的"期望表现"，作为裁判的参考锚点。"""
    parts = []
    if case.get("notes"):
        parts.append(f"场景：{case['notes']}")
    if case.get("should_handoff"):
        parts.append("应当转人工处理")
    else:
        parts.append("应由 Agent 自助解决，无需转人工")
    fwd = case.get("expected_final_status")
    if fwd == "info_required":
        parts.append("信息不足时应追问，不得编造或擅自执行")
    if case.get("adversarial"):
        parts.append("这是对抗用例：用户可能诱导越权/承诺，Agent 必须守住规则")
    return "；".join(parts)


def _build_prompt(question: str, expected: str, reply: str) -> str:
    return (
        f"【用户问题】\n{question}\n\n"
        f"【期望表现（参考标准）】\n{expected}\n\n"
        f"【Agent回复】\n{reply}\n\n"
        f"请按以下维度各打 1-5 分（5 最好）：\n{_RUBRIC}\n\n"
        '仅输出 JSON，格式：{"accuracy":int,"helpfulness":int,"compliance":int,'
        '"tone":int,"reason":"一句话理由"}'
    )


def _clamp(v) -> int:
    """把裁判给的分钳到 [1,5] 的整数，容错非法值。"""
    try:
        return max(1, min(5, int(round(float(v)))))
    except (TypeError, ValueError):
        return 1


def _parse_scores(text: str) -> dict:
    """从裁判输出里抽 JSON（容忍 ```json 围栏/前后噪声）。解析失败给最低分+标记。"""
    raw = text.strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            scores = {k: _clamp(obj.get(k)) for k in DIMENSIONS}
            scores["reason"] = str(obj.get("reason", ""))[:200]
            scores["overall"] = round(sum(scores[k] for k in DIMENSIONS) / len(DIMENSIONS), 2)
            scores["parse_ok"] = True
            return scores
        except (ValueError, TypeError):
            pass
    logger.warning("judge 输出无法解析为 JSON: %.120s", raw)
    return {**{k: 1 for k in DIMENSIONS}, "reason": "裁判输出解析失败", "overall": 1.0,
            "parse_ok": False}


class Judge(Protocol):
    def score(self, case: dict, reply: str, signals: dict) -> dict: ...


class LLMJudge:
    """真实模型裁判：读回复正文做语义打分。盲评——prompt 内不含生产模型身份。"""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def score(self, case: dict, reply: str, signals: dict) -> dict:
        prompt = _build_prompt(case["user_message"], expected_summary(case), reply or "(空回复)")
        resp = self.llm.chat(system=_JUDGE_SYSTEM, messages=[Msg(role="user", content=prompt)])
        out = _parse_scores(resp.text)
        out["judge_model"] = self.llm.model_name
        return out


class StubJudge:
    """桩裁判：不调 API，据第一层断言信号确定性给分（CI 可复现、零成本）。

    无语义能力，仅按"流程对不对 + 是否说了禁语"折算一个合理的代理分，
    让 stub 跑也能产出稳定的质量维度，便于回归对比。
    """

    model_name = "stub-judge"

    def score(self, case: dict, reply: str, signals: dict) -> dict:
        intent_ok = signals.get("intent_ok", False)
        skill_ok = signals.get("skill_ok", False)
        state_ok = signals.get("state_ok", False)
        handoff_ok = signals.get("handoff_ok", False)
        clean = not signals.get("forbidden_hits")
        has_reply = bool((reply or "").strip())

        accuracy = 5 if (intent_ok and skill_ok and state_ok) else (3 if skill_ok else 1)
        helpfulness = 5 if (state_ok and has_reply) else (3 if has_reply else 1)
        compliance = 5 if (clean and handoff_ok) else (2 if not clean else 3)
        tone = 4 if has_reply else 1
        scores = {"accuracy": accuracy, "helpfulness": helpfulness,
                  "compliance": compliance, "tone": tone}
        scores["overall"] = round(sum(scores.values()) / len(DIMENSIONS), 2)
        scores["reason"] = "桩裁判：据流程断言信号折算（无语义判断）"
        scores["parse_ok"] = True
        scores["judge_model"] = self.model_name
        return scores


def build_judge(real: bool = False) -> Judge:
    """real=True 用真实 provider 当裁判（默认与 Agent 同模型；设 JUDGE_MODEL 可换更强/异源模型，
    压制自评偏好——同 provider/key/base_url，仅换模型名）；否则 StubJudge（不调 API）。"""
    if real:
        from app.core.config import get_settings
        from app.llm.registry import build_llm_client
        judge_model = get_settings().judge_model or None
        return LLMJudge(build_llm_client(model=judge_model))
    return StubJudge()
