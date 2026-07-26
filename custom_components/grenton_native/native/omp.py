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
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

# Object-id prefixes that denote a real controllable/readable object (the part
# after "->" in a grenton_id, e.g. DOU6691, ROL7324).
_OBJECT_ID_RE = re.compile(
    r"^(DOU|DIN|ROL|LED|DIM|ZWA|ONE|TEM|ANA|PAN|MOD|DAL|SEN|THE|PWM|ADC|VAL|FLA|PRE|PID|SAT)[0-9A-Za-z]"
)
# Top-level tree containers are named like "CLU221011038_clu" / "..._alarm".
_CLU_CONTAINER_RE = re.compile(r"^(CLU\d+)_")


@dataclass(frozen=True)
class CluInfo:
    serial: str
    ip: str | None
    # Object name to address the CLU itself over the protocol (e.g. its own
    # system features). Object Manager names the CLU object "CLU<serial>".
    object_name: str


@dataclass(frozen=True)
class Feature:
    """A built-in feature ("cecha wbudowana") of an object, from the .omp."""

    name: str
    index: int | None        # protocol feature index (subscribe/get by this)
    param_type: str | None   # NUMBER / STRING / ...
    unit: str | None
    access: str | None       # accessType, e.g. ALL / READONLY
    constraint: str | None   # constrainAsString, e.g. "{0,1}" or "[0-10000]"
    init_value: str | None
    hint: str | None
    visible: bool


@dataclass(frozen=True)
class ObjectEvent:
    """A configured event/callback on an object (e.g. OnSwitchOn)."""

    name: str


@dataclass(frozen=True)
class CluObject:
    clu: str | None          # CLU serial (e.g. "CLU221011038")
    obj_id: str              # name on the CLU (e.g. "DOU6691")
    grenton_id: str          # "CLU221011038->DOU6691"
    name: str | None         # user-facing name from OM
    type: str | None         # OM object type (e.g. "DOUT", "ROLLER_SHUTTER")
    features: tuple[Feature, ...] = ()
    events: tuple[ObjectEvent, ...] = ()


@dataclass
class OmpProject:
    cipher_key: bytes
    cipher_iv: bytes
    clus: list[CluInfo]
    objects: list[CluObject] = field(default_factory=list)

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
        system_xml = _read_member(archive, "system.xml")
        clus = _parse_clus(system_xml)
        objects = _parse_objects(system_xml)
    return OmpProject(cipher_key=key, cipher_iv=iv, clus=clus, objects=objects)


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


def _parse_objects(system_xml: bytes) -> list[CluObject]:
    """Discover controllable/readable objects, tracking which CLU they belong to.

    Mirrors how Object Manager nests objects under CLU tree containers and uses
    XStream ``reference`` attributes to share element definitions.
    """
    root = ET.fromstring(system_xml)
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}

    def resolve(el: ET.Element) -> ET.Element:
        ref = el.get("reference")
        return by_id.get(ref, el) if ref else el

    def child_text(el: ET.Element, tag: str) -> str | None:
        node = el.find(tag)
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
        return None

    objects: dict[str, CluObject] = {}

    def walk(node: ET.Element, clu: str | None) -> None:
        name = child_text(node, "name")
        match = _CLU_CONTAINER_RE.match(name) if name else None
        if match:
            clu = match.group(1)
        spec = node.find("specificObject")
        if spec is not None:
            spec = resolve(spec)
            obj_id = child_text(spec, "nameOnCLU")
            obj_type = child_text(spec, "type") or child_text(spec, "typeName")
            if obj_id and _OBJECT_ID_RE.match(obj_id) and not obj_id.startswith("CLU"):
                grenton_id = f"{clu}->{obj_id}" if clu else obj_id
                objects.setdefault(
                    grenton_id,
                    CluObject(
                        clu=clu,
                        obj_id=obj_id,
                        grenton_id=grenton_id,
                        name=child_text(spec, "name"),
                        type=obj_type,
                        features=_parse_features(spec, resolve, child_text),
                        events=_parse_events(spec, resolve, child_text),
                    ),
                )
        children = node.find("children")
        if children is not None:
            for sub in children.findall("TreeObject"):
                walk(sub, clu)

    for node in root.findall("TreeObject"):
        walk(node, None)
    return sorted(objects.values(), key=lambda o: o.grenton_id)


def _parse_features(spec, resolve, child_text) -> tuple[Feature, ...]:
    container = spec.find("features")
    if container is None:
        return ()
    container = resolve(container)
    out: list[Feature] = []
    for raw in list(container):
        el = resolve(raw)
        name = child_text(el, "name")
        if not name:
            continue
        index_txt = child_text(el, "index")
        try:
            index = int(index_txt) if index_txt is not None else None
        except ValueError:
            index = None
        type_el = el.find("type")
        param_type = child_text(resolve(type_el), "paramType") if type_el is not None else None
        out.append(
            Feature(
                name=name,
                index=index,
                param_type=param_type,
                unit=child_text(el, "unit"),
                access=child_text(el, "accessType"),
                constraint=child_text(el, "constrainAsString"),
                init_value=child_text(el, "initValue"),
                hint=child_text(el, "hint"),
                visible=(child_text(el, "visible") or "").lower() == "true",
            )
        )
    return tuple(out)


def _parse_events(spec, resolve, child_text) -> tuple[ObjectEvent, ...]:
    container = spec.find("events")
    if container is None:
        return ()
    container = resolve(container)
    out: list[ObjectEvent] = []
    for raw in list(container):
        el = resolve(raw)
        name = child_text(el, "name") or el.tag
        out.append(ObjectEvent(name=name))
    return tuple(out)
