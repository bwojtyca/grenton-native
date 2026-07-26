"""Parse a synthetic .omp. Uses DUMMY key material — never a real project key."""

import base64
import io
import zipfile

import pytest

from grenton_native.omp import load_omp_bytes

DUMMY_KEY = b"\x00" * 16
DUMMY_IV = b"\x11" * 16

PROPERTIES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<ProjectProperties>
  <projectCipherKey>
    <keyBytes>{base64.b64encode(DUMMY_KEY).decode()}</keyBytes>
    <ivBytes>{base64.b64encode(DUMMY_IV).decode()}</ivBytes>
  </projectCipherKey>
</ProjectProperties>
""".encode()

# One CLU node (nameOnCLU + ipAddress) and one device object (nameOnCLU only) —
# the device must NOT be mistaken for a CLU.
SYSTEM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Project>
  <TreeObject>
    <specificObject>
      <clu>
        <nameOnCLU>221011038</nameOnCLU>
        <ipAddress>192.168.0.10</ipAddress>
      </clu>
    </specificObject>
  </TreeObject>
  <TreeObject>
    <specificObject>
      <nameOnCLU>DOU1234</nameOnCLU>
      <type>DOUT</type>
    </specificObject>
  </TreeObject>
</Project>
"""


def _make_omp(properties=PROPERTIES_XML, system=SYSTEM_XML) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("properties.xml", properties)
        z.writestr("system.xml", system)
    return buf.getvalue()


def test_loads_cipher_key_and_iv():
    project = load_omp_bytes(_make_omp())
    assert project.cipher_key == DUMMY_KEY
    assert project.cipher_iv == DUMMY_IV


def test_parses_only_the_clu_not_devices():
    project = load_omp_bytes(_make_omp())
    assert len(project.clus) == 1
    clu = project.clus[0]
    assert clu.serial == "221011038"
    assert clu.ip == "192.168.0.10"
    assert clu.object_name == "CLU221011038"


def test_clu_selector_by_serial_and_ip():
    project = load_omp_bytes(_make_omp())
    assert project.clu("221011038") is project.clus[0]
    assert project.clu("192.168.0.10") is project.clus[0]
    assert project.clu("nope") is None


def test_missing_cipher_key_raises():
    bad_props = b"<ProjectProperties></ProjectProperties>"
    with pytest.raises(ValueError, match="projectCipherKey"):
        load_omp_bytes(_make_omp(properties=bad_props))


def test_not_a_zip_raises():
    with pytest.raises(ValueError, match="not a valid .omp"):
        load_omp_bytes(b"this is not a zip")


def test_missing_member_raises():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("properties.xml", PROPERTIES_XML)  # no system.xml
    with pytest.raises(ValueError, match="system.xml not found"):
        load_omp_bytes(buf.getvalue())
