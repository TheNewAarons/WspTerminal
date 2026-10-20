"""Identidad del peer en sockets Unix (equivalente a getpeereid)."""

import socket
import struct
from typing import Final

_SOL_LOCAL: Final = 0
_LOCAL_PEERCRED: Final = 0x001  # <sys/un.h>
_XUCRED_VERSION: Final = 0


def peer_uid(sock: socket.socket) -> int | None:
    """UID del proceso en el otro extremo; None si no se puede determinar (fail-closed)."""
    try:
        so_peercred: int | None = getattr(socket, "SO_PEERCRED", None)
        if so_peercred is not None:  # Linux: struct ucred {pid, uid, gid}
            data = sock.getsockopt(socket.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
            return int(struct.unpack_from("3i", data)[1])
        data = sock.getsockopt(_SOL_LOCAL, _LOCAL_PEERCRED, 256)  # macOS: struct xucred
        version, uid = struct.unpack_from("=II", data)  # (cr_version, cr_uid)
        return int(uid) if version == _XUCRED_VERSION else None
    except (OSError, struct.error):
        return None
