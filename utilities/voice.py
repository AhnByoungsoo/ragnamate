"""Windows 내장 음성(SAPI)으로 글을 wav로 만든다. 외부 서비스에 보내지 않는다.

한국어 음성 'Microsoft Heami Desktop'은 한국어 Windows에 기본으로 들어 있다. 없으면 RuntimeError.
"""
import base64
import os
import struct
import subprocess
import wave

KOREAN_VOICE = 'Microsoft Heami Desktop'
RATE = 44100
_SCRIPT = """
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('%(voice)s')
$s.Rate = %(rate)d
$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(%(hz)d, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile('%(path)s', $f)
$s.Speak('%(text)s')
$s.Dispose()
"""


def _quote(text):
    return str(text).replace("'", "''").replace('\r', ' ').replace('\n', ' ')


def speak_to_wav(text, path, voice=KOREAN_VOICE, rate=1, timeout=30):
    """text 를 읽은 소리를 path(16비트 모노 44.1kHz wav)에 쓴다. rate: -10(느리게) ~ 10(빠르게)."""
    text = (text or '').strip()
    if not text:
        raise ValueError('읽을 글이 없습니다.')
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    script = _SCRIPT % dict(voice=_quote(voice), rate=int(rate), hz=RATE, path=_quote(os.path.abspath(path)),
                            text=_quote(text))
    done = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                           base64.b64encode(script.encode('utf-16-le')).decode('ascii')],
                          capture_output=True, timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if done.returncode or not os.path.isfile(path) or os.path.getsize(path) < 1000:
        raise RuntimeError('음성을 만들지 못했습니다(Windows 한국어 음성이 설치돼 있는지 확인하세요).')
    return path


def _read(path):
    with wave.open(path, 'rb') as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, RATE):
            raise ValueError('16비트 모노 44.1kHz wav만 섞을 수 있습니다: %s' % path)
        return list(struct.unpack('<%dh' % w.getnframes(), w.readframes(w.getnframes())))


def mix(voice_wav, under_wav, out, delay=0.3, under_gain=0.4):
    """음성 밑에 다른 소리(팡파르)를 작게 깔아 out 에 쓴다. 음성은 delay 초 뒤에 시작한다."""
    voice, under = _read(voice_wav), _read(under_wav)
    loud = [i for i, v in enumerate(voice) if abs(v) > 800]
    if loud:                                       # 뒤쪽 빈 소리를 잘라 내고, 작게 녹음된 음성을 키운다
        voice = voice[:min(len(voice), loud[-1] + int(0.2 * RATE))]
        gain = 29000 / max(abs(v) for v in voice)
        voice = [v * gain for v in voice]
    start = int(delay * RATE)
    total = max(len(under), start + len(voice))
    data = [0.0] * total
    for i, v in enumerate(under):
        data[i] += v * under_gain
    for i, v in enumerate(voice):
        data[start + i] += v
    peak = max(1.0, max(abs(v) for v in data))
    scale = min(1.0, 0.9 * 32767 / peak)
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack('<%dh' % total, *(int(v * scale) for v in data)))
    return out
