#!/usr/bin/env python3
"""
relay.py — Penumbra WAN rendezvous / relay server.

Run this on a machine with a public IP (the game embeds that address in an
obscured form — see game/net.py `relay_endpoint()` — so players never see or type
it; they only exchange a short room code). It lets two players behind home routers
over the internet WITHOUT either of them port-forwarding: both the host and the
joiner open an *outbound* TCP connection to this relay (outbound traverses NAT
freely), the relay pairs them by a short room code, and from then on it simply
forwards raw bytes between the two sockets.

The relay is deliberately dumb: it understands exactly one control frame from
each side and never looks at the game traffic that follows. So the whole game
protocol (game/netsync.py) rides over it unchanged.

Wire format (same length-prefixed JSON the game uses):

    [4-byte big-endian length][UTF-8 JSON]

Handshake (client → relay, one frame):
    host  :  {"t":"host","room":"ABCD"}   — claim a room, then wait for a joiner
    join  :  {"t":"join","room":"ABCD"}   — pair with a waiting host

Relay → client (one frame), then the pipe goes transparent:
    ok    :  {"t":"peer"}                 — paired; start playing
    err   :  {"t":"err","msg":"..."}      — room taken / no such room / timed out

Zero third-party dependencies — plain Python 3 stdlib. Deploy notes at the
bottom of this file (systemd unit + firewall).

Usage:
    python3 relay.py [--host 0.0.0.0] [--port 50577] [--room-timeout 600]
"""

from __future__ import annotations
import argparse
import json
import select
import socket
import struct
import sys
import threading
import time

_HEADER = struct.Struct(">I")
_MAX_MSG = 8 * 1024 * 1024
_HANDSHAKE_TIMEOUT = 10.0        # a fresh connection must send its intent quickly

# room id -> _Room, guarded by _lock. A room lives only while its host waits in
# the lobby ("awaiting player"); once a joiner is paired it leaves the registry.
_rooms: "dict[str, _Room]" = {}
_lock = threading.Lock()
_next_id = 0                     # monotonic room-id source (guarded by _lock)


def _log(msg: str):
    # Line-buffered stdout so `journalctl -f` shows activity live.
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ── framing ───────────────────────────────────────────────────────────────────
def _recv_all(sock: socket.socket, n: int):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _send_frame(sock: socket.socket, obj: dict):
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    sock.sendall(_HEADER.pack(len(payload)) + payload)


def _recv_frame(sock: socket.socket):
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


def _quiet_close(sock: socket.socket):
    try:
        sock.close()
    except OSError:
        pass


# ── pairing ───────────────────────────────────────────────────────────────────
class _Room:
    __slots__ = ("id", "name", "map", "password", "host_sock", "peer_sock",
                 "joined", "hidden")

    def __init__(self, rid: str, name: str, map_: str, host_sock: socket.socket,
                 hidden: bool = False, password: str = ""):
        self.id = rid
        self.name = name
        self.map = map_
        # "" ⇒ open room; otherwise a joiner must present this exact password.
        # Never sent in the browser listing — only a boolean "locked" flag is.
        self.password = password
        self.host_sock = host_sock
        self.peer_sock: "socket.socket | None" = None
        self.joined = threading.Event()
        # Hidden rooms are reconnect slots keyed by a private session token: the
        # dropped player rejoins by that exact token, so they're never listed.
        self.hidden = hidden


def _sock_dead(sock: socket.socket) -> bool:
    """True if a *waiting host* socket has been closed by the peer. A host that is
    parked in the lobby sends nothing, so any readable byte means EOF (a clean
    close); we peek so we never consume real data if that assumption is ever off."""
    try:
        r, _, _ = select.select([sock], [], [], 0)
        if not r:
            return False
        return len(sock.recv(1, socket.MSG_PEEK)) == 0
    except OSError:
        return True


