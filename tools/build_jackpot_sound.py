"""득템 축하 효과음(ui/assets/jackpot.wav)을 만든다 — 외부 음원 없이 사인파를 합성한다.

올라가는 아르페지오(도-미-솔-도) 뒤에 화음을 길게 울리는 짧은 팡파르. 다시 만들려면:
    python tools/build_jackpot_sound.py
"""
import math
import os
import struct
import wave

RATE = 44100
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ui', 'assets', 'jackpot.wav')
C5, E5, G5, C6, E6 = 523.25, 659.25, 783.99, 1046.50, 1318.51
# (시작 초, 길이 초, [주파수…], 세기)
NOTES = [(0.00, 0.14, [C5], 0.50), (0.11, 0.14, [E5], 0.50), (0.22, 0.14, [G5], 0.50),
         (0.33, 0.95, [C6, G5, E6], 0.42)]


def tone(freq, t):
    """종소리 비슷하게: 기본음 + 배음 조금."""
    return (math.sin(2 * math.pi * freq * t) + 0.35 * math.sin(4 * math.pi * freq * t)
            + 0.12 * math.sin(6 * math.pi * freq * t))


def envelope(t, length):
    attack = min(1.0, t / 0.008)                     # 8ms 안에 올라와 '틱' 소리를 막는다
    return attack * math.exp(-3.2 * t / length)


def build():
    total = max(start + length for start, length, _, _ in NOTES) + 0.05
    samples = [0.0] * int(total * RATE)
    for start, length, freqs, gain in NOTES:
        first = int(start * RATE)
        for i in range(int(length * RATE)):
            t = i / RATE
            samples[first + i] += gain * envelope(t, length) * sum(tone(f, t) for f in freqs) / len(freqs)
    peak = max(abs(s) for s in samples) or 1.0
    return [int(s / peak * 0.85 * 32767) for s in samples]


def main():
    data = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with wave.open(OUT, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack('<%dh' % len(data), *data))
    print('%s  %.2f초  %d바이트' % (OUT, len(data) / RATE, os.path.getsize(OUT)))


if __name__ == '__main__':
    main()
