"""Lets the tests import Zephyr without a microphone, TV, speech models or keys: hardware and
model libraries are replaced by empty stand-ins before assistant.py is imported."""
import os
import sys
import types

VOICE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'voice')
sys.path.insert(0, VOICE)


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules.setdefault(name, mod)
    return sys.modules[name]


class _Anything:
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Anything()

    def __getattr__(self, name):
        return _Anything()


_stub('sounddevice', play=lambda *a, **k: None, wait=lambda: None, stop=lambda: None,
      InputStream=_Anything, query_devices=lambda *a, **k: {'name': 'test'}, default=_Anything())
_stub('openwakeword')
_stub('openwakeword.model', Model=_Anything)
_stub('vosk', KaldiRecognizer=_Anything, Model=_Anything, SetLogLevel=lambda *a: None)
try:
    import websocket  # noqa: F401
except ImportError:
    _stub('websocket', create_connection=_Anything)
try:
    import keyring  # noqa: F401
except ImportError:
    _stub('keyring', get_password=lambda *a: None, set_password=lambda *a: None)
