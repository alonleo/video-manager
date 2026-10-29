"""Small offline translation model; inference runs in a cancelable child process."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

MODEL_ID = "nllb-600m-int8"
REPOSITORY = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
REVISION = "302d78f00e6fdb50a1064059df7c392b735e9d05"
FILES = {
    "model.bin": 622595991,
    "sentencepiece.bpe.model": 4852054,
    "shared_vocabulary.txt": 2568098,
    "config.json": 159,
    "README.md": 3435,
}
HASHES = {
    "model.bin": "ed1beaf75134de7505315a5223162f56acff397eff6b50638a500d3936fe707b",
    "sentencepiece.bpe.model": "14bb8dfb35c0ffdea7bc01e56cea38b9e3d5efcdcb9c251d6b40538e1aab555a",
}
LANGUAGES = {
    "eng_Latn": ("en", "eng", "english"),
    "zho_Hans": ("zh", "zh-cn", "zh-hans", "zh-sg", "zho", "chi", "chinese"),
    "zho_Hant": ("zh-tw", "zh-hant", "zh-hk"),
    "jpn_Jpan": ("ja", "jpn", "japanese"),
    "kor_Hang": ("ko", "kor", "korean"),
    "fra_Latn": ("fr", "fra", "fre", "french"),
    "deu_Latn": ("de", "deu", "ger", "german"),
    "spa_Latn": ("es", "spa", "spanish"),
    "por_Latn": ("pt", "por", "portuguese"),
    "rus_Cyrl": ("ru", "rus", "russian"),
    "ita_Latn": ("it", "ita", "italian"),
    "arb_Arab": ("ar", "ara", "arabic"),
    "vie_Latn": ("vi", "vie", "vietnamese"),
    "tha_Thai": ("th", "tha", "thai"),
    "ind_Latn": ("id", "ind", "indonesian"),
}


def language_code(value: str) -> str:
    for code, aliases in LANGUAGES.items():
        if value.lower() in aliases or value == code:
            return code
    raise ValueError("无法确定本地翻译语言，请在翻译区域明确选择支持的原文和目标语言")


def runtime_ready() -> bool:
    return all(importlib.util.find_spec(m) for m in ("ctranslate2", "sentencepiece"))


def model_dir(model: str) -> Path:
    if model != MODEL_ID:
        raise ValueError("未知本地翻译模型")
    from app.config import settings

    return settings.data_dir / "models" / "translation" / model


def model_ready(model: str) -> bool:
    root = model_dir(model)
    try:
        return (root / ".ready").read_text() == REVISION and all(
            (root / name).stat().st_size == size for name, size in FILES.items()
        )
    except OSError:
        return False


def emit(done, total, message):
    print(json.dumps(dict(done=done, total=total, message=message)), flush=True)


def load_model(root):
    import ctranslate2
    import sentencepiece

    tokenizer = sentencepiece.SentencePieceProcessor(
        model_file=str(root / "sentencepiece.bpe.model")
    )
    available = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    workers = 2 if (available or 1) >= 2 else 1
    threads = max(1, min(4, (available or 1) // workers))
    model = ctranslate2.Translator(
        str(root), device="cpu", compute_type="int8", inter_threads=workers, intra_threads=threads
    )
    return model, tokenizer


def download(root):
    from huggingface_hub import hf_hub_download

    root.mkdir(parents=True, exist_ok=True)
    (root / ".ready").unlink(missing_ok=True)
    total, done = sum(FILES.values()), 0
    for name, size in FILES.items():
        emit(done, total, f"下载轻量翻译模型：{name}")
        hf_hub_download(REPOSITORY, name, revision=REVISION, local_dir=str(root))
        if (root / name).stat().st_size != size:
            raise ValueError("模型文件大小不匹配")
        done += size
    emit(total, total, "校验模型文件")
    for name, expected in HASHES.items():
        with (root / name).open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != expected:
                (root / name).unlink()
                raise ValueError("模型校验失败，请重新下载")
    load_model(root)
    (root / ".ready").write_text(REVISION)
    emit(total, total, "本地翻译模型已就绪")


def token_chunks(tokens, limit=400):
    """Keep every source token; leave room for NLLB's language and end tokens."""
    while len(tokens) > limit:
        end = limit
        # Avoid cutting in the middle of a word when a nearby boundary is available.
        while end > limit - 40 and not tokens[end].startswith("▁"):
            end -= 1
        if end == limit - 40:
            end = limit
        yield tokens[:end]
        tokens = tokens[end:]
    if tokens:
        yield tokens


