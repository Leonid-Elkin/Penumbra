"""
game.net – LAN multiplayer transport.

A tiny host-authoritative link for a two-player PvP battle. One player HOSTS
(runs the authoritative simulation) and the other JOINS. The transport is a
single TCP socket carrying length-prefixed JSON messages:

    [4-byte big-endian length][UTF-8 JSON payload]

Message flow (see game.netsync for the payload shapes):
  host → client :  hello (level + config), snap (world snapshots), over, alert
  client → host :  ready, cmd (an enemy-faction order)

The socket is serviced on a background reader thread that only ever appends to a
thread-safe queue; the Qt battle loop drains that queue on its own tick, so no
Qt object is ever touched off the GUI thread. Sends happen straight from the GUI
thread under a lock – LAN messages are small and infrequent enough that a
blocking send never stalls a frame in practice.
"""

from __future__ import annotations
import base64, json, os, socket, struct, threading, time, queue
from typing import Optional


DEFAULT_PORT = 50577
_HEADER = struct.Struct(">I")          # 4-byte unsigned length prefix
_MAX_MSG = 8 * 1024 * 1024             # sanity cap on a single frame (8 MB)

# Public relay used for WAN ("online") play. Both players connect OUTBOUND here,
# so neither has to port-forward; the relay pairs them by room code and then just
# pipes bytes between the two sockets (see relay.py).
#
# The endpoint is deliberately NOT a plaintext literal: players create/join rooms
# by code and never see, type, or need the server address, so it's kept out of the
# UI and lightly obscured here (a casual `strings`/grep won't surface an IP). This
# is obfuscation, not secrecy – the address is observable on the wire – its only
# job is to keep the relay off the players' radar. Override with PENUMBRA_RELAY
# ("host" or "host:port") for local testing / self-hosting.
_RELAY_BLOB = "QVFZW1xaQE8fQ1dCVkoXVERZVkQ="
_RELAY_KEY = b"penumbra-relay-atlas-7"


def new_session_token() -> str:
    """A short, unguessable id the host mints per match. It rides in the 'hello' so
    both sides know it; on a mid-match disconnect it names the private relay slot
    the players rendezvous through to reconnect (see NetRelayHost/NetReconnectClient)."""
    return base64.urlsafe_b64encode(os.urandom(6)).decode("ascii").rstrip("=")


def relay_endpoint() -> "tuple[str, int]":
    """Resolve the relay (host, port). Honours the PENUMBRA_RELAY env override,
    else decodes the obscured built-in endpoint."""
    override = os.environ.get("PENUMBRA_RELAY", "").strip()
    if override:
        host, _, p = override.rpartition(":")
        if host and p.isdigit():
            return host, int(p)
        return override, DEFAULT_PORT
    raw = base64.b64decode(_RELAY_BLOB)
    text = bytes(b ^ _RELAY_KEY[i % len(_RELAY_KEY)]
                 for i, b in enumerate(raw)).decode("utf-8")
    host, _, p = text.rpartition(":")
    return host, int(p)


def local_ips() -> list[str]:
    """Best-effort list of this machine's LAN IPv4 addresses, so the host screen
    can tell the other player what to type. Falls back to loopback."""
    ips: list[str] = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    # A UDP "connect" to a public address reveals the primary outbound NIC
    # without sending anything – catches the common case the hostname lookup misses.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip not in ips and not ip.startswith("127."):
                ips.insert(0, ip)
        finally:
            s.close()
    except OSError:
        pass
    if not ips:
        ips.append("127.0.0.1")
    return ips


