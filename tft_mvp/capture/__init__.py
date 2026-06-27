from .base import ScreenCapture
from .file_capture import FileCapture

__all__ = ["ScreenCapture", "FileCapture", "MSSCapture"]


def __getattr__(name):  # 惰性导入 MSSCapture，避免无显示环境 import mss 失败
    if name == "MSSCapture":
        from .mss_capture import MSSCapture
        return MSSCapture
    raise AttributeError(name)
