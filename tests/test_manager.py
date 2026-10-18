"""Media manager: scan, file list, job queue, Renamer runs and reviews, through HTTP."""

import shutil

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mam.helpers.local_db import models as m
from mam.helpers.local_db import session_scope
from mam.manager.app import create_app
from mam.manager.queue import JobQueue, enqueue


@pytest.fixture
def client(settings):
    with TestClient(create_app(start_queue=False)) as c:
        yield c


def run_queue():
    """Run every queued job synchronously."""
    queue = JobQueue()
    while True:
        claimed = [queue.claim(pool) for pool in ("io", "cpu", "ml")]
        claimed = [j for j in claimed if j]
        if not claimed:
            return
        for job_id in claimed:
            queue.execute(job_id)


@pytest.fixture
def archive(settings, tone):
    root = settings.media_sample_files_path
    files = {
        "From JVD (8.9.11)/KKS_S.B. 1.19.30_28.8.11_Oslo_ .mp3": "clean",
        "Serbia-summercamp-6-13-jul-2012/R09_0004.MP3": "flagged",
        "notes.txt": "ignored",
    }
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".txt"):
            path.write_text("x")
        else:
            shutil.copy2(tone, path)
            # make each file unique so they are not linked as identical copies
            with path.open("ab") as fh:
                fh.write(name.encode())
    tone.unlink()
    return root


def test_pages_render(client):
    for url in ("/", "/jobs", "/reviews", "/import", "/health"):
        assert client.get(url).status_code == 200


def test_import_scan_and_list(client, archive, settings):
    response = client.post("/import", data={"root": 0, "path": ""}, follow_redirects=False)
    assert response.status_code == 303
    run_queue()
    with session_scope() as s:
        files = s.scalars(select(m.MediaFile)).all()
        assert len(files) == 2  # the .txt file is ignored
        assert {f.status for f in files} == {"Not processed"}
    page = client.get("/", params={"q": "R09", "status": "__all"}).text
    assert "R09_0004.MP3" in page and "S.B. 1.19.30" not in page


def test_import_refuses_paths_outside_root(client):
    assert client.get("/import", params={"root": 0, "path": "../.."}).status_code == 400


def ids(s):
    return {f.original_path.rsplit("/", 1)[-1]: f.id for f in s.scalars(select(m.MediaFile))}


def test_renamer_dry_run_changes_nothing(client, archive, reference, no_baserow, settings):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        file_ids = list(ids(s).values())
    client.post("/actions", data={"tool": "renamer", "ids": file_ids, "dry_run": "on"})
    run_queue()
    assert not any(settings.media_processed_files_path.rglob("*.mp3"))
    assert no_baserow == []
    with session_scope() as s:
        for f in s.scalars(select(m.MediaFile)):
            assert f.status == "Not processed" and f.processed_path is None
            assert f.meta["proposal"]["dry_run"] is True
    assert "2011-08-28_KKS_SB-1-19-30_Oslo-no.mp3" in client.get("/?status=__all").text