def _pump(src: socket.socket, dst: socket.socket):
    """Copy bytes src → dst until either end closes, then tear both down so the
    opposite pump also unblocks and the room fully collapses."""
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _serve_pair(a: socket.socket, b: socket.socket, room: str):
    """Both sockets are paired: tell each it's live, then relay until one drops."""
    if a is None or b is None:                       # defensive: never pair a hole
        for s in (a, b):
            if s is not None:
                _quiet_close(s)
        return
    for s in (a, b):
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
    try:
        _send_frame(a, {"t": "peer"})
        _send_frame(b, {"t": "peer"})
    except OSError as e:
        _log(f"room {room}: peer handshake failed ({e}); dropping")
        _quiet_close(a)
        _quiet_close(b)
        return
    t = threading.Thread(target=_pump, args=(b, a), daemon=True)
    t.start()
    _pump(a, b)              # runs until a→b closes
    t.join()
    _quiet_close(a)
    _quiet_close(b)
    _log(f"room {room}: session ended")


def _handle_host(sock: socket.socket, name: str, map_: str, addr, room_timeout: float,
                 token: "str | None" = None, password: str = ""):
    global _next_id
    with _lock:
        if token:
            # Reconnect slot: keyed by the match's private session token and hidden
            # from the browser. Reject a duplicate so a stale slot can't hijack it.
            rid = str(token)
            if rid in _rooms:
                _lock_free_send_err(sock, "reconnect slot busy")
                _log(f"reconnect {rid}: rejected, slot already open")
                return
            # The token is itself the secret, so reconnect slots never need a password.
            r = _Room(rid, name, map_, sock, hidden=True)
        else:
            _next_id += 1
            rid = str(_next_id)
            r = _Room(rid, name, map_, sock, password=password)
        _rooms[rid] = r
    # Tell the host its room is open; it then sits on this socket ("awaiting player"
    # for a fresh room, or the reconnect grace window) until we pair it or it drops.
    try:
        _send_frame(sock, {"t": "created", "room": rid})
    except OSError:
        with _lock:
            _rooms.pop(rid, None)
        _quiet_close(sock)
        return
    kind = "reconnect" if token else "room"
    _log(f"{kind} {rid} ({name!r}, map {map_}): hosted by {addr[0]}, awaiting a joiner")

    deadline = time.monotonic() + room_timeout
    while True:
        if r.joined.wait(0.5):
            break                                   # a joiner arrived
        if time.monotonic() > deadline:
            with _lock:
                if _rooms.get(rid) is r:
                    del _rooms[rid]
            _lock_free_send_err(sock, "timed out waiting for an opponent")
            _log(f"room {rid}: timed out")
            return
        if _sock_dead(sock):                        # host closed the lobby / quit
            with _lock:
                if _rooms.get(rid) is r:
                    del _rooms[rid]
            _quiet_close(sock)
            _log(f"room {rid}: host left before anyone joined")
            return

    # A joiner set r.peer_sock; we own both sockets now.
    _serve_pair(sock, r.peer_sock, rid)


def _handle_join(sock: socket.socket, room: str, addr, password: str = ""):
    wrong_pw = False
    with _lock:
        r = _rooms.get(room)
        # A wrong password must NOT consume the room: leave it in the registry so
        # the joiner (or someone else) can try again. Only claim it on a match.
        if r is None:
            paired = None
        elif r.password and password != r.password:
            wrong_pw = True
            paired = None
        else:
            paired = _rooms.pop(room)
            paired.peer_sock = sock
    if paired is None:
        if wrong_pw:
            _lock_free_send_err(sock, "wrong password")
            _log(f"room {room}: join from {addr[0]} gave the wrong password")
        else:
            _lock_free_send_err(sock, "that room is no longer open")
            _log(f"room {room}: join from {addr[0]} found no open host")
        return
    _log(f"room {room}: joined by {addr[0]}, pairing")
    # Hand off to the host thread, which drives the relayed session.
    paired.joined.set()


def _handle_list(sock: socket.socket):
    """Reply with every open room, pruning any whose host has since disconnected,
    then close. The browser reconnects every second or so to refresh."""
    dead = []
    with _lock:
        rooms = []
        for rid, r in _rooms.items():
            if _sock_dead(r.host_sock):
                dead.append((rid, r))
                continue
            if r.hidden:                            # reconnect slots are never listed
                continue
            rooms.append({"id": rid, "name": r.name, "map": r.map,
                          "locked": bool(r.password)})
        for rid, r in dead:
            if _rooms.get(rid) is r:
                del _rooms[rid]
    # Don't signal the dead rooms' hosts here: each host thread polls its own
    # socket every 0.5s and cleans itself up. Setting joined would wake it with
    # no peer_sock and send it into _serve_pair(None).
    try:
        _send_frame(sock, {"t": "rooms", "rooms": rooms})
    except OSError:
        pass
    _quiet_close(sock)


