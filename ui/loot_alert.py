"""득템 알림: LIVE로 붙은 리플레이에서 새로 얻은 아이템의 노점 시세가 기준 이상이면 축하 효과음을 낸다.

소리만 낸다 — 게임에 입력을 보내지 않는다. 획득은 리플레이의 획득 패킷(●), 가격은 공식 노점 자료다.
"""
import logging
import os
import queue
import threading

from PySide6.QtCore import QObject, Signal

from core.fmt import comma
from services import gnjoy_market as G

log = logging.getLogger(__name__)
_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
FANFARE = os.path.join(_ASSETS, 'jackpot.wav')                 # 효과음만
VOICE = os.path.join(_ASSETS, 'jackpot_voice.wav')             # 효과음 + "득템 축하드려요!" 음성
VOICE_TEXT = '득템 축하드려요!'
DEFAULT_SOUND = VOICE if os.path.isfile(VOICE) else FANFARE


def make_voice_sound(text, out):
    """팡파르가 울리고 0.35초 뒤(화음이 깔릴 때) Windows 한국어 음성으로 text 를 읽는 wav를 out 에 만든다."""
    import tempfile

    from utilities import voice
    with tempfile.TemporaryDirectory() as tmp:
        spoken = voice.speak_to_wav(text, os.path.join(tmp, 'voice.wav'))
        return voice.mix(spoken, FANFARE, out, delay=0.35, under_gain=0.4)


def sound_path(settings):
    path = (settings or {}).get('jackpot_sound') or ''
    return path if path and os.path.isfile(path) else DEFAULT_SOUND


class LootAlert(QObject):
    hit = Signal(dict)            # 가격을 알아본 스레드 → 화면

    def __init__(self, settings, pricer, name_of, fire, parent=None):
        """pricer: services.loot_value.LootValue · name_of(item_id) → 이름 · fire(글, 소리 파일)."""
        super().__init__(parent)
        self.s, self.pricer, self.name_of, self.fire = settings, pricer, name_of, fire
        self._session, self._seq = None, 0
        self._drops_ready, self._drop_seq = False, 0
        self._q = queue.Queue()
        self.hit.connect(self._announce)
        self._thread = threading.Thread(target=self._loop, daemon=True, name='LootAlert')
        self._thread.start()

    @property
    def threshold(self):
        return int(self.s.get('jackpot_min_price', 10_000_000) or 0)

    def feed(self, rp):
        """LIVE 스냅샷의 replay_live 부분을 받을 때마다 부른다. 새로 생긴 획득만 가격을 알아본다."""
        if not rp or not self.s.get('jackpot_enabled', True) or not self.threshold:
            return
        gains = rp.get('gains') or []
        top = max((g['seq'] for g in gains), default=0)
        if rp.get('session_id') != self._session or top < self._seq:
            # 다른 리플레이에 붙었거나 처음부터 다시 읽는 중: 이미 지나간 획득은 알리지 않는다
            self._session, self._seq, self._drops_ready = rp.get('session_id'), top, False
            self._feed_drops(rp)
            return
        for g in gains:
            if g['seq'] > self._seq:
                self._q.put(g)
        self._seq = top
        self._feed_drops(rp)

    def _feed_drops(self, rp):
        """고가 아이템이 바닥에 떨어진 순간(줍기 전) 알린다."""
        drops = rp.get('drops') or []
        top = max((d['seq'] for d in drops), default=0)
        if not self._drops_ready or top < self._drop_seq:       # 처음 붙었을 때 이미 지나간 드랍은 알리지 않는다
            self._drops_ready, self._drop_seq = True, top
            return
        if self.s.get('drop_alert', True):
            for d in drops:
                if d['seq'] > self._drop_seq:
                    self._q.put(dict(d, drop=True))
        self._drop_seq = top

    def check(self, gain):
        """획득 한 건 → 기준 이상이면 dict, 아니면 None. 시세를 모르면 None(알리지 않는다)."""
        item_id, name = gain['item_id'], self.name_of(gain['item_id'])
        unit, basis = self.pricer.price(item_id)
        # 이미 아는 값이 기준의 절반도 안 되면 다시 묻지 않는다(잡템마다 공식 사이트에 요청하지 않게)
        if unit is None or (unit * 2 >= self.threshold and not self.pricer.fresh(item_id)):
            unit, basis = self.pricer.ensure(item_id, name)
        if unit is None or unit < self.threshold:
            return None
        return dict(item_id=item_id, name=name, amount=gain.get('amount', 1), unit=unit, basis=basis,
                    drop=bool(gain.get('drop')))

    def _loop(self):
        while True:
            gain = self._q.get()
            if gain is None:
                return
            try:
                found = self.check(gain)
            except G.RateLimited as exc:
                log.info('득템 알림: 시세 요청이 잠시 막혀 건너뜀 (%s)', exc)
                continue
            except Exception:
                log.exception('득템 알림: 시세 확인 실패')
                continue
            if found:
                self.hit.emit(found)

    def _announce(self, d):
        if d.get('drop'):                              # 아직 줍지 않았다 — 소리는 기본 알림음, 축하는 주운 뒤에
            self.fire('💎 고가 드랍!  %s 떨어졌습니다 — 노점 시세 %s Z (%s)' % (d['name'], comma(d['unit']), d['basis']), '')
            return
        self.fire('🎉 득템!  %s%s — 노점 시세 %s Z (%s)' % (
            d['name'], ' ×%d' % d['amount'] if d['amount'] > 1 else '', comma(d['unit']), d['basis']),
            sound_path(self.s))

    def stop(self):
        self._q.put(None)
