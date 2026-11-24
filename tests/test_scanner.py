"""Scanning a folder."""


def test_scan_reads_fingerprints_in_parallel(settings, tmp_path):
    from mam.helpers.local_db import session_scope
    from mam.manager.scanner import scan_folder

    for i in range(40):
        (tmp_path / f"f{i}.mp3").write_bytes(str(i).encode() * 50)
    (tmp_path / "same.mp3").write_bytes(b"0" * 50)  # byte-identical to f0.mp3
    with session_scope() as s:
        result = scan_folder(s, tmp_path)
    assert (result.found, result.new, result.duplicates) == (41, 40, 1)
    with session_scope() as s:
        assert scan_folder(s, tmp_path).known == 40
