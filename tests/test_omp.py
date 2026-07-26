"""Parse a synthetic .omp. Uses DUMMY key material — never a real project key."""

import base64
import io
import zipfile

import pytest

from custom_components.grenton_native.native.omp import load_omp_bytes

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

# One CLU node (nameOnCLU + ipAddress), plus a device object nested under a CLU
# tree container so grenton_id gets the CLU prefix. The device carries a built-in
# feature and an event, mirroring the real .omp structure.
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
    <name>CLU221011038_clu</name>
    <children>
      <TreeObject>
        <specificObject>
          <name>Lampa salon</name>
          <nameOnCLU>DOU1234</nameOnCLU>
          <type>DOUT</type>
          <features>
            <EmbeddedFeature>
              <name>Value</name>
              <type><paramType>NUMBER</paramType></type>
              <accessType>ALL</accessType>
              <unit>bool</unit>
              <index>0</index>
              <constrainAsString>{0,1}</constrainAsString>
              <initValue>0</initValue>
              <visible>true</visible>
            </EmbeddedFeature>
          </features>
          <events>
            <Event><name>OnSwitchOn</name></Event>
          </events>
        </specificObject>
      </TreeObject>
    </children>
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


def test_clu_prefixed_serial_is_not_double_prefixed():
    """Real projects store nameOnCLU already prefixed (e.g. 'CLU221011038');
    object_name must not become 'CLUCLU221011038'."""
    system = SYSTEM_XML.replace(b"<nameOnCLU>221011038</nameOnCLU>",
                                b"<nameOnCLU>CLU221011038</nameOnCLU>")
    project = load_omp_bytes(_make_omp(system=system))
    clu = project.clus[0]
    assert clu.serial == "CLU221011038"
    assert clu.object_name == "CLU221011038"


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


def test_parses_object_with_features_and_events():
    project = load_omp_bytes(_make_omp())
    assert len(project.objects) == 1
    obj = project.objects[0]
    assert obj.grenton_id == "CLU221011038->DOU1234"
    assert obj.obj_id == "DOU1234"
    assert obj.clu == "CLU221011038"
    assert obj.type == "DOUT"
    assert obj.name == "Lampa salon"

    assert len(obj.features) == 1
    value = obj.features[0]
    assert value.name == "Value"
    assert value.index == 0
    assert value.param_type == "NUMBER"
    assert value.unit == "bool"
    assert value.access == "ALL"
    assert value.constraint == "{0,1}"
    assert value.visible is True

    assert [e.name for e in obj.events] == ["OnSwitchOn"]


def test_the_clu_node_is_not_parsed_as_an_object():
    # The <clu> hardware node has nameOnCLU "221011038" but must not appear as a
    # controllable object (it isn't an object-id prefix and is filtered out).
    project = load_omp_bytes(_make_omp())
    assert all(not o.obj_id.startswith("CLU") for o in project.objects)
