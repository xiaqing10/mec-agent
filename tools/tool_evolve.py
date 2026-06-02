import json
import logging
from datetime import datetime, timedelta

from langchain_core.tools import tool
from feedback_store import get_user_conversation_summary, get_feedback_stats, get_pinned_feedback

logger = logging.getLogger(__name__)


def _get_users_config() -> dict:
    try:
        from config import AVAILABLE_USERS
        return AVAILABLE_USERS
    except (ImportError, AttributeError):
        return {}


_USERS_DISPLAY = {}


def _user_display_name(user_id: str) -> str:
    if not _USERS_DISPLAY:
        cfg = _get_users_config()
        for uid, info in cfg.items():
            _USERS_DISPLAY[uid] = info.get("name", uid)
    return _USERS_DISPLAY.get(user_id, user_id)


@tool
def generate_improvement_report(days: int = 3) -> str:
    """生成用户反馈改进报告，基于用户评价数据进行统计和LLM分析，给出优化建议。

    数据来源：feedback.db（用户满意/不满意评价、pin标记的待优化项）。
    每次调用会自动汇总最近 N 天全部用户的反馈数据。

    Args:
        days: 统计最近几天的数据（默认3天），传0则统计全部历史数据
    """
    try:
        if days > 0:
            users_data = get_user_conversation_summary(hours=days * 24)
        else:
            users_data = get_user_conversation_summary(hours=87600)

        pinned = get_pinned_feedback(limit=200)

        all_stats = get_feedback_stats()

        curr_hour = days * 24 if days > 0 else 87600
        from datetime import datetime, timedelta
        since = (datetime.now() - timedelta(hours=curr_hour)).isoformat()
    except Exception as e:
        return json.dumps({"error": f"读取反馈数据失败: {e}"}, ensure_ascii=False)

    if not users_data and not pinned:
        return "📊 **近{}天暂无用户反馈数据**，无需生成改进报告。".format(days)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# 📊 用户反馈改进报告",
        f"生成时间: {now_str}",
        f"统计范围: 最近{days}天" if days > 0 else "统计范围: 全部历史数据",
        "",
        "---",
        "",
        "## 一、总体概览",
        "",
    ]

    s = all_stats
    lines.append(f"- 总反馈数: **{s['total']}** 条")
    rated = s["satisfied"] + s["partial"] + s["unsatisfied"]
    if rated > 0:
        satisfaction_rate = s["satisfied"] / rated * 100
        unsatisfied_rate = s["unsatisfied"] / rated * 100
        lines.append(f"- 👍 有帮助: **{s['satisfied']}** 条 ({satisfaction_rate:.1f}%)")
        lines.append(f"- 🤔 部分解决: **{s['partial']}** 条")
        lines.append(f"- 👎 没帮助: **{s['unsatisfied']}** 条 ({unsatisfied_rate:.1f}%)")
    lines.append(f"- ⏳ 待评价: **{s['pending']}** 条")
    lines.append("")

    if pinned:
        lines.append(f"- 📌 待优化项（管理员标记）: **{len(pinned)}** 条")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 二、各用户反馈")
    lines.append("")

    if users_data:
        lines.append("| 用户 | 总反馈 | 👍有帮助 | 🤔部分解决 | 👎没帮助 | 负面率 | 最近活跃 |")
        lines.append("|------|--------|----------|------------|----------|--------|----------|")
        for u in users_data:
            uid = u["user_id"]
            display = _user_display_name(uid)
            total = u["total"]
            sat = u["satisfied"]
            part = u["partial"]
            unsat = u["unsatisfied"]
            negative_rate = f"{unsat / total * 100:.0f}%" if total > 0 else "-"
            last_active = u["last_active"][:16] if u.get("last_active") else "-"
            lines.append(
                f"| {display} | {total} | {sat} | {part} | {unsat} | {negative_rate} | {last_active} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 三、具体负面反馈详情")
    lines.append("")

    negative_found = False
    for u in users_data:
        uid = u["user_id"]
        display = _user_display_name(uid)
        bad_convs = [
            c for c in u["conversations"]
            if c.get("rating") in ("unsatisfied", "partial")
        ]
        if not bad_convs:
            continue
        negative_found = True
        lines.append(f"### {display}")
        lines.append("")
        for c in bad_convs:
            intent = c.get("intent", "") or ""
            feedback_text = c.get("feedback_text", "") or ""
            rating_label = "👎" if c.get("rating") == "unsatisfied" else "🤔"
            actions_str = ", ".join(c.get("actions", [])[:5]) if c.get("actions") else "无"
            ts = c.get("created_at", "")[:16]
            lines.append(f"- **{rating_label} [{ts}]** {intent}")
            if actions_str and actions_str != "无":
                lines.append(f"  - 调用的工具: {actions_str}")
            if feedback_text:
                lines.append(f"  - 💬 用户补充说明: {feedback_text}")
            lines.append("")
        lines.append("")

    if not negative_found:
        lines.append("无负面反馈。\n")

    if pinned:
        lines.append("---")
        lines.append("")
        lines.append("## 四、待优化项（管理员标记）")
        lines.append("")
        for p in pinned:
            intent = p.get("intent", "") or ""
            uid = _user_display_name(p.get("user_id", ""))
            ts = p.get("created_at", "")[:16]
            lines.append(f"- 📌 [{ts}] ({uid}) {intent[:80]}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 五、LLM 改进建议")
    lines.append("")

    prompt_parts = ["请基于以下用户反馈数据，生成针对该MEC交通监测智能体的改进建议报告。"]
    prompt_parts.append("")

    if s["total"] > 0:
        prompt_parts.append(f"### 总体数据")
        prompt_parts.append(f"- 总反馈数: {s['total']}条")
        prompt_parts.append(f"- 有帮助: {s['satisfied']}条")
        prompt_parts.append(f"- 部分解决: {s['partial']}条")
        prompt_parts.append(f"- 没帮助: {s['unsatisfied']}条")
        prompt_parts.append("")

    if users_data:
        prompt_parts.append("### 各用户负面反馈")
        for u in users_data:
            if u["unsatisfied"] > 0 or u["partial"] > 0:
                display = _user_display_name(u["user_id"])
                bad = [c for c in u["conversations"] if c.get("rating") in ("unsatisfied", "partial")]
                for c in bad:
                    intent = c.get("intent", "") or ""
                    fb = c.get("feedback_text", "") or ""
                    tools_used = ", ".join(c.get("actions", [])[:5])
                    prompt_parts.append(f"- [{display}] {intent}")
                    if tools_used:
                        prompt_parts.append(f"  工具: {tools_used}")
                    if fb:
                        prompt_parts.append(f"  用户补充: {fb}")
        prompt_parts.append("")

    if pinned:
        prompt_parts.append("### 管理员标记的待优化项")
        for p in pinned:
            prompt_parts.append(f"- {p.get('intent', '')[:80]}")
        prompt_parts.append("")

    prompt_parts.append("请从以下维度提出改进建议：")
    prompt_parts.append("1. **Agent行为/提示词优化** — 是否system prompt描述不清导致工具选错/回答不准")
    prompt_parts.append("2. **工具代码优化** — 是否某个工具返回慢/超时/数据不全/过滤条件不够")
    prompt_parts.append("3. **状态管理优化** — 是否上下文丢失/遗忘前文/对话逻辑断裂")
    prompt_parts.append("4. **WebUI前端优化** — 是否交互路径不清晰/反馈不便/展示信息过多")
    prompt_parts.append("")
    prompt_parts.append("用中文、Markdown格式回复，每条建议标注优先级（高/中/低）。")

    llm_result = _llm_analysis("\n".join(prompt_parts),
                               "你是一个专业的产品和工程分析专家，基于用户反馈数据分析MEC智能体系统的改进方向。")
    lines.append(llm_result)

    return "\n".join(lines)


def _llm_analysis(prompt: str, system: str = "") -> str:
    from config import AVAILABLE_MODELS
    cfg = AVAILABLE_MODELS.get("deepseek-v4-flash", {})
    url = f"{cfg.get('base_url', 'https://ark.cn-beijing.volces.com/api/coding/v3')}/chat/completions"
    api_key = cfg.get("api_key", "")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": "deepseek-v4-flash",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    import urllib.request
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=90)
        data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"LLM 分析调用失败: {e}"