"""득템 축하 효과음 + 음성(ui/assets/jackpot_voice.wav)을 만든다.

팡파르(ui/assets/jackpot.wav) 위에 Windows 한국어 음성으로 읽은 문구를 얹는다. 문구를 바꾸려면:
    python tools/build_jackpot_voice.py "득템 축하드려요!"
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.loot_alert import VOICE, VOICE_TEXT, make_voice_sound  # noqa: E402

if __name__ == '__main__':
    path = make_voice_sound(sys.argv[1] if len(sys.argv) > 1 else VOICE_TEXT, VOICE)
    print('%s  %d바이트' % (path, os.path.getsize(path)))