def test_renamer_run_copies_flags_and_reviews(client, archive, reference, no_baserow, settings):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        by_name = ids(s)
    client.post("/actions", data={"tool": "__pipeline", "scope": "all"})
    run_queue()
    processed = settings.media_processed_files_path
    clean = processed / "Srimad-bhagavatam" / "2011-08-28_KKS_SB-1-19-30_Oslo-no.mp3"
    flagged = processed / "_unresolved" / "2012-07-DD_KKS_rs.mp3"
    assert clean.exists() and flagged.exists()
    assert (
        archive / "From JVD (8.9.11)/KKS_S.B. 1.19.30_28.8.11_Oslo_ .mp3"
    ).exists()  # original kept
    assert len(no_baserow) == 1  # only the clean file goes to Baserow; flagged waits for review

    with session_scope() as s:
        clean_rec = s.get(m.MediaFile, by_name["KKS_S.B. 1.19.30_28.8.11_Oslo_ .mp3"])
        flag_rec = s.get(m.MediaFile, by_name["R09_0004.MP3"])
        assert clean_rec.status == "Renamer Done" and clean_rec.rec_date == "2011-08-28"
        assert flag_rec.status == "Renamer Review"
        review = s.scalars(select(m.Review).where(m.Review.outcome == "pending")).one()
        evidence = s.scalars(
            select(m.WEvidence).where(m.WEvidence.media_file_id == clean_rec.id)
        ).all()
        assert {e.field for e in evidence} >= {"when", "what", "where"}

    page = client.get(f"/review/{review.id}").text
    assert "Non-descriptive filename" in page
    preview = client.post(
        f"/review/{review.id}/preview",
        data={"when": "2012-07-12", "what": "Kirtan", "place": "", "cc": "rs"},
    )
    assert (
        "2012-07-12_KKS_Kirtan_rs.mp3" in preview.text
        and "Follows the naming convention" in preview.text
    )
    bad = client.post(
        f"/review/{review.id}/preview", data={"when": "12-07-2012", "what": "", "cc": ""}
    )
    assert "date must be YYYY-MM-DD" in bad.text

    response = client.post(
        f"/review/{review.id}/decide",
        data={
            "action": "approve",
            "when": "2012-07-12",
            "what": "Kirtan",
            "place": "",
            "cc": "rs",
            "title": "Kirtan",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    moved = processed / "Kirtan" / "2012-07-12_KKS_Kirtan_rs.mp3"
    assert moved.exists() and not flagged.exists()
    with session_scope() as s:
        rec = s.get(m.MediaFile, by_name["R09_0004.MP3"])
        assert rec.status == "Renamer Done" and rec.current_name == moved.name
        assert s.get(m.Review, review.id).outcome == "edited"
    assert len(no_baserow) == 2  # written once approved


def test_reject_removes_copy_and_exclude_stops(client, archive, reference, no_baserow, settings):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        file_id = ids(s)["R09_0004.MP3"]
    client.post("/actions", data={"tool": "renamer", "ids": [file_id]})
    run_queue()
    copy = settings.media_processed_files_path / "_unresolved" / "2012-07-DD_KKS_rs.mp3"
    assert copy.exists()
    with session_scope() as s:
        review_id = s.scalars(select(m.Review.id)).one()
    client.post(f"/review/{review_id}/decide", data={"action": "reject", "note": "wrong"})
    assert not copy.exists()
    with session_scope() as s:
        assert s.get(m.MediaFile, file_id).status == "Not processed"

    client.post("/actions", data={"tool": "renamer", "ids": [file_id]})
    run_queue()
    with session_scope() as s:
        review_id = s.scalars(select(m.Review.id).where(m.Review.outcome == "pending")).one()
    client.post(f"/review/{review_id}/decide", data={"action": "exclude", "note": "not KKS"})
    with session_scope() as s:
        assert s.get(m.MediaFile, file_id).status == "Excluded"
        assert enqueue(s, "renamer", [file_id], dry_run=False) == 0  # excluded files are skipped


def test_failed_job_marks_file_and_can_retry(client, archive, monkeypatch):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()

    def boom(ctx):
        raise RuntimeError("Baserow is down")

    from mam.manager import tools

    monkeypatch.setitem(
        tools.TOOLS, "renamer", tools.ToolSpec("renamer", "Renamer", "io", boom, "test")
    )
    with session_scope() as s:
        file_id = next(iter(ids(s).values()))
    client.post("/actions", data={"tool": "renamer", "ids": [file_id]})
    run_queue()
    with session_scope() as s:
        assert s.get(m.MediaFile, file_id).status == "Renamer Failed"
        job = s.scalars(select(m.Job).where(m.Job.tool == "renamer")).one()
        assert job.state == "failed" and "Baserow is down" in job.error
    assert "Baserow is down" in client.get("/jobs").text
    client.post(f"/jobs/{job.id}/retry")
    with session_scope() as s:
        assert s.scalars(select(m.Job).where(m.Job.state == "queued")).one()


def test_queue_survives_restart(settings):
    with session_scope() as s:
        s.add(m.Job(tool="scan", pool="io", state="running", params={"path": "/nowhere"}))
    queue = JobQueue()
    queue.settings.workers_io = 1
    queue.start()
    queue.stop()
    with session_scope() as s:
        job = s.scalars(select(m.Job)).one()
        assert job.state in ("queued", "running", "done", "failed")
        assert job.state != "running" or job.started_at is not None


@pytest.mark.parametrize("dry_run", [True, False])
def test_recordings_never_share_a_proposed_name(
    client, settings, tone, reference, no_baserow, dry_run
):
    """STE-000/001/002 all resolve to 2008-MM-DD_KKS.mp3; each must get its own name."""
    folder = settings.media_sample_files_path / "2008"
    folder.mkdir()
    for i in range(3):
        path = folder / f"STE-00{i}.mp3"
        shutil.copy2(tone, path)
        with path.open("ab") as fh:
            fh.write(bytes([i]))  # different content: three different recordings
    tone.unlink()
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    data = {"tool": "renamer", "scope": "all"}
    if dry_run:
        data["dry_run"] = "on"
    client.post("/actions", data=data)

    # Run the jobs on several threads at once, like the real worker pool.
    queue = JobQueue()
    queue.settings.workers_io = 4
    queue.start()
    import time

    for _ in range(100):
        with session_scope() as s:
            if not s.scalar(select(m.Job.id).where(m.Job.state.in_(("queued", "running")))):
                break
        time.sleep(0.1)
    queue.stop()

    with session_scope() as s:
        names = sorted(f.meta["proposal"]["final_name"] for f in s.scalars(select(m.MediaFile)))
        flags = [f.meta["proposal"]["flags"] for f in s.scalars(select(m.MediaFile))]
    assert names == ["2008-MM-DD_KKS-2.mp3", "2008-MM-DD_KKS-3.mp3", "2008-MM-DD_KKS.mp3"]
    assert sum(any("Same name as another recording" in f for f in fl) for fl in flags) == 2
    copies = sorted(p.name for p in settings.media_processed_files_path.rglob("*.mp3"))
    assert copies == ([] if dry_run else names)

    # Running again keeps each file's own name (no shifting to -4, -5, ...).
    client.post("/actions", data=data)
    run_queue()
    with session_scope() as s:
        again = sorted(f.meta["proposal"]["final_name"] for f in s.scalars(select(m.MediaFile)))
    assert again == names


def test_file_list_opens_on_files_needing_attention(
    client, archive, reference, no_baserow, settings
):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    assert "Nothing needs your attention" in client.get("/").text
    client.post("/actions", data={"tool": "renamer", "scope": "all"})
    run_queue()
    page = client.get("/").text
    assert "R09_0004.MP3" in page  # flagged for review
    assert "S.B. 1.19.30" not in page  # renamed cleanly; nothing to do
    assert "Needs attention (1)" in page and "All files (2)" in page
    # grouped by folder, relative to the import root
    assert "Sample files / Serbia-summercamp-6-13-jul-2012" in page
    everything = client.get("/?status=__all").text
    assert "S.B. 1.19.30" in everything and "What do the statuses mean?" in everything


def test_evidence_shows_where_it_came_from(client, archive, reference, no_baserow):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    client.post("/actions", data={"tool": "renamer", "scope": "all"})
    run_queue()
    with session_scope() as s:
        file_id = ids(s)["R09_0004.MP3"]
    page = client.get(f"/files/{file_id}").text
    assert "Folder name" in page
    assert "&#34;6-13-jul-2012&#34; in folder &#34;Serbia-summercamp-6-13-jul-2012&#34;" in page
