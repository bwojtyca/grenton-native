# Grenton native CLU protocol — clean-room notes

These are the protocol *facts* this project implements. They were confirmed from
public sources (OpenGr8on's CC BY-SA `COMMUNICATION.md`, the `domktorymysli.org`
reverse-engineering write-up, and the structure of a real `.omp`). No third-party
source code was copied; the implementation here is original.

## Transport

- **UDP.** Command/query port on the CLU: **`1234/udp`**.
- Subscription (`clientReport`) push is delivered to **`4344/udp`** on the client
  (the client tells the CLU where to send them in the `clientRegister` call).
- Max message size ≈ **2000 bytes** → subscriptions are chunked.
- Request/response are correlated by an 8-hex-char **message id** (OM emits 6, the
  CLU always answers with 8, left-padded with zeros; 8 works both ways).

## Encryption

- **AES-128-CBC + PKCS7** (a.k.a. PKCS5), applied to the whole plaintext message.
- **One static key + IV** for every message on the command channel (this is the
  protocol's design, not our choice — a known weakness).
- Key + IV are **16 bytes each**, stored **base64** in the `.omp` under
  `properties.xml → ProjectProperties → projectCipherKey → {keyBytes, ivBytes}`,
  and used **raw** (base64-decode only; no de-obfuscation).
- Factory-default key/IV (a CLU before a project assigns its own) are public
  constants; see `cipher.py`.

## Wire format

```
REQUEST   req:<HOST_IP>:<MSG_ID>:<PAYLOAD>\r\n
RESPONSE  resp:<CLU_IP>:<MSG_ID>:<PAYLOAD>
```

`split(":", 3)` → exactly 4 fields. Unsolicited notifications (ongoing reports)
use `MSG_ID == "00000000"`.

### Payloads

- **Keep-alive / arbitrary Lua:** `checkAlive()` → reply payload is the CLU serial
  (hex), or `true` (gate), or `emergency`. The same channel can execute any Lua
  function declared on the CLU, e.g. `DOUT_8565:execute(2, 0)`.
- **Subscribe:**
  `SYSTEM:clientRegister("<HOST_IP>",<REPORT_PORT>,<SESSION>,{{OBJ,IDX},{OBJ,IDX},...})`
  - `OBJ` is an object name on the CLU (e.g. the CLU's own name `CLU<serial>`, or a
    device like `DOU1234`); `IDX` is the numeric feature index.
  - Reply (and every later push) is a `clientReport`:
    `clientReport:<SESSION>:{v1,v2,...}` — values are Lua literals
    (`nil`/`true`/`false`/number/`"string"`), positionally matching the requested
    keys.
- **Unsubscribe:** `SYSTEM:clientDestroy("<HOST_IP>",<REPORT_PORT>,<SESSION>)`.

## Coexistence model

We register as **an additional, independent client** (like a second phone running
MyGrenton). We do not hijack existing sessions and we add nothing to the CLU
project. Open questions to validate on real hardware: how many concurrent client
subscriptions a CLU tolerates, and behaviour while OM is also connected.
