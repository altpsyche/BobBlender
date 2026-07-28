"""A minimal websocket reader for ComfyUI's `/ws` progress stream. Stdlib only.

Why this exists at all: until the agent-surface gate the only progress a caller could report was the job's own status
string, polled at 1 Hz, so a 200 s mesh job showed "in_progress" two hundred times. ComfyUI
publishes per-node progress on `/ws` (`{"type": "progress", "data": {"value": 3, "max": 20}}`),
which is the difference between a spinner and a bar.

Why it is hand-rolled: `core/comfy.py` is stdlib-only by Bob-side constraint 1 (Blender's bundled
Python has no `websockets` and the extension ships no dependency), so the choice was a hand-rolled
reader or no per-node progress. The reader is small because it only has to do the client half of one
direction: ComfyUI never expects data from us beyond a pong, and server-to-client frames are
unmasked, so there is no send path to speak of.

The safety property that makes it worth shipping: **progress only, never termination.** A job's
terminal state still comes from the jobs API poll in `comfy.wait`, so a dropped, stalled or
never-connected socket costs a progress bar and nothing else. That is deliberate: a websocket is
one more thing that can hang, and no artifact in this integration should depend on one.

    ws = connect(url, client_id)          # None when anything at all goes wrong
    ws.pump(0.5, on_event)                # drain for up to 0.5 s, call on_event per message
    ws.close()
"""

import base64
import json
import os
import select
import socket
import struct
import urllib.parse

# Frame opcodes we care about. Binary frames are ComfyUI's live preview images, which Bob does not
# display, so they are read off the socket and dropped rather than decoded.
_TEXT, _BINARY, _CLOSE, _PING, _PONG = 0x1, 0x2, 0x8, 0x9, 0xA

# A single frame's payload is capped so a malformed or hostile length cannot make Bob allocate the
# machine. ComfyUI's own text events are a few hundred bytes; its preview binaries are the large
# ones and they are discarded anyway.
MAX_FRAME = 16 << 20


