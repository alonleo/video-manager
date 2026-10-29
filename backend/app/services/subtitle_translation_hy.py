"""Official HY-MT1.5 GGUF models through local Ollama, using Tencent's templates."""

from app.services.subtitle_text import validate_cues

MODELS = {
    "hf.co/tencent/HY-MT1.5-1.8B-GGUF:Q4_K_M": {
        "name": "HY-MT1.5 1.8B · 轻量专用翻译", "size_mb": 1130,
        "description": "腾讯官方 GGUF · Q4_K_M。专用多语言翻译模型，逐条翻译字幕并保留时间轴。",
        "hardware_hint": "建议至少 4 GB 可用内存；需支持 Hunyuan 模型的新版 Ollama。",
        "source_url": "https://huggingface.co/tencent/HY-MT1.5-1.8B-GGUF",
    },
    "hf.co/tencent/HY-MT1.5-7B-GGUF:Q4_K_M": {
        "name": "HY-MT1.5 7B · 专用翻译", "size_mb": 4620,
        "description": "腾讯官方 GGUF · Q4_K_M。更大容量的专用翻译模型，适合尝试复杂句与混合语言。",
        "hardware_hint": "建议至少 8 GB 可用内存；需支持 Hunyuan 模型的新版 Ollama。",
        "source_url": "https://huggingface.co/tencent/HY-MT1.5-7B-GGUF",
    },
}
LANGUAGES = {
    "zh": ("Chinese", "中文"), "zh-hant": ("Traditional Chinese", "繁体中文"),
    "en": ("English", "英语"), "fr": ("French", "法语"),
    "pt": ("Portuguese", "葡萄牙语"), "es": ("Spanish", "西班牙语"),
    "ja": ("Japanese", "日语"), "tr": ("Turkish", "土耳其语"),
    "ru": ("Russian", "俄语"), "ar": ("Arabic", "阿拉伯语"),
    "ko": ("Korean", "韩语"), "th": ("Thai", "泰语"),
    "it": ("Italian", "意大利语"), "de": ("German", "德语"),
    "vi": ("Vietnamese", "越南语"), "ms": ("Malay", "马来语"),
    "id": ("Indonesian", "印尼语"), "tl": ("Filipino", "菲律宾语"),
    "hi": ("Hindi", "印地语"), "pl": ("Polish", "波兰语"),
    "cs": ("Czech", "捷克语"), "nl": ("Dutch", "荷兰语"),
    "km": ("Khmer", "高棉语"), "my": ("Burmese", "缅甸语"),
    "fa": ("Persian", "波斯语"), "gu": ("Gujarati", "古吉拉特语"),
    "ur": ("Urdu", "乌尔都语"), "te": ("Telugu", "泰卢固语"),
    "mr": ("Marathi", "马拉地语"), "he": ("Hebrew", "希伯来语"),
    "bn": ("Bengali", "孟加拉语"), "ta": ("Tamil", "泰米尔语"),
    "uk": ("Ukrainian", "乌克兰语"), "bo": ("Tibetan", "藏语"),
    "kk": ("Kazakh", "哈萨克语"), "mn": ("Mongolian", "蒙古语"),
    "ug": ("Uyghur", "维吾尔语"), "yue": ("Cantonese", "粤语"),
}


def is_model(model):
    return model.casefold() in {key.casefold() for key in MODELS}


def language_key(value):
    value = value.lower()
    if value in {"zh-tw", "zh-hk", "zh-hant"}:
        return "zh-hant"
    return value.split("-")[0]


async def translate_cue(url, model, cue, source, target, request):
    target_key = language_key(target)
    if target_key not in LANGUAGES:
        raise ValueError("HY-MT1.5 暂不支持所选目标语言，请更换语言或模型")
    chinese = target_key.startswith("zh") or language_key(source).startswith("zh")
    if chinese:
        prompt = f"将以下文本翻译为{LANGUAGES[target_key][1]}，注意只需要输出翻译后的结果，不要额外解释：\n\n{cue['text']}"
    else:
        prompt = f"Translate the following segment into {LANGUAGES[target_key][0]}, without additional explanation.\n\n{cue['text']}"
    # These two variants use different special tokens in Tencent's model cards.
    if "1.8b" in model.lower():
        prompt = f"<｜hy_begin▁of▁sentence｜><｜hy_User｜>{prompt}<｜hy_Assistant｜>"
    else:
        prompt = f"<|startoftext|>{prompt}<|extra_0|>"
    result = await request("POST", url + "/api/generate", json={
        "model": model, "prompt": prompt, "raw": True, "stream": False,
        "keep_alive": "5m",
        "options": {"temperature": 0.7, "top_k": 20, "top_p": 0.6,
                    "repeat_penalty": 1.05, "num_predict": 4096},
    })
    if result.get("done_reason") == "length":
        raise ValueError("HY-MT1.5 译文被截断，原文已保留；请缩短字幕片段后重试")
    text = result.get("response")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("HY-MT1.5 返回空译文，原文已保留；请重试")
    return validate_cues([{**cue, "text": text.strip()}])
