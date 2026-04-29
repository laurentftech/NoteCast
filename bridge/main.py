import asyncio
import contextlib
import signal

import harvester
import rss_transformer


async def _run_all():
    stop_event = asyncio.Event()

    def _request_stop():
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _request_stop)

    tasks = [
        asyncio.create_task(harvester.run_http_server(), name="http_server"),
        asyncio.create_task(harvester.main_async(), name="harvester"),
        asyncio.create_task(rss_transformer.main_async(), name="rss_transformer"),
    ]

    wait_stop_task = asyncio.create_task(stop_event.wait(), name="stop_event")
    done, pending = await asyncio.wait(
        [*tasks, wait_stop_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    if wait_stop_task in done:
        for task in tasks:
            task.cancel()
    else:
        wait_stop_task.cancel()
        for task in done:
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                raise exc
        for task in tasks:
            if task not in done:
                task.cancel()

    for task in pending:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)


def main():
    asyncio.run(_run_all())


if __name__ == "__main__":
    main()