"""整篇预览提速：纯文本 → 文本的结果按进程缓存（LRU，按条数和总字符数限量），线程安全。

只用于纯函数（同一输入恒得同一输出，例如 Markdown → HTML）。缓存的是不可变字符串，调用方不会改到缓存。"""
import threading
from collections import OrderedDict
from collections.abc import Callable


class TextMemo:
    def __init__(self, fn: Callable[[str], str], *, max_items: int = 20000, max_chars: int = 40_000_000):
        self._fn, self._max_items, self._max_chars = fn, max_items, max_chars
        self._items: OrderedDict[str, str] = OrderedDict()
        self._chars = 0
        self._lock = threading.Lock()

    def __call__(self, value: str) -> str:
        with self._lock:
            found = self._items.get(value)
            if found is not None:
                self._items.move_to_end(value)
                return found
        result = self._fn(value)
        size = len(value) + len(result)
        if size > self._max_chars // 4:
            return result
        with self._lock:
            if value not in self._items:
                self._items[value] = result
                self._chars += size
                while len(self._items) > self._max_items or self._chars > self._max_chars:
                    old_key, old_value = self._items.popitem(last=False)
                    self._chars -= len(old_key) + len(old_value)
        return result

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._chars = 0
