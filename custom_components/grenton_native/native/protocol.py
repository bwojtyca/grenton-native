"""Wire-level encode/decode for the Grenton native protocol.

    REQUEST   req:<HOST_IP>:<MSG_ID>:<PAYLOAD>\\r\\n
    RESPONSE  resp:<CLU_IP>:<MSG_ID>:<PAYLOAD>

Only the string layer lives here; encryption is applied by the caller. Kept pure
and dependency-free so it is trivially unit-testable.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

# The blog capture shows OM terminating requests with CRLF. The CLU tolerates its
# absence (other clients omit it), so it is overridable — but we default to the
# documented form.
REQUEST_TERMINATOR = "\r\n"

CLIENT_REPORT_PREFIX = "clientReport:"
NOTIFICATION_MSG_ID = "00000000"


def new_msg_id() -> str:
    """8 hex chars, lower-case (the width the CLU always answers with)."""
    return secrets.token_hex(4)


def build_request(
    host_ip: str,
    payload: str,
    msg_id: str | None = None,
    terminator: str = REQUEST_TERMINATOR,
) -> tuple[str, str]:
    """Return ``(msg_id, raw_plaintext)`` for a request."""
    msg_id = (msg_id or new_msg_id()).lower()
    return msg_id, f"req:{host_ip}:{msg_id}:{payload}{terminator}"


@dataclass(frozen=True)
class Response:
    clu_ip: str
    msg_id: str
    payload: str

    @property
    def is_notification(self) -> bool:
        return self.msg_id == NOTIFICATION_MSG_ID

    @property
    def is_client_report(self) -> bool:
        return self.payload.startswith(CLIENT_REPORT_PREFIX)


def parse_response(text: str) -> Response | None:
    """Parse a decrypted ``resp:...`` message, or ``None`` if it is not one."""
    text = text.strip()
    if not text.startswith("resp:"):
        return None
    parts = text.split(":", 3)
    if len(parts) != 4:
        return None
    _, clu_ip, msg_id, payload = parts
    return Response(clu_ip=clu_ip, msg_id=msg_id.lower(), payload=payload)


# ── Payload builders ────────────────────────────────────────────────────────

def check_alive_payload() -> str:
    return "checkAlive()"


def client_register_payload(
    host_ip: str,
    report_port: int,
    session: int,
    keys: list[tuple[str, int]],
) -> str:
    """`SYSTEM:clientRegister("<ip>",<port>,<session>,{{OBJ,IDX},...})`."""
    inner = ",".join(f"{{{obj},{idx}}}" for obj, idx in keys)
    return f'SYSTEM:clientRegister("{host_ip}",{report_port},{session},{{{inner}}})'


def client_destroy_payload(host_ip: str, report_port: int, session: int) -> str:
    return f'SYSTEM:clientDestroy("{host_ip}",{report_port},{session})'


# ── clientReport parsing ─────────────────────────────────────────────────────

LuaValue = str | int | float | bool | None


def parse_client_report(payload: str) -> tuple[int, list[LuaValue]] | None:
    """Parse ``clientReport:<session>:{v1,v2,...}`` → ``(session, [values])``."""
    if not payload.startswith(CLIENT_REPORT_PREFIX):
        return None
    rest = payload[len(CLIENT_REPORT_PREFIX):]
    session_str, sep, values_str = rest.partition(":")
    if not sep:
        return None
    try:
        session = int(session_str)
    except ValueError:
        return None
    values_str = values_str.strip()
    if values_str.startswith("{") and values_str.endswith("}"):
        values_str = values_str[1:-1]
    return session, _parse_lua_values(values_str)


def _parse_lua_values(body: str) -> list[LuaValue]:
    """Split a comma-separated Lua value list, respecting quoted strings.

    Quoted tokens are always returned as ``str`` (so `"true"` stays a string);
    bare tokens are cast to bool/nil/number/str.
    """
    if body.strip() == "":
        return []
    values: list[LuaValue] = []
    buf: list[str] = []
    in_string = False
    was_quoted = False
    for ch in body:
        if in_string:
            if ch == '"':
                in_string = False
            else:
                buf.append(ch)
        elif ch == '"':
            in_string = True
            was_quoted = True
        elif ch == ",":
            values.append("".join(buf) if was_quoted else _cast_bare("".join(buf)))
            buf, was_quoted = [], False
        else:
            buf.append(ch)
    values.append("".join(buf) if was_quoted else _cast_bare("".join(buf)))
    return values


def _cast_bare(token: str) -> LuaValue:
    token = token.strip()
    if token == "" or token.lower() == "nil":
        return None
    low = token.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(token)
    except ValueError:
        try:
            return float(token)
        except ValueError:
            return token
