"""Translate immutable generated batches while recognition continues in the same job."""

import asyncio
import contextlib

from app.services.subtitle_partial import ProgressiveTrack


async def generate_and_translate(payload, progress):
    from app.services import subtitles
    from app.workers.tasks import JobCanceled

    parameters = payload["translation"]
    queue = asyncio.Queue()
    originals = []
    translated = {}
    publisher = None
    lock = asyncio.Lock()
    errors = []
    generation_fraction = 0
    high_water = 0
    workers_count = subtitles.API_TRANSLATION_CONCURRENCY if parameters["provider"] == "api" else 1
    batch_size = subtitles.API_TRANSLATION_BATCH_SIZE if parameters["provider"] == "api" else 15

    async def report(done=None, total=None, message=""):
        nonlocal high_water
        if done is None:
            await progress(None, None, "")
            return
        percent = min(99, int(generation_fraction * 70 + len(translated) / max(1, len(originals)) * 29))
        high_water = max(high_water, percent)
        await progress(high_water, 100, f"已生成 {len(originals)} 条 · 已翻译 {len(translated)} 条" + (f" · {message}" if message else ""))

    async def generation_progress(done, total, message=""):
        nonlocal generation_fraction
        if done is not None and total:
            generation_fraction = min(1, done / total)
        await report(done, total, message)

    async def checkpoint(cues, language, track_id):
        nonlocal publisher
        if publisher is None:
            publisher = ProgressiveTrack(subtitles.SessionLocal, payload["video_id"],
                f"{parameters['target_language']} · {parameters['model']}", parameters["target_language"],
                parameters["provider"], parameters["model"], source_id=track_id, job_id=payload.get("_job_id"))
        start = len(originals)
        fresh = [dict(cue) for cue in cues[start:]]
        originals.extend(fresh)
        # Flush even a short checkpoint so translation can begin with the first speech.
        for offset in range(0, len(fresh), batch_size):
            queue.put_nowait((start + offset, fresh[offset:offset + batch_size], language, track_id))
        await report(0, 1)

    class BatchPublisher:
        def __init__(self, offset, batch):
            self.offset, self.batch = offset, batch

        async def publish(self, cues, source_cues=None, **kwargs):
            async with lock:
                for cue, original in zip(cues, source_cues):
                    translated[self.offset + self.batch.index(original)] = dict(cue)
                indices = sorted(translated)
                result = await publisher.publish([translated[index] for index in indices],
                                                 [originals[index] for index in indices])
                await report(0, 1)
                return result

    async def worker():
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                if errors:
                    continue
                offset, batch, language, track_id = item
                await subtitles.translate({**parameters, "track_id": track_id}, report,
                    cues_override=batch, publisher_override=BatchPublisher(offset, batch), final=False)
            except (JobCanceled, asyncio.CancelledError):
                raise
            except Exception as exc:
                # Preserve generation: a translation failure must not discard future source speech.
                errors.append(exc)
            finally:
                queue.task_done()

    async def monitor_generation():
        generation = asyncio.create_task(subtitles.generate_source(payload, generation_progress, on_checkpoint=checkpoint))
        try:
            while not generation.done():
                finished, _ = await asyncio.wait([generation, *workers], return_when=asyncio.FIRST_COMPLETED)
                # A canceled worker cancels recognition too, including an in-flight ASR request.
                for task in finished:
                    if task is not generation:
                        task.result()
            return generation.result()
        finally:
            if not generation.done():
                generation.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await generation

    workers = [asyncio.create_task(worker()) for _ in range(workers_count)]
    try:
        source_result = await monitor_generation()
        generation_fraction = 1
        for _ in workers:
            queue.put_nowait(None)
        await asyncio.gather(*workers)
        if errors:
            raise ValueError(f"原字幕已生成，自动翻译失败，已完成片段已保留：{errors[0]}") from errors[0]
        if len(translated) != len(originals) or publisher is None:
            raise ValueError("自动翻译未覆盖全部字幕，原文与已完成译文已保留")
        result = await publisher.publish([translated[index] for index in range(len(originals))], originals, final=True)
        return {**result, "source_track_id": source_result["track_id"], "generated_cue_count": len(originals)}
    finally:
        for worker_task in workers:
            if not worker_task.done():
                worker_task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
