"""앱 업데이트: 이 폴더가 GitHub에서 받은(git clone) 것이면 새 버전이 있는지 보고, 받아 온다.

- 확인: git fetch 뒤 내 버전과 서버 버전 사이의 커밋 수·제목.
- 받기: git pull --ff-only --autostash (설정·DB는 git에 없어 건드리지 않는다), 이어서 필요한 패키지 설치.
- git이 없거나 받은 폴더가 아니면 available()이 False — 버튼을 숨긴다.
"""
import os
import subprocess
import sys

TIMEOUT = 60
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class UpdateError(Exception):
    pass


def _git(root, *args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')            # 뒤에서 암호를 묻느라 멈추지 않게
    try:
        r = subprocess.run(('git',) + args, cwd=root, env=env, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=TIMEOUT, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise UpdateError(str(e) or e.__class__.__name__)
    if r.returncode:
        raise UpdateError((r.stderr or r.stdout).strip() or 'git %s 실패' % args[0])
    return r.stdout.strip()


def available(root):
    """git으로 받은 폴더이고 받아 올 곳(origin)이 있는가."""
    if not os.path.isdir(os.path.join(root, '.git')):
        return False
    try:
        return bool(_git(root, 'remote', 'get-url', 'origin'))
    except UpdateError:
        return False


def check(root):
    """서버에만 있는 변경의 제목들(최신이 앞). 최신 버전이면 []."""
    _git(root, 'fetch', '-q', 'origin')
    out = _git(root, 'log', '--format=%s', 'HEAD..@{u}')
    return out.splitlines() if out else []


def apply(root, pip=True):
    """새 버전을 받는다. 반환: 받은 뒤의 버전(짧은 해시)."""
    before = _git(root, 'rev-parse', 'HEAD')
    _git(root, 'pull', '--ff-only', '--autostash', '-q')
    after = _git(root, 'rev-parse', 'HEAD')
    req = os.path.join(root, 'requirements-replay.txt')
    if pip and after != before and os.path.isfile(req):
        subprocess.run((sys.executable, '-m', 'pip', 'install', '-q', '-r', req), cwd=root, capture_output=True,
                       timeout=600, creationflags=NO_WINDOW)     # 실패해도 업데이트는 끝난 것 — 다음 실행 때 다시 본다
    return after[:7]
