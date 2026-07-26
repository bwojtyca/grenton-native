"""Read the cipher key/IV and CLU topology from an Object Manager ``.omp``.

A ``.omp`` is a ZIP. We need two members:

* ``properties.xml`` → ``projectCipherKey`` → ``keyBytes`` / ``ivBytes``
  (base64, used raw as the AES-128 key + IV for the command channel).
* ``system.xml`` → the CLU hardware nodes, each carrying ``nameOnCLU`` (serial)
  and ``ipAddress``.

The key material is a secret; callers must not log it.
"""

from __future__ import annotations

import base64
import io
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class CluInfo:
    serial: str
    ip: str | None
    # Object name to address the CLU itself over the protocol (e.g. its own
    # system features). Object Manager names the CLU object "CLU<serial>".
    object_name: str


@dataclass
class OmpProject:
    cipher_key: bytes
    cipher_iv: bytes
    clus: list[CluInfo]

    def clu(self, selector: str) -> CluInfo | None:
        """Find a CLU by serial or by IP."""
        for c in self.clus:
            if c.serial == selector or c.ip == selector:
                return c
        return None


def load_omp(path: str) -> OmpProject:
    with open(path, "rb") as fh:
        return load_omp_bytes(fh.read())


def load_omp_bytes(data: bytes) -> OmpProject:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise ValueError("not a valid .omp (ZIP) archive") from err
    with archive:
        key, iv = _parse_cipher_key(_read_member(archive, "properties.xml"))
        clus = _parse_clus(_read_member(archive, "system.xml"))
    return OmpProject(cipher_key=key, cipher_iv=iv, clus=clus)


def _read_member(archive: zipfile.ZipFile, basename: str) -> bytes:
    name = next(
        (n for n in archive.namelist() if n.rsplit("/", 1)[-1] == basename),
        None,
    )
    if name is None:
        raise ValueError(f"{basename} not found in .omp")
    return archive.read(name)


def _find_first(root: ET.Element, tag: str) -> ET.Element | None:
    """First element with this tag anywhere under (or equal to) root."""
    if root.tag == tag:
        return root
    return next((el for el in root.iter() if el.tag == tag), None)


def _parse_cipher_key(properties_xml: bytes) -> tuple[bytes, bytes]:
    root = ET.fromstring(properties_xml)
    key_el = _find_first(root, "keyBytes")
    iv_el = _find_first(root, "ivBytes")
    if key_el is None or iv_el is None or not (key_el.text and iv_el.text):
        raise ValueError(
            "projectCipherKey (keyBytes/ivBytes) not found in properties.xml — "
            "is the project open in Object Manager? (OM writes it on open)"
        )
    key = base64.b64decode(key_el.text.strip())
    iv = base64.b64decode(iv_el.text.strip())
    return key, iv


def _parse_clus(system_xml: bytes) -> list[CluInfo]:
    root = ET.fromstring(system_xml)
    found: dict[str, CluInfo] = {}
    # Only the CLU hardware node carries BOTH nameOnCLU and ipAddress as direct
    # children; device objects carry nameOnCLU alone. That pair isolates CLUs.
    for el in root.iter():
        serial_el = el.find("nameOnCLU")
        ip_el = el.find("ipAddress")
        if serial_el is None or ip_el is None or not serial_el.text:
            continue
        serial = serial_el.text.strip()
        ip = (ip_el.text or "").strip() or None
        # nameOnCLU already carries the "CLU" prefix in real projects
        # (e.g. "CLU221011038"); only add it when it's missing so we never
        # end up addressing "CLUCLU…".
        object_name = serial if serial.upper().startswith("CLU") else f"CLU{serial}"
        found.setdefault(
            serial,
            CluInfo(serial=serial, ip=ip, object_name=object_name),
        )
    return sorted(found.values(), key=lambda c: c.serial)
