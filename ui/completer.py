"""검색창 자동완성: 글자 일부만 쳐도(가운데 일치, 대소문자 무시) 목록이 뜬다. 목록은 나중에 바꿀 수 있다."""
from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QCompleter


def attach_completer(line_edit, words=(), on_pick=None, max_visible=15):
    """line_edit 에 자동완성을 붙이고 QCompleter 를 돌려준다. 목록 바꾸기: set_words(completer, words).
    on_pick(text): 목록에서 골랐을 때."""
    completer = QCompleter(QStringListModel(_unique(words), line_edit), line_edit)
    completer.setFilterMode(Qt.MatchContains)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    completer.setMaxVisibleItems(max_visible)
    line_edit.setCompleter(completer)
    if on_pick:
        completer.activated[str].connect(on_pick)
    return completer


def set_words(completer, words):
    words = _unique(words)
    if completer.model().stringList() != words:
        completer.model().setStringList(words)


def _unique(words):
    return sorted({str(w).strip() for w in words if w is not None and str(w).strip()})
