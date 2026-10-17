from mam.helpers.local_db.hashing import CHUNK, fingerprint, sha256


def test_fingerprint_is_stable_and_content_sensitive(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"x" * 1000)
    b.write_bytes(b"x" * 999 + b"y")
    assert fingerprint(a) == fingerprint(a)
    assert fingerprint(a) != fingerprint(b)


def test_fingerprint_samples_large_files(tmp_path):
    size = 4 * CHUNK
    base = bytearray(size)
    a = tmp_path / "a.bin"
    a.write_bytes(base)
    middle = bytearray(base)
    middle[size // 2] = 1  # inside the sampled middle chunk
    b = tmp_path / "b.bin"
    b.write_bytes(middle)
    unsampled = bytearray(base)
    unsampled[CHUNK + 10] = 1  # between first and middle chunk: not sampled
    c = tmp_path / "c.bin"
    c.write_bytes(unsampled)
    assert fingerprint(a) != fingerprint(b)
    assert fingerprint(a) == fingerprint(c)  # collision -> resolved by sha256
    assert sha256(a) != sha256(c)