def translate_texts(root, texts, source, target, progress=emit, *, cache_path=None, checkpoint=None):
    from collections import Counter

    from app.services.subtitle_translation_memory import TranslationMemory

    source, target = language_code(source), language_code(target)
    if source == target:
        return list(texts)
    fingerprints = []
    if cache_path is not None:
        for name in ("model.bin", "sentencepiece.bpe.model"):
            try:
                stat = (root / name).stat()
                fingerprints.append((name, stat.st_size, stat.st_mtime_ns))
            except OSError:
                fingerprints.append((name, None))
    memory = TranslationMemory(
        cache_path,
        {
            "version": "nllb-beam4-chunks400-v1",
            "revision": REVISION,
            "model": str(root.resolve()),
            "files": fingerprints,
            "source": source,
            "target": target,
        },
    )
    try:
        counts = Counter(texts)
        translated = {}
        pending = []
        for text in counts:
            cached = memory.get(text)
            if cached is None:
                pending.append(text)
            else:
                translated[text] = cached
        completed = sum(counts[text] for text in translated)
        def publish():
            if checkpoint:
                indices = [i for i, text in enumerate(texts) if text in translated]
                checkpoint(indices, [translated[texts[i]] for i in indices])
        publish()
        if not pending:
            progress(completed, len(texts), "已复用本地翻译结果")
            return [translated[text] for text in texts]
        progress(completed, len(texts), "载入本地轻量翻译模型")
        model, tokenizer = load_model(root)
        # Translate each exact sentence once; keep independent timing for every cue.
        # CT2 sorts each window by length and runs sub-batches on two shared-weight workers.
        for offset in range(0, len(pending), 32):
            batch = pending[offset : offset + 32]
            chunks, owners = [], []
            for index, text in enumerate(batch):
                for tokens in token_chunks(tokenizer.encode(text, out_type=str)):
                    chunks.append([source, *tokens, "</s>"])
                    owners.append(index)
            parts = [[] for _ in batch]
            for start in range(0, len(chunks), 32):
                group = chunks[start : start + 32]
                results = model.translate_batch(
                    group,
                    target_prefix=[[target] for _ in group],
                    beam_size=4,
                    max_input_length=512,
                    max_decoding_length=512,
                    max_batch_size=8,
                )
                if len(results) != len(group):
                    raise ValueError("本地译文数量与原字幕不一致")
                for index, result in enumerate(results):
                    tokens = result.hypotheses[0]
                    if len(tokens) >= 512:
                        raise ValueError("译文超过模型长度限制，请缩短原字幕片段后重试")
                    text = tokenizer.decode(tokens[1:]).strip()
                    if not text:
                        raise ValueError("模型返回空译文")
                    parts[owners[start + index]].append(text)
            pairs = []
            for original, part in zip(batch, parts, strict=True):
                text = " ".join(part).strip()
                if not text or len(text) > 4000:
                    raise ValueError("译文为空或过长，请缩短原字幕片段后重试")
                translated[original] = text
                pairs.append((original, text))
                completed += counts[original]
            memory.put_many(pairs)
            publish()
            progress(completed, len(texts), f"本地翻译 {completed}/{len(texts)} 条字幕")
        return [translated[text] for text in texts]
    finally:
        memory.close()


def main():
    action, root, *args = sys.argv[1:]
    root = Path(root)
    if action == "download":
        download(root)
    elif action == "translate":
        input_path, output_path = map(Path, args)
        data = json.loads(input_path.read_text())
        def checkpoint(indices, texts):
            path = output_path.with_suffix(".partial.json")
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"indices": indices, "texts": texts}), encoding="utf-8")
            temporary.replace(path)
        result = translate_texts(
            root,
            data["texts"],
            data["source"],
            data["target"],
            cache_path=os.environ.get("SUBTITLE_TRANSLATION_CACHE"),
            checkpoint=checkpoint,
        )
        output_path.write_text(json.dumps(result, ensure_ascii=False))
    else:
        raise ValueError("未知操作")


if __name__ == "__main__":
    main()
