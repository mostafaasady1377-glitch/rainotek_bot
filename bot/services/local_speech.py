"""Local Persian voice transcription without sending audio to another service."""
import asyncio
from functools import lru_cache
from io import BytesIO
import json
from pathlib import Path
import sys
import threading

ROOT=Path(__file__).resolve().parents[2]
MODEL_PATH=ROOT/'bot/assets/speech/vosk-model-small-fa-0.42'
_lock=threading.Lock()

@lru_cache(maxsize=1)
def speech_model():
    local=ROOT/'.local-deps'
    if local.is_dir() and str(local) not in sys.path:
        sys.path.insert(0,str(local))
    from vosk import Model, SetLogLevel
    SetLogLevel(-1)
    if not (MODEL_PATH/'am/final.mdl').is_file():
        raise RuntimeError('مدل تشخیص گفتار فارسی آماده نیست.')
    return Model(str(MODEL_PATH))

def _transcribe(audio):
    with _lock:
        model=speech_model()
        import av
        from vosk import KaldiRecognizer
        recognizer=KaldiRecognizer(model,16000)
        texts=[]
        samples=0
        with av.open(BytesIO(audio)) as container:
            resampler=av.AudioResampler(format='s16',layout='mono',rate=16000)
            for frame in container.decode(audio=0):
                for pcm in resampler.resample(frame):
                    samples+=pcm.samples
                    if samples>16000*180:
                        raise ValueError('لطفاً ویس کوتاه‌تر از سه دقیقه بفرستید.')
                    if recognizer.AcceptWaveform(bytes(pcm.planes[0])[:pcm.samples*2]):
                        texts.append(json.loads(recognizer.Result()).get('text',''))
            for pcm in resampler.resample(None):
                recognizer.AcceptWaveform(bytes(pcm.planes[0])[:pcm.samples*2])
            texts.append(json.loads(recognizer.FinalResult()).get('text',''))
        text=' '.join(x for x in texts if x).strip()
        if not text:
            raise ValueError('گفتار واضحی تشخیص داده نشد؛ لطفاً واضح‌تر صحبت کنید یا مشخصات را بنویسید.')
        return text

async def transcribe_local(audio):
    return await asyncio.to_thread(_transcribe,audio)
