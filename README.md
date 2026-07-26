# grenton-native

[![CI](https://github.com/bwojtyca/grenton-native/actions/workflows/ci.yml/badge.svg)](https://github.com/bwojtyca/grenton-native/actions/workflows/ci.yml)
[![Validate](https://github.com/bwojtyca/grenton-native/actions/workflows/validate.yml/badge.svg)](https://github.com/bwojtyca/grenton-native/actions/workflows/validate.yml)

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
custom_components/grenton_native/      # Home Assistant custom integration
  __init__.py        # setup (HA-free at import time)
  config_flow.py     # point at a .omp on the host
  monitor.py         # runtime: sessions + event ring + checkAlive loop
  panel.py           # sidebar panel + websocket API
  panel.js           # live communication-monitor UI (vanilla, no build step)
  native/            # ← the protocol library (HA-free, reusable, tested)
    cipher.py        #   AES-128-CBC / PKCS7 (the CLU command-channel scheme)
    omp.py           #   read cipher key/IV + CLU topology from a .omp
    protocol.py      #   wire encode/decode (req/resp, clientRegister/Report)
    client.py        #   asyncio UDP client (cmd→1234, report listener→4344)
    events.py        #   WireEvent + bounded EventRing for the monitor
spikes/spike_listen.py   # standalone: checkAlive + live subscription (no HA)
tests/                   # offline unit tests (cipher, protocol, omp, events)
.github/workflows/       # CI: ruff + pytest · Validate: hassfest + HACS
docs/PROTOCOL.md
```

The `native/` package has **no Home Assistant dependency** and the integration's
`__init__.py` keeps all HA imports lazy — so the same protocol code powers both
the HA integration and the standalone spike, and CI can test it without HA.

## Run inside Home Assistant (recommended)

Your HA host is already on the Grenton LAN, so this is the easiest way to try it.

1. Copy `custom_components/grenton_native/` into your HA `config/custom_components/`
   (or add this repo to HACS as a custom repository, category *Integration*).
2. Put your Object Manager project on the host, e.g. `config/grenton_native/project.omp`.
3. **Settings → Devices & Services → Add Integration → “Grenton Native”**, and give
   it the `.omp` path.
4. Open the **Grenton Native** entry in the sidebar — the panel shows each CLU's
   liveness and a **live stream of the native communication** (requests,
   responses, `clientReport` pushes), with a per-CLU *Ping* button.

## Running the standalone spike (no Home Assistant)

```bash
pip install cryptography
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
