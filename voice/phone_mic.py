"""Use an Android phone as Zephyr's microphone over Wi-Fi.

The phone runs AndroidMic (open source, github.com/teamclouday/AndroidMic) in Wi-Fi / TCP mode,
pointed at this laptop's IP and PHONE_MIC_PORT. This file speaks AndroidMic's own protocol, so
no Windows app or virtual audio cable is needed:
  phone connects -> sends "AndroidMic1" -> we answer "AndroidMic2" ->
  then a stream of [4-byte big-endian length][protobuf AudioPacketMessage].

While the phone is connected its audio replaces the laptop mic; when it disconnects (you left,
the app stopped, Wi-Fi dropped), the laptop mic takes over again.
"""
import socket
import struct
import threading
import time

import numpy as np

CHECK_1, CHECK_2 = b'AndroidMic1', b'AndroidMic2'
# android.media.AudioFormat encodings
PCM_16, PCM_8, PCM_FLOAT, PCM_24, PCM_32 = 2, 3, 4, 21, 22


def _varint(buf, i):
    shift = value = 0
    while True:
        b = buf[i]
        i += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, i
        shift += 7


def decode_packet(buf):
    """AudioPacketMessage {bytes buffer=1; uint32 sample_rate=2; uint32 channel_count=3; uint32 audio_format=4}"""
    out, i = {}, 0
    while i < len(buf):
        key, i = _varint(buf, i)
        field, wire = key >> 3, key & 7
        if wire == 0:
            out[field], i = _varint(buf, i)
        elif wire == 2:
            n, i = _varint(buf, i)
            out[field], i = buf[i:i + n], i + n
        elif wire == 5:
            i += 4
        elif wire == 1:
            i += 8
        else:
            raise ValueError('bad protobuf')
    return out.get(1, b''), out.get(2, 16000), out.get(3, 1), out.get(4, PCM_16)


def to_float(data, fmt):
    if fmt == PCM_16:
        return np.frombuffer(data, '<i2').astype(np.float32) / 32768
    if fmt == PCM_FLOAT:
        return np.frombuffer(data, '<f4')
    if fmt == PCM_8:
        return (np.frombuffer(data, np.uint8).astype(np.float32) - 128) / 128
    if fmt == PCM_32:
        return np.frombuffer(data, '<i4').astype(np.float32) / 2147483648
    if fmt == PCM_24:
        b = np.frombuffer(data[:len(data) // 3 * 3], np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        return (np.where(v >= 1 << 23, v - (1 << 24), v)).astype(np.float32) / 8388608
    raise ValueError(f'audio format {fmt}')


class PhoneMic:
    def __init__(self, port, rate, frame, deliver, log, gain=1.0):
        """deliver(frame_int16) is called with `frame`-sample chunks at `rate` while connected."""
        self.port, self.rate, self.frame, self.deliver, self.log, self.gain = port, rate, frame, deliver, log, gain
        self.connected = None            # the phone's address while it's streaming
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('0.0.0.0', self.port))
        srv.listen(1)
        self.log.info(f'phone mic: waiting for AndroidMic on port {self.port}')
        while True:
            conn, addr = srv.accept()
            try:
                self._stream(conn, addr[0])
            except Exception as e:
                self.log.info(f'phone mic dropped: {e}')
            finally:
                self.connected = None
                conn.close()
                self.log.info('phone mic disconnected: back to the laptop mic')

    def _read(self, conn, n):
        buf = b''
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError('phone closed the connection')
            buf += chunk
        return buf

    def _stream(self, conn, ip):
        conn.settimeout(5)
        if self._read(conn, len(CHECK_1)) != CHECK_1:
            raise ConnectionError('not AndroidMic')
        conn.sendall(CHECK_2)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.connected = ip
        self.log.info(f'phone mic connected from {ip}: using it instead of the laptop mic')
        pending = np.zeros(0, np.float32)
        while True:
            (n,) = struct.unpack('>I', self._read(conn, 4))
            if n > 1 << 20:
                raise ConnectionError('packet too large')
            data, sr, channels, fmt = decode_packet(self._read(conn, n))
            x = to_float(data, fmt)
            if channels > 1:
                x = x[:len(x) // channels * channels].reshape(-1, channels).mean(axis=1)
            if sr != self.rate and len(x):
                # simple linear resample; fine for speech going to 16 kHz recognisers
                t = np.arange(0, len(x), sr / self.rate)
                x = np.interp(t, np.arange(len(x)), x).astype(np.float32)
            pending = np.concatenate([pending, x])
            while len(pending) >= self.frame:
                f, pending = pending[:self.frame], pending[self.frame:]
                self.deliver(np.clip(f * self.gain * 32768, -32768, 32767).astype(np.int16))
