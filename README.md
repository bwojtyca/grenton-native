# grenton-native

A **clean-room** Python client for Grenton's *native* CLU protocol — the encrypted
UDP channel that Object Manager and the MyGrenton mobile app use to talk to CLUs
directly, **without the HTTP Gate**.

The distinguishing idea: the AES key + IV and the CLU topology are read from the
**Object Manager project file (`.omp`)** you already have — so there is **no
dependency on MyGrenton**, and, unlike VCLU/OpenGr8on-style bridges, **nothing is
added to or changed in the Grenton project**. This is a pure external client that
subscribes and sends actions over UDP. If it dies, the Grenton system is
completely untouched — an installer sees a 100% standard project.

> Status: **spike / proof-of-concept.** Goal 1 is to prove we can establish an
> encrypted session with a real CLU and receive live state *push* (`clientReport`).
> If that holds, this becomes the transport for a modern, push-native Home
> Assistant integration.

## Why this over the alternatives

| Approach | Touches OM project? | Needs MyGrenton? | Extra service? | Push? |
|---|---|---|---|---|
| HTTP Gate integration | yes (native objects) | no | no | polling / manual wiring |
| MyGrenton reverse (sszczep) | no | **yes** | no | yes |
| OpenGr8on VCLU + MQTT | **yes (foreign VCLU)** | no | **yes (Docker+broker)** | yes |
| **grenton-native (this)** | **no** | **no** | **no** | **yes** |

The one honest cost shared by every non-HTTP path: it rides an **unofficial,
reverse-engineered protocol**, so it can break on a Grenton firmware change and
carries no vendor support. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Layout

```
src/grenton_native/
  cipher.py     # AES-128-CBC / PKCS7 (the CLU command-channel scheme)
  omp.py        # read cipher key/IV + CLU topology from a .omp
  protocol.py   # wire message encode/decode (req/resp, clientRegister/Report)
  client.py     # asyncio UDP client (cmd→1234, report listener→4344)
spikes/
  spike_listen.py   # run against real hardware: checkAlive + live subscription
tests/          # offline unit tests (cipher, protocol, .omp parsing)
docs/PROTOCOL.md
```

## Running the spike (against your own hardware)

```bash
python -m pip install -e '.[dev]'           # or: pip install cryptography
python spikes/spike_listen.py --omp /path/to/project.omp -v
```

What it does, in order (each step is independently informative):

1. Reads the AES key/IV and CLU list from the `.omp` (**key value is never printed**).
2. Sends `checkAlive()` to one CLU and decrypts the reply — this alone proves the
   key + transport work.
3. Subscribes (`clientRegister`) to that CLU's own system features and prints the
   live `clientReport` pushes for a few seconds (the CLU clock ticks, so pushes
   are guaranteed).
4. On exit, sends `clientDestroy` and closes — leaving no subscription behind.

Useful flags: `--clu <serial|ip>`, `--host-ip <ip>`, `--object <name>`,
`--indices 0-15`, `--duration 30`, `--no-newline`, `--default-key`.

## Safety / secrets

- A `.omp` contains the project **AES key** (in `properties.xml`). It is treated
  as a secret: `*.omp` is git-ignored and the tools never print key/IV material.
- The spike is **read-only** toward the Grenton system: `checkAlive` + subscribe +
  destroy. It does not send device actions.

## Roadmap (risk reduction — after the spike proves out)

- **Coexistence check** at scale (multiple CLUs, many subscribed keys).
- **Key-rotation resilience**: re-import `.omp` when the installer changes the
  project key.
- Investigate a **Lua-assisted / non-destructive** variant so re-uploading a
  configuration in OM cannot disturb Home Assistant.
- Package the transport behind a small, typed API and build the push-native HA
  integration on top (coordinator + auto-discovery from `.omp`).

## License / provenance

Clean-room: implemented from an understanding of the protocol, **not** by copying
GPL/AGPL sources. Protocol *facts* were confirmed from OpenGr8on's CC BY-SA
documentation and public reverse-engineering write-ups — see
[`docs/PROTOCOL.md`](docs/PROTOCOL.md). Project license: **TBD.**