class _Socket:
    """An open `/ws` connection. Not thread-safe: one owner, which is the worker running `wait`."""

    def __init__(self, sock):
        self._sock = sock
        self._buf = b""
        self.closed = False

    # -- frame reading ---------------------------------------------------------------------
    def _fill(self, n, deadline_left):
        """Read until the buffer holds n bytes, or return False if it cannot within the budget."""
        while len(self._buf) < n:
            if deadline_left() <= 0:
                return False
            ready, _, _ = select.select([self._sock], [], [], min(0.05, deadline_left()))
            if not ready:
                continue
            try:
                chunk = self._sock.recv(65536)
            except OSError:
                self.closed = True
                return False
            if not chunk:
                self.closed = True
                return False
            self._buf += chunk
        return True

    def _take(self, n):
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _frame(self, deadline_left):
        """One frame as (opcode, payload, fin), or None when none arrived in the budget."""
        if not self._fill(2, deadline_left):
            return None
        b0, b1 = self._buf[0], self._buf[1]
        fin, opcode = bool(b0 & 0x80), b0 & 0x0F
        masked, length = bool(b1 & 0x80), b1 & 0x7F
        header = 2
        if length == 126:
            if not self._fill(4, deadline_left):
                return None
            length = struct.unpack(">H", self._buf[2:4])[0]
            header = 4
        elif length == 127:
            if not self._fill(10, deadline_left):
                return None
            length = struct.unpack(">Q", self._buf[2:10])[0]
            header = 10
        if length > MAX_FRAME:
            self.closed = True
            return None
        total = header + (4 if masked else 0) + length
        if not self._fill(total, deadline_left):
            return None
        self._take(header)
        mask = self._take(4) if masked else b""
        payload = self._take(length)
        if mask:  # a server has no business masking, but decode it rather than returning garbage
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload, fin

    def _send(self, opcode, payload=b""):
        """Client frames MUST be masked (RFC 6455 5.3). Only pong and close are ever sent."""
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        head = struct.pack("!BB", 0x80 | opcode, 0x80 | len(payload))  # payloads here are tiny
        try:
            self._sock.sendall(head + mask + masked)
        except OSError:
            self.closed = True

    # -- the one public verb ---------------------------------------------------------------
    def pump(self, budget, on_event):
        """Drain messages for up to `budget` seconds, calling `on_event(dict)` per JSON message.

        Returns the number of messages delivered. Never raises: this is a progress channel, and a
        caller that stops getting events has to keep working off the status poll (see the module
        docstring). Doubles as the caller's sleep, so `wait`'s loop does not sleep twice.
        """
        import time

        end = time.time() + max(0.0, budget)

        def left():
            return end - time.time()

        seen, parts, part_op = 0, [], None
        while not self.closed and left() > 0:
            frame = self._frame(left)
            if frame is None:
                break
            opcode, payload, fin = frame
            if opcode == _PING:
                self._send(_PONG, payload)
                continue
            if opcode == _CLOSE:
                self.closed = True
                break
            if opcode == _PONG:
                continue
            if opcode == 0x0:  # continuation of a fragmented message
                if part_op is None:
                    continue
                parts.append(payload)
            elif opcode in (_TEXT, _BINARY):
                part_op, parts = opcode, [payload]
            else:
                continue
            if not fin:
                continue
            data, op, parts, part_op = b"".join(parts), part_op, [], None
            if op != _TEXT:
                continue  # a preview image; Bob has no viewer for it
            try:
                event = json.loads(data.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(event, dict):
                seen += 1
                try:
                    on_event(event)
                except Exception:  # a caller's progress callback must not kill the read loop
                    pass
        return seen

    def close(self):
        if not self.closed:
            self._send(_CLOSE)
        self.closed = True
        try:
            self._sock.close()
        except OSError:
            pass


def connect(base, client_id, timeout=5):
    """Open `<base>/ws?clientId=<client_id>`, or return None if anything goes wrong.

    None is a normal outcome, not an error: `http://` may be a proxy that does not upgrade, the
    server may be an older build, or the socket may simply be refused. Every caller falls back to
    status polling, so this returning None costs granularity and nothing else.
    """
    parsed = urllib.parse.urlparse(base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = (parsed.path.rstrip("/") or "") + "/ws?" + urllib.parse.urlencode(
        {"clientId": client_id})
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    ).encode()

    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        if parsed.scheme == "https":
            import ssl

            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        sock.sendall(request)
        # The handshake response is small; read until the header terminator or give up.
        head = b""
        sock.settimeout(timeout)
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise OSError("closed during the websocket handshake")
            head += chunk
            if len(head) > 65536:
                raise OSError("websocket handshake response too large")
        status = head.split(b"\r\n", 1)[0]
        if b" 101" not in status:
            raise OSError(f"no upgrade: {status[:80]!r}")
        sock.settimeout(None)
        ws = _Socket(sock)
        # Bytes after the header terminator are already frames; keep them.
        ws._buf = head.split(b"\r\n\r\n", 1)[1]
        return ws
    except (OSError, ValueError):
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        return None


def progress_text(event, prompt_id=None):
    """One ComfyUI `/ws` event as a short progress string, or None when it says nothing useful.

    The event vocabulary is ComfyUI's, and only the four types that carry progress are read:
    `progress` (per-node sampler steps), `executing` (which node started), `execution_cached`
    (nodes skipped) and `status` (queue depth). Anything else, including the binary previews and
    this fork's `progress_state`, is ignored rather than guessed at.
    """
    kind, data = event.get("type"), event.get("data") or {}
    if prompt_id and data.get("prompt_id") not in (None, prompt_id):
        return None  # another client's job on the same server
    if kind == "progress":
        value, maximum = data.get("value"), data.get("max")
        if isinstance(value, (int, float)) and isinstance(maximum, (int, float)) and maximum:
            return f"step {int(value)}/{int(maximum)}"
        return None
    if kind == "executing":
        node = data.get("node")
        return f"node {node}" if node else None
    if kind == "execution_cached":
        nodes = data.get("nodes") or []
        return f"{len(nodes)} cached" if nodes else None
    if kind == "status":
        remaining = ((data.get("status") or {}).get("exec_info") or {}).get("queue_remaining")
        if isinstance(remaining, int) and remaining > 1:
            return f"queued, {remaining} ahead"
        return None
    return None
