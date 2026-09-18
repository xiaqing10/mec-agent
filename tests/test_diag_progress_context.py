import asyncio

from tools._shared import (
    get_diag_progress_callback,
    reset_diag_progress_callback,
    set_diag_progress_callback,
)


def test_diag_progress_callback_is_context_scoped():
    seen = []

    async def run():
        token = set_diag_progress_callback(lambda *args: seen.append("outer"))
        try:
            async def child():
                child_token = set_diag_progress_callback(lambda *args: seen.append("child"))
                try:
                    get_diag_progress_callback()("x", "ok", "child")
                finally:
                    reset_diag_progress_callback(child_token)

            await asyncio.gather(child(), asyncio.sleep(0))
            get_diag_progress_callback()("x", "ok", "outer")
        finally:
            reset_diag_progress_callback(token)

    asyncio.run(run())
    assert seen == ["child", "outer"]
