"""
wisp-mail classification — one email, one isolated LLM call.

Core design principle (see the design doc, section 01): every email gets its
own independent content-understanding pass. Each call below builds a FRESH
messages list — a static system prompt plus this one email's own content —
and nothing else. No conversation history, no accumulated state from prior
emails in the same run. This is deliberate: the whole point is that one
email's judgment must never leak into another's.
"""

from __future__ import annotations

import json
import re

from agent_core import call_llm

# Starter taxonomy — deliberately small and revisable. Not locked; if real
# mail keeps landing in "其他/不确定", that's a signal to add a category, not
# a signal the model is failing.
#
# 验证码/资讯通知/广告推广 are the categories Phase 2's rule table is expected
# to map straight to "delete" (验证码, 资讯通知 are junk-after-read candidates
# per the user; 广告推广 already was). That makes precision at these
# boundaries a correctness requirement, not a nice-to-have — a false positive
# here doesn't just mis-sort, it deletes something that shouldn't be deleted.
# The prompt below spells out the boundaries explicitly rather than trusting
# category names to be self-explanatory to the model.
CATEGORIES = [
    "社交通知",    # a person's actual activity on a social platform — connection
                  # request, like, comment, direct message. Not the platform's own
                  # operational/informational notices.
    "资讯通知",    # informational notices that don't require action and aren't
                  # financial/security-sensitive — market/analyst updates, exchange
                  # holiday notices, product/policy update announcements, completed-
                  # action confirmations (e.g. "verification complete") with no
                  # figures or security implications attached.
    "广告推广",    # marketing, newsletters, promotions
    "财务账单",    # bank/brokerage statements, bills, invoices — anything with
                  # concrete amounts, balances, or payment/invoice details
    "安全提醒",    # account security alerts, suspicious-login notifications, fraud
                  # warnings — the email is reporting a security-relevant EVENT
    "验证码",      # the email's actual purpose is to hand you a one-time code /
                  # OTP to type in somewhere right now. Not a "verification
                  # complete" confirmation — that has no code left to use.
    "出行预订",    # flight/hotel/travel booking confirmations
    "个人邮件",    # real correspondence from a person
    "其他",       # doesn't fit cleanly — kept as uncertain, never auto-actioned
]

_SYSTEM_PROMPT = (
    "你是一个邮件内容分类器。你会收到一封邮件的发件人、主题和正文，"
    "需要判断它属于以下哪个类别，并给出置信度和简短理由。\n\n"
    f"类别（必须从中选一个）：{ '、'.join(CATEGORIES) }\n\n"
    "判断依据只能是这封邮件本身的实际内容，不能因为发件人domain是某个平台"
    "就直接归类——同一个发件人既可能发无意义通知，也可能发真正重要的安全提醒，"
    "必须看这一封具体写了什么。\n\n"
    "几个容易混淆的类别边界，请严格区分（这些类别后续会被用来做批量删除，"
    "分错会导致误删）：\n"
    "- 验证码 vs 安全提醒 vs 资讯通知：只有邮件里包含一个当前可以拿去使用的"
    "一次性验证码/OTP，才算验证码类；如果邮件只是告诉你\"验证已完成\"\"账户已核实\""
    "这类结果性确认、但没有码可用，归入资讯通知；如果邮件是在报告一个安全相关的"
    "事件（异常登录、密码被修改、可疑活动），归入安全提醒。\n"
    "- 资讯通知 vs 财务账单：只要邮件出现具体金额、余额、账单或需要核对的交易明细，"
    "一律归入财务账单，不能因为它看起来像\"系统通知\"就归入资讯通知。\n"
    "- 资讯通知 vs 社交通知：社交通知专指某个具体的人对你做了什么（加好友、点赞、"
    "评论、私信）；平台自己发的市场行情、条款变更、假期公告等运营性通知属于资讯通知，"
    "即使发件人是同一个社交/金融平台。\n\n"
    '只返回 JSON，不要其他文字：{"category": "...", "confidence": 0.0到1.0之间的数字, "reasoning": "一句话"}'
)


def classify_email(sender: str, subject: str, body: str, date: str = "") -> dict:
    """Classify one email. Returns:
        {"category": str, "confidence": float, "reasoning": str}
    or, if the model's output couldn't be parsed:
        {"category": "其他", "confidence": 0.0, "reasoning": "解析失败: ..."}

    Stateless by construction — builds messages from scratch every call.
    """
    user_content = (
        f"发件人: {sender}\n"
        f"主题: {subject}\n"
        + (f"日期: {date}\n" if date else "")
        + f"正文:\n{(body or '').strip()[:1500]}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        raw = call_llm(messages, max_tokens=256, json_mode=True)
        data = _parse_json(raw)
        category = str(data.get("category", "")).strip()
        if category not in CATEGORIES:
            category = "其他"
        confidence = float(data.get("confidence", 0.0) or 0.0)
        confidence = max(0.0, min(1.0, confidence))
        reasoning = str(data.get("reasoning", "")).strip()
        return {"category": category, "confidence": confidence, "reasoning": reasoning}
    except Exception as e:
        return {"category": "其他", "confidence": 0.0, "reasoning": f"解析失败: {e}"}


def _parse_json(raw: str) -> dict:
    """Best-effort JSON parse — a 9B model under json_mode is usually clean,
    but strip markdown fences if they slip through."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)