def _lock_free_send_err(sock: socket.socket, msg: str):
    try:
        _send_frame(sock, {"t": "err", "msg": msg})
    except OSError:
        pass
    _quiet_close(sock)


def _handle(sock: socket.socket, addr, room_timeout: float):
    try:
        sock.settimeout(_HANDSHAKE_TIMEOUT)
        hello = _recv_frame(sock)
        sock.settimeout(None)
    except OSError:
        _quiet_close(sock)
        return
    if not isinstance(hello, dict):
        _quiet_close(sock)
        return
    kind = hello.get("t")
    if kind == "list":
        _handle_list(sock)
        return
    if kind == "host":
        name = str(hello.get("name", "") or "Unnamed room")[:40]
        map_ = str(hello.get("map", "") or "?")[:40]
        token = hello.get("reconnect")
        token = str(token)[:32] if token else None
        password = str(hello.get("password", "") or "")[:64]
        _handle_host(sock, name, map_, addr, room_timeout, token=token,
                     password=password)
        return
    if kind == "join":
        room = str(hello.get("room", "")).strip()
        if not room or len(room) > 16:
            _lock_free_send_err(sock, "bad room id")
            return
        password = str(hello.get("password", "") or "")[:64]
        _handle_join(sock, room, addr, password=password)
        return
    _lock_free_send_err(sock, "expected host, join or list")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Penumbra WAN relay server")
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default all)")
    ap.add_argument("--port", type=int, default=50577, help="listen port")
    ap.add_argument("--room-timeout", type=float, default=600.0,
                    help="seconds a host waits for a joiner before giving up")
    args = ap.parse_args(argv)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((args.host, args.port))
    except OSError as e:
        _log(f"FATAL: cannot bind {args.host}:{args.port} — {e}")
        return 1
    srv.listen(64)
    _log(f"relay listening on {args.host}:{args.port} "
         f"(room timeout {args.room_timeout:.0f}s)")

    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=_handle, args=(conn, addr, args.room_timeout),
                             daemon=True).start()
    except KeyboardInterrupt:
        _log("shutting down")
    finally:
        _quiet_close(srv)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ─────────────────────────────────────────────────────────────────────────────
# DEPLOY ON THE PUBLIC SERVER  (replace <SERVER_IP> with your droplet's IP)
# ─────────────────────────────────────────────────────────────────────────────
#
#   1. Copy just this one file up (no game code or PyQt needed on the server):
#          scp relay.py root@<SERVER_IP>:/opt/penumbra-relay/relay.py
#
#   2. Open the port on the host firewall AND the DigitalOcean cloud firewall:
#          ssh root@<SERVER_IP> 'ufw allow 50577/tcp'
#      (DO firewall: add inbound TCP 50577 in the control panel — ufw alone is
#       not enough on DO droplets.)
#
#   3. Run it as a service so it survives reboots and logout. Create
#      /etc/systemd/system/penumbra-relay.service :
#
#          [Unit]
#          Description=Penumbra WAN relay
#          After=network.target
#
#          [Service]
#          ExecStart=/usr/bin/python3 /opt/penumbra-relay/relay.py --port 50577
#          Restart=always
#          User=nobody
#          AmbientCapabilities=
#          NoNewPrivileges=true
#
#          [Install]
#          WantedBy=multi-user.target
#
#      Then:
#          systemctl daemon-reload
#          systemctl enable --now penumbra-relay
#          journalctl -u penumbra-relay -f      # watch rooms being paired
#
#   4. Bake the address into the client: it lives obscured in game/net.py as
#      _RELAY_BLOB. To point the game at a different server, re-encode "host:port"
#      with the XOR+base64 scheme in relay_endpoint(), or just set the env var
#      PENUMBRA_RELAY=host:port. Then in-game: Online → Create Room → share code.
