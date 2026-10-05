"""Fixed local fixtures; no user media, network services, or library DB writes."""
import gc
import json
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
OUT = ROOT / ".run/subtitle-speed-validation"
OUT.mkdir(parents=True, exist_ok=True)


def record(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)
    with (OUT / "tuning.jsonl").open("a") as f:
        f.write(json.dumps(value, ensure_ascii=False) + "\n")


def asr():
    import numpy as np
    from faster_whisper import WhisperModel, BatchedInferencePipeline
    with wave.open(str(ROOT / ".run/subtitle-validation/speech-16k.wav")) as f:
        raw = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    # Repeated fixed synthetic speech, 59 seconds, enough for several model windows.
    audio = np.tile(raw, 8)
    for threads, batch in [(2, 0), (4, 0), (4, 4), (6, 4)]:
        start = time.perf_counter()
        model = WhisperModel(str(ROOT / "data/models/whisper/small"), device="cpu",
                             compute_type="int8", cpu_threads=threads, local_files_only=True)
        loaded = time.perf_counter()
        engine = BatchedInferencePipeline(model=model) if batch else model
        kwargs = dict(language="en", vad_filter=True, beam_size=3, condition_on_previous_text=False)
        if batch:
            kwargs.update(batch_size=batch, without_timestamps=False)
        segments, info = engine.transcribe(audio, **kwargs)
        segments = list(segments)
        end = time.perf_counter()
        record(dict(kind="asr", threads=threads, batch=batch, beam=3,
                    audio_seconds=len(audio)/16000, load=loaded-start, inference=end-loaded,
                    total=end-start, cues=len(segments),
                    text=" ".join(s.text.strip() for s in segments),
                    valid=all(s.start >= 0 and s.end > s.start and s.end <= len(audio)/16000+1 for s in segments)))
        del segments, engine, model
        gc.collect()


def translation():
    import ctranslate2
    import sentencepiece
    root = ROOT / "data/models/translation/nllb-600m-int8"
    tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(root / "sentencepiece.bpe.model"))
    texts = [
        "This video explains how to organize a personal media library.",
        "You can generate subtitles and translate them into another language.",
        "The original video and its audio remain unchanged.",
        "Select a subtitle language from the menu at the bottom of the player.",
        "The model runs on your own computer without sending text to a server.",
        "Long recordings are processed in smaller sections.",
        "The timeline should remain accurate after translation.",
        "You can pause a video, change the playback speed and adjust the volume.",
        "The download will continue in the background.",
        "When a task finishes, a notification appears in the corner.",
        "Please check the source language before translating an imported subtitle.",
        "You can edit the translated text and export a subtitle file.",
    ] * 4
    tokens = [["eng_Latn", *tokenizer.encode(t, out_type=str), "</s>"] for t in texts]
    for intra, inter, window, sub in [(2, 1, 8, 8), (4, 1, 16, 16), (2, 2, 32, 8), (4, 2, 32, 8)]:
        start = time.perf_counter()
        model = ctranslate2.Translator(str(root), device="cpu", compute_type="int8",
                                      intra_threads=intra, inter_threads=inter)
        loaded = time.perf_counter()
        output = []
        for offset in range(0, len(tokens), window):
            group = tokens[offset:offset+window]
            results = model.translate_batch(group, target_prefix=[["zho_Hans"] for _ in group],
                       beam_size=4, max_input_length=512, max_decoding_length=512, max_batch_size=sub)
            output.extend(tokenizer.decode(r.hypotheses[0][1:]).strip() for r in results)
        end = time.perf_counter()
        record(dict(kind="translation", intra=intra, inter=inter, window=window, sub=sub,
                    beam=4, cues=len(texts), load=loaded-start, inference=end-loaded, total=end-start,
                    output=output, valid=len(output)==len(texts) and all(output)))
        del model
        gc.collect()


if __name__ == "__main__":
    (asr if sys.argv[1] == "asr" else translation)()
