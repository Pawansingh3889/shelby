from __future__ import annotations

import io
import os
from typing import Awaitable, Callable, Optional

import av
import edge_tts
import numpy as np
import pyttsx3
import sounddevice as sd
from faster_whisper import WhisperModel


WordEvent = dict  # {"text": str, "offset_ms": float, "duration_ms": float}
OnSpeakStart = Callable[[list[WordEvent]], Awaitable[None]]


SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"
DEFAULT_STT_MODEL = os.environ.get("SHELBY_STT_MODEL", "tiny.en")
DEFAULT_TTS_VOICE = os.environ.get("SHELBY_TTS_VOICE", "en-GB-SoniaNeural")
DEFAULT_TTS_RATE = os.environ.get("SHELBY_TTS_RATE", "+10%")


_stt: Optional[WhisperModel] = None


def get_stt(model_size: str = DEFAULT_STT_MODEL) -> WhisperModel:
    global _stt
    if _stt is None:
        print(f"[loading whisper {model_size} (first run downloads ~75MB)]", flush=True)
        _stt = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _stt


def warmup_stt() -> None:
    model = get_stt()
    silent = np.zeros(SAMPLE_RATE, dtype=np.float32)
    list(model.transcribe(silent, beam_size=1, vad_filter=False)[0])


async def warmup_tts(
    voice: str = DEFAULT_TTS_VOICE,
    rate: str = DEFAULT_TTS_RATE,
) -> None:
    """Run a tiny edge-tts request to pay the TLS + websocket cold-start cost
    once at boot instead of on the first real reply. Saves ~400-600ms off the
    first user-visible TTS chunk.
    """
    try:
        await _edge_tts_to_pcm(".", voice, rate)
    except Exception as exc:
        print(f"[tts warmup skipped: {exc}]", flush=True)


def transcribe(audio: np.ndarray) -> str:
    if audio.size == 0:
        return ""
    model = get_stt()
    audio_f32 = audio.astype(np.float32) / 32768.0
    segments, _ = model.transcribe(audio_f32, beam_size=1, vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


async def _edge_tts_to_pcm(text: str, voice: str, rate: str) -> tuple[np.ndarray, int, list[WordEvent]]:
    communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    mp3_bytes = bytearray()
    words: list[WordEvent] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3_bytes.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            words.append({
                "text": chunk["text"],
                "offset_ms": chunk["offset"] / 10000.0,
                "duration_ms": chunk["duration"] / 10000.0,
            })

    container = av.open(io.BytesIO(bytes(mp3_bytes)))
    stream = container.streams.audio[0]
    sample_rate = stream.codec_context.sample_rate

    frames = []
    for frame in container.decode(stream):
        arr = frame.to_ndarray()
        if arr.ndim > 1:
            arr = arr.mean(axis=0)
        frames.append(arr)

    pcm = np.concatenate(frames).astype(np.float32)
    if np.issubdtype(pcm.dtype, np.integer):
        pcm = pcm / np.iinfo(pcm.dtype).max
    elif pcm.max() > 1.0:
        pcm = pcm / 32768.0

    return pcm, sample_rate, words


async def speak_async(
    text: str,
    voice: str = DEFAULT_TTS_VOICE,
    rate: str = DEFAULT_TTS_RATE,
    on_start: Optional[OnSpeakStart] = None,
) -> None:
    if not text.strip():
        return
    try:
        pcm, sr, words = await _edge_tts_to_pcm(text, voice, rate)
        if on_start is not None:
            await on_start(words)
        sd.play(pcm, sr)
        sd.wait()
        return
    except Exception as exc:
        print(f"[edge-tts failed, falling back to SAPI: {exc}]", flush=True)
        if on_start is not None:
            try:
                await on_start([])
            except Exception:
                pass
        _speak_sapi(text)


def _speak_sapi(text: str, rate: int = 185) -> None:
    engine = pyttsx3.init()
    engine.setProperty("rate", rate)
    engine.say(text)
    engine.runAndWait()


def speak(text: str) -> None:
    import anyio
    anyio.run(speak_async, text)


class Recorder:
    def __init__(self) -> None:
        self._frames: list[np.ndarray] = []
        self._stream: Optional[sd.InputStream] = None

    def start(self) -> None:
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time, status):
        self._frames.append(indata.copy())

    def stop(self) -> np.ndarray:
        if self._stream is None:
            return np.zeros(0, dtype=DTYPE)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        if not self._frames:
            return np.zeros(0, dtype=DTYPE)
        return np.concatenate(self._frames).flatten()