# ── One-shot framed control messages (used only for the relay handshake) ──────
# The live game link (NetLink) does its own framing; these standalone helpers let
# the relay connectors exchange a single length-prefixed JSON control frame on a
# raw socket *before* that socket is handed to NetLink. They read/write exact byte
# counts, so no bytes of the following game stream are ever consumed.
def _recv_all(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def send_frame(sock: socket.socket, obj: dict):
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    sock.sendall(_HEADER.pack(len(payload)) + payload)


def recv_frame(sock: socket.socket) -> Optional[dict]:
    head = _recv_all(sock, _HEADER.size)
    if head is None:
        return None
    (length,) = _HEADER.unpack(head)
    if length == 0 or length > _MAX_MSG:
        return None
    body = _recv_all(sock, length)
    if body is None:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


class NetLink:
    """A live connection to the peer. Wraps one connected socket, runs a reader
    thread, and exposes a poll()-able inbox of decoded messages plus send()."""

    def __init__(self, sock: socket.socket, role: str):
        self.sock = sock
        self.role = role                       # "host" | "client"
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self._send_lock = threading.Lock()
        self._alive = True
        self.error: Optional[str] = None
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # ── Lifecycle ────────────────────────────────────────────────────────────
    @property
    def alive(self) -> bool:
        return self._alive

    def close(self):
        self._alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def _fail(self, why: str):
        if self._alive:
            self.error = why
            self._alive = False
        try:
            self.sock.close()
        except OSError:
            pass

    # ── Send (GUI thread) ────────────────────────────────────────────────────
    def send(self, msg: dict):
        if not self._alive:
            return
        try:
            payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")
            frame = _HEADER.pack(len(payload)) + payload
            with self._send_lock:
                self.sock.sendall(frame)
        except (OSError, ValueError) as e:
            self._fail(f"send failed: {e}")

    # ── Receive (drained by the GUI thread) ──────────────────────────────────
    def poll(self) -> list[dict]:
        """Return every message that has arrived since the last poll (never blocks)."""
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                break
        return out

    def _recv_exact(self, n: int) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except OSError as e:
                self._fail(f"recv failed: {e}")
                return None
            if not chunk:
                self._fail("connection closed by peer")
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _read_loop(self):
        while self._alive:
            head = self._recv_exact(_HEADER.size)
            if head is None:
                break
            (length,) = _HEADER.unpack(head)
            if length == 0 or length > _MAX_MSG:
                self._fail("bad frame length")
                break
            body = self._recv_exact(length)
            if body is None:
                break
            try:
                self.inbox.put(json.loads(body.decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                self._fail("bad frame payload")
                break


class NetHost:
    """Listens for one joining player. Accepts on a background thread so the Qt
    lobby screen stays responsive; poll() reports the link once someone joins."""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.link: Optional[NetLink] = None
        self.error: Optional[str] = None
        self._srv: Optional[socket.socket] = None
        self._stop = False
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)

    def start(self) -> bool:
        try:
            self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._srv.bind(("0.0.0.0", self.port))
            self._srv.listen(1)
            self._srv.settimeout(0.5)
        except OSError as e:
            self.error = f"could not host on port {self.port}: {e}"
            return False
        self._thread.start()
        return True

    def _accept_loop(self):
        while not self._stop:
            try:
                conn, _addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError as e:
                if not self._stop:
                    self.error = f"accept failed: {e}"
                break
            self.link = NetLink(conn, "host")
            break
        try:
            if self._srv is not None:
                self._srv.close()
        except OSError:
            pass

    def poll(self) -> Optional[NetLink]:
        return self.link

    def cancel(self):
        self._stop = True
        try:
            if self._srv is not None:
                self._srv.close()
        except OSError:
            pass


class NetClient:
    """Connects to a host. The blocking connect runs on a background thread so
    the lobby can show a 'connecting…' state; poll the result each frame."""

    def __init__(self, host: str, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.link: Optional[NetLink] = None
        self.error: Optional[str] = None
        self._done = False
        self._thread = threading.Thread(target=self._connect, daemon=True)

    def start(self):
        self._thread.start()

    def _connect(self):
        try:
            sock = socket.create_connection((self.host, self.port), timeout=8.0)
            sock.settimeout(None)
            self.link = NetLink(sock, "client")
        except OSError as e:
            self.error = f"could not connect to {self.host}:{self.port}. {e}"
        finally:
            self._done = True

    @property
    def done(self) -> bool:
        return self._done

    def poll(self) -> Optional[NetLink]:
        return self.link


# ══ WAN play: connect out to a public relay instead of listening/dialing direct ══
#
# Both players open an OUTBOUND connection to the same relay (relay.py, running on
# a server with a public IP). Outbound TCP traverses home NAT with no port
# forwarding, so this Just Works over the internet. The host registers a room
# code; the joiner supplies the same code; the relay pairs them and then forwards
# raw bytes each way. From that point the socket behaves exactly like a direct
# link, so we hand it to the unchanged NetLink and the rest of the game is none
# the wiser.
#
# Both classes deliberately mirror NetHost / NetClient (start / poll / error /
# cancel|done) so the lobby drives LAN and online matches through one code path.

class NetRelayHost:
    """Host a match through a relay: dial the relay, open a named room (listed in
    the browser for others to pick), and wait for it to pair us with a joiner.
    poll() returns the live NetLink once paired, matching NetHost's surface; while
    waiting, `room_id` names the open room and the game shows 'awaiting player'."""

    def __init__(self, relay: str, port: int = DEFAULT_PORT,
                 name: str = "Room", map_: str = "?", reconnect: Optional[str] = None,
                 password: str = ""):
        self.relay = relay
        self.port = port
        self.name = name
        self.map = map_
        # "" ⇒ an open room; otherwise joiners must supply this exact password.
        self.password = password
        # When set, this opens a hidden "reconnect slot" under the session token
        # rather than a fresh, browser-listed room (used to resume a dropped match).
        self.reconnect = reconnect
        self.room_id: Optional[str] = None
        self.link: Optional[NetLink] = None
        self.error: Optional[str] = None
        self._sock: Optional[socket.socket] = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> bool:
        self._thread.start()
        return True

    def _run(self):
        try:
            sock = socket.create_connection((self.relay, self.port), timeout=8.0)
        except OSError as e:
            self.error = f"could not reach relay {self.relay}:{self.port}. {e}"
            return
        self._sock = sock
        try:
            # Smuggle the lock flag into `map` so it survives even a dumb/old relay
            # that strips the "locked" list field (see _decode_room). A hidden
            # reconnect slot is never browsed, so it's left untagged.
            map_field = self.map
            if self.password and not self.reconnect:
                map_field = _LOCK_MAP_MARK + map_field
            hello = {"t": "host", "name": self.name, "map": map_field}
            if self.reconnect:
                hello["reconnect"] = self.reconnect
            if self.password:
                hello["password"] = self.password
            send_frame(sock, hello)
            # First the relay confirms the room is open and listed…
            sock.settimeout(8.0)
            created = recv_frame(sock)
            if not created or created.get("t") != "created":
                self._cleanup(sock)
                if not self._stop:
                    self.error = (created or {}).get("msg", "relay refused the room")
                return
            self.room_id = created.get("room")
            # …then we park until a joiner is paired in (or we time out / cancel).
            sock.settimeout(None)
            ack = recv_frame(sock)
        except OSError as e:
            self._cleanup(sock)
            if not self._stop:
                self.error = f"relay handshake failed: {e}"
            return
        if self._stop:
            self._cleanup(sock)
            return
        if not ack or ack.get("t") != "peer":
            self._cleanup(sock)
            self.error = (ack or {}).get("msg", "the room closed before anyone joined")
            return
        self.link = NetLink(sock, "host")

    @staticmethod
    def _cleanup(sock: socket.socket):
        try:
            sock.close()
        except OSError:
            pass

    def poll(self) -> Optional[NetLink]:
        return self.link

    def cancel(self):
        self._stop = True
        # Closing the socket unblocks the reader thread's parked recv_frame() and
        # makes the relay drop our room from the browser list.
        if self._sock is not None and self.link is None:
            self._cleanup(self._sock)


class NetRelayClient:
    """Join a relayed match by room id (picked from the browser list). Mirrors
    NetClient (start / done / poll / error); the relay pairs a join near-instantly."""

    def __init__(self, relay: str, room: str, port: int = DEFAULT_PORT,
                 password: str = ""):
        self.relay = relay
        self.room = str(room).strip()
        self.port = port
        self.password = password          # supplied for locked rooms; "" otherwise
        self.link: Optional[NetLink] = None
        self.error: Optional[str] = None
        self._done = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        try:
            sock = socket.create_connection((self.relay, self.port), timeout=8.0)
        except OSError as e:
            self.error = f"could not reach relay {self.relay}:{self.port}. {e}"
            self._done = True
            return
        try:
            join = {"t": "join", "room": self.room}
            if self.password:
                join["password"] = self.password
            send_frame(sock, join)
            sock.settimeout(8.0)
            ack = recv_frame(sock)
            sock.settimeout(None)
        except OSError as e:
            NetRelayHost._cleanup(sock)
            self.error = f"relay handshake failed: {e}"
            self._done = True
            return
        if not ack or ack.get("t") != "peer":
            NetRelayHost._cleanup(sock)
            self.error = (ack or {}).get("msg", "that room is no longer open")
            self._done = True
            return
        self.link = NetLink(sock, "client")
        self._done = True

    @property
    def done(self) -> bool:
        return self._done

    def poll(self) -> Optional[NetLink]:
        return self.link


# A locked room's status must reach the browser even through an OLD relay that
# doesn't know the "locked" field – the relay is deliberately dumb (see relay.py /
# the host-authoritative check in battle.py), so we can't rely on it to advertise
# it. Every relay echoes the room's `map` verbatim, and the browser never shows
# `map`, so the host smuggles the lock flag in there as a leading sentinel; the
# client strips it back off. A newer relay's own "locked" field is honoured too.
_LOCK_MAP_MARK = "\x01lk\x01"


def _decode_room(r: dict) -> dict:
    """Normalise one relay room entry: recover `locked` from either the relay's own
    field or the host's map-sentinel fallback, and clean the sentinel off `map`."""
    m = r.get("map", "")
    marked = isinstance(m, str) and m.startswith(_LOCK_MAP_MARK)
    if marked:
        r["map"] = m[len(_LOCK_MAP_MARK):]
    r["locked"] = bool(r.get("locked")) or marked
    return r


def fetch_rooms(relay: str, port: int = DEFAULT_PORT,
                timeout: float = 5.0) -> list[dict]:
    """One-shot query of the relay's open-room list. Raises OSError on failure."""
    sock = socket.create_connection((relay, port), timeout=timeout)
    try:
        send_frame(sock, {"t": "list"})
        sock.settimeout(timeout)
        resp = recv_frame(sock)
    finally:
        NetRelayHost._cleanup(sock)
    if not resp or resp.get("t") != "rooms":
        return []
    return [_decode_room(r) for r in resp.get("rooms", []) if isinstance(r, dict)]


class NetRoomBrowser:
    """Background poller of the relay's open-room list. The lobby reads `rooms`
    (a list of {id, name, map}) on its own timer to repaint the browsable list,
    and `error` for the connection state, without ever blocking the GUI thread."""

    def __init__(self, relay: str, port: int = DEFAULT_PORT, interval: float = 1.5):
        self.relay = relay
        self.port = port
        self.interval = interval
        self.rooms: list[dict] = []
        self.error: Optional[str] = None
        self.loaded = False                 # True once at least one poll has returned
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True

    def _loop(self):
        while not self._stop:
            try:
                self.rooms = fetch_rooms(self.relay, self.port)
                self.error = None
            except OSError as e:
                self.error = f"cannot reach the server. {e}"
            self.loaded = True
            slept = 0.0
            while slept < self.interval and not self._stop:
                time.sleep(0.1); slept += 0.1


class NetReconnectClient:
    """Client half of a mid-match reconnect: repeatedly tries to rejoin the host's
    hidden reconnect slot (by session token) until it pairs or is stopped. Retries
    because the host may take a moment to re-open the slot after the drop. poll()
    returns the live NetLink once back in; the battle stops it at the grace deadline."""

    def __init__(self, relay: str, token: str, port: int = DEFAULT_PORT,
                 retry: float = 0.4):
        self.relay = relay
        self.token = str(token)
        self.port = port
        self.retry = retry
        self.link: Optional[NetLink] = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True

    def _run(self):
        while not self._stop and self.link is None:
            sock = None
            try:
                sock = socket.create_connection((self.relay, self.port), timeout=4.0)
                send_frame(sock, {"t": "join", "room": self.token})
                sock.settimeout(4.0)
                ack = recv_frame(sock)
                sock.settimeout(None)
            except OSError:
                ack = None
            if ack and ack.get("t") == "peer":
                self.link = NetLink(sock, "client")
                return
            if sock is not None:
                NetRelayHost._cleanup(sock)
            slept = 0.0
            while slept < self.retry and not self._stop:
                time.sleep(0.1); slept += 0.1

    def poll(self) -> Optional[NetLink]:
        return self.link
