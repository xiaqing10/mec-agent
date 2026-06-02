import asyncio
import logging

logger = logging.getLogger(__name__)

_feedback_queue = asyncio.Queue()
_worker_task = None


async def _feedback_worker():
    while True:
        task = await _feedback_queue.get()
        try:
            await task()
        except Exception as e:
            logger.warning("后台反馈任务失败: %s", e)
        finally:
            _feedback_queue.task_done()


async def _ensure_worker():
    global _worker_task
    if _worker_task is None:
        _worker_task = asyncio.ensure_future(_feedback_worker())


def _extract_intent_and_score(user_msg: str, ai_msg: str, tool_actions: list) -> tuple:
    """Extract intent and self-evaluate correctness using LLM (runs in executor)."""
    intent = user_msg[:50]
    auto_score = None

    if not tool_actions:
        return intent, auto_score

    try:
        from langchain_openai import ChatOpenAI
        from config import AVAILABLE_MODELS
        _default_cfg = next(iter(AVAILABLE_MODELS.values()))
        LLM_MODEL = next(iter(AVAILABLE_MODELS))
        LLM_API_KEY = _default_cfg["api_key"]
        LLM_BASE_URL = _default_cfg["base_url"]

        background_llm = ChatOpenAI(
            model=LLM_MODEL, api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL, temperature=0.1,
            max_retries=0, timeout=15,
        )

        intent_resp = background_llm.invoke([
            ("human", "请用一句话概括用户本次对话中用户的意图（20字以内），仅输出概括内容：\n"
             f"用户消息: {user_msg[:200]}\n"
             f"AI回复: {ai_msg[:200] if ai_msg else ''}")
        ])
        intent = intent_resp.content.strip()[:100]

        score_resp = background_llm.invoke([
            ("human", "请评估本次诊断是否成功完成。仅输出0-10的整数分数（10=完美）:\n"
             f"用户意图: {intent}\n"
             f"AI回复: {ai_msg[:500] if ai_msg else ''}")
        ])
        score_text = score_resp.content.strip()
        auto_score = max(0, min(10, int(score_text)))
    except Exception:
        pass

    return intent, auto_score


async def enqueue_post_process(session_id, username, user_msg, ai_msg, tool_called, tool_actions, config, agent):
    await _ensure_worker()

    async def _task():
        intent = user_msg[:50]
        auto_score = None

        if tool_called:
            intent, auto_score = await asyncio.get_event_loop().run_in_executor(
                None, _extract_intent_and_score, user_msg, ai_msg, tool_actions
            )

        try:
            if tool_called:
                from feedback_store import create_feedback_record
                create_feedback_record(
                    session_id,
                    user_id=username or session_id,
                    intent=intent,
                    actions=tool_actions,
                    auto_correctness=auto_score,
                )
        except Exception as e:
            logger.warning("保存反馈记录失败: %s", e)

        try:
            if username:
                from user_memory_store import extract_memories_from_conversation
                extract_memories_from_conversation(username, user_msg, ai_msg, intent)
        except Exception as e:
            logger.debug("提取用户记忆失败: %s", e)

    await _feedback_queue.put(_task)
