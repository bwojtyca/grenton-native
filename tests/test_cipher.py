import pytest

from grenton_native.cipher import DEFAULT_IV_B64, DEFAULT_KEY_B64, GrentonCipher


def test_round_trip_default():
    cipher = GrentonCipher.default()
    plaintext = b"req:192.168.0.9:00abcdef:checkAlive()\r\n"
    assert cipher.decrypt(cipher.encrypt(plaintext)) == plaintext


def test_round_trip_custom_key():
    cipher = GrentonCipher(b"\x00" * 16, b"\x01" * 16)
    for msg in (b"", b"a", b"x" * 15, b"y" * 16, b"z" * 17, b"long" * 500):
        assert cipher.decrypt(cipher.encrypt(msg)) == msg


def test_from_base64_matches_default():
    a = GrentonCipher.from_base64(DEFAULT_KEY_B64, DEFAULT_IV_B64)
    b = GrentonCipher.default()
    blob = b"hello"
    assert a.decrypt(b.encrypt(blob)) == blob


def test_decrypt_rejects_non_block_aligned():
    cipher = GrentonCipher.default()
    assert cipher.decrypt(b"not-block-aligned") is None
    assert cipher.decrypt(b"") is None


def test_decrypt_wrong_key_does_not_return_plaintext():
    good = GrentonCipher(b"\x00" * 16, b"\x02" * 16)
    other = GrentonCipher(b"\xff" * 16, b"\x02" * 16)
    ct = good.encrypt(b"secret-payload-here")
    assert other.decrypt(ct) != b"secret-payload-here"  # None or garbage, never the plaintext


@pytest.mark.parametrize(
    ("key_len", "iv_len"),
    [(8, 16), (15, 16), (16, 8), (16, 15), (16, 17)],
)
def test_invalid_lengths_raise(key_len, iv_len):
    with pytest.raises(ValueError):
        GrentonCipher(b"\x00" * key_len, b"\x00" * iv_len)
