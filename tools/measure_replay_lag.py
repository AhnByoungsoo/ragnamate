"""게임이 리플레이 파일에 얼마나 자주 기록하는지 잰다(녹화 중에 실행). 파일을 읽기만 한다.

    python tools/measure_replay_lag.py [초=60] [리플레이 폴더]

파일이 커질 때마다: 지난번 뒤 몇 초 만인지, 몇 바이트·패킷 몇 개인지, 그 안의 패킷 시각이 몇 초 구간인지.
'구간'이 길면 게임이 모아서 한꺼번에 쓰는 것 — 그만큼 실시간 알림이 늦는다.
"""
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live.reader import IncrementalReplayReader  # noqa: E402


def main(seconds=60, folder=r'C:\Gravity\Ragnarok\Replay'):
    path = max(glob.glob(os.path.join(folder, '*.rrf')), key=os.path.getmtime)
    reader = IncrementalReplayReader(path)
    while reader.poll().frames:                                   # 이미 적힌 부분은 건너뛴다
        pass
    print('보는 파일:', os.path.basename(path), '· 크기', reader.file_size)
    begin = last = time.monotonic()
    gaps, spans = [], []
    while time.monotonic() - begin < seconds:
        size = reader.file_size
        frames = reader.poll().frames
        now = time.monotonic()
        if frames:
            span = (frames[-1].timestamp - frames[0].timestamp) / 1000
            gaps.append(now - last)
            spans.append(span)
            print('%6.1fs  +%.2f초 만에  %6d바이트  패킷 %4d개  패킷 시각 구간 %.2f초'
                  % (now - begin, now - last, reader.file_size - size, len(frames), span))
            last = now
        time.sleep(0.02)
    if gaps:
        print('기록 %d번 · 간격 평균 %.2f초 최대 %.2f초 · 한 번에 담긴 구간 최대 %.2f초'
              % (len(gaps), sum(gaps) / len(gaps), max(gaps), max(spans)))
    else:
        print('%d초 동안 파일이 커지지 않았습니다 — 녹화 중이 아닙니다.' % seconds)


if __name__ == '__main__':
    main(*([float(sys.argv[1])] if len(sys.argv) > 1 else []), *sys.argv[2:3])
