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


def approve_form(filename, **extra):
    """The review/edit form as a browser would send it for `filename`."""
    from mam.tools.renamer.editing import parse_filename
    from mam.tools.renamer.knowledge import get_reference

    values = parse_filename(filename, get_reference())
    return {
        "action": "approve",
        "filename": filename,
        "when": values["when"],
        "category": values["category"],
        "tag": values["tag"],
        "place": values["place"],
        "cc": values["cc"],
        "title": values["title"] or filename,
        "languages": ["English"],
        **extra,
    }


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
    flag_id = by_name["R09_0004.MP3"]
    # editing the filename fills the table fields (values only; the form is not replaced)
    synced = client.post(
        f"/files/{flag_id}/edit/sync",
        data={
            "filename": "2012-07-12_KKS_Kirtan_rs.mp3",
            "changed": "filename",
            "notes": "keep me",
        },
    ).json()
    assert synced["values"]["when"] == "2012-07-12"
    assert synced["values"]["category"] == "Kirtan" and synced["values"]["cc"] == "rs"
    assert synced["values"]["notes"] == "keep me"  # a typed note is never lost
    # changing the date rewrites the filename
    synced = client.post(
        f"/files/{flag_id}/edit/sync",
        data={
            "filename": "2012-07-12_KKS_Kirtan_rs.mp3",
            "when": "2012-07-13",
            "cc": "rs",
            "changed": "when",
        },
    ).json()
    assert synced["values"]["filename"] == "2012-07-13_KKS_Kirtan_rs.mp3"
    bad = " ".join(
        client.post(
            f"/files/{flag_id}/edit/sync",
            data={"filename": "12-07-2012_KKS.mp3", "when": "12-07-2012", "changed": "when"},
        ).json()["problems"]
    )
    assert "date must be YYYY-MM-DD" in bad

    response = client.post(
        f"/review/{review.id}/decide",
        data={**approve_form("2012-07-12_KKS_Kirtan_rs.mp3"), "title": "Kirtan"},
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
    assert no_baserow[0]["live"] is False and no_baserow[1]["live"] is True  # approval is live


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


def test_dry_run_explains_that_no_review_is_opened(client, archive, reference, no_baserow):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        file_id = ids(s)["R09_0004.MP3"]
    client.post("/actions", data={"tool": "renamer", "ids": [file_id], "dry_run": "on"})
    run_queue()
    assert "would need a review" in client.get("/jobs").text
    page = client.get(f"/files/{file_id}").text
    assert "no review was opened" in page and "Run the Renamer" in page
    with session_scope() as s:
        assert not s.scalars(select(m.Review)).all()


def test_rerun_keeps_confirmed_values(client, archive, reference, no_baserow, settings):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        file_id = ids(s)["R09_0004.MP3"]
    client.post("/actions", data={"tool": "renamer", "ids": [file_id]})
    run_queue()
    with session_scope() as s:
        review_id = s.scalars(select(m.Review.id)).one()
    client.post(
        f"/review/{review_id}/decide",
        data=approve_form("2012-07-11_KKS_Kirtan_rs.mp3"),
    )
    approved = settings.media_processed_files_path / "Kirtan" / "2012-07-11_KKS_Kirtan_rs.mp3"
    assert approved.exists()

    for dry in (True, False):  # neither a dry run nor a real run changes it
        data = {"tool": "renamer", "ids": [file_id]} | ({"dry_run": "on"} if dry else {})
        client.post("/actions", data=data)
        run_queue()
    assert approved.exists()
    with session_scope() as s:
        rec = s.get(m.MediaFile, file_id)
        assert rec.current_name == approved.name and rec.status == "Renamer Done"
        assert rec.meta["proposal"]["final_name"] == approved.name
        assert (
            "Kept 2012-07-11_KKS_Kirtan_rs.mp3 (confirmed by tester)"
            in s.scalars(
                select(m.Job.result).where(m.Job.media_file_id == file_id).order_by(m.Job.id.desc())
            ).first()["summary"]
        )


def test_file_page_updates_itself_and_shows_the_last_result(client, archive, reference, no_baserow):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        file_id = ids(s)["R09_0004.MP3"]
    # "Run" button on the file page
    client.post("/actions", data={"tool": "renamer", "ids": [file_id], "mode": "run"})
    page = client.get(f"/files/{file_id}").text
    assert f'hx-get="/files/{file_id}/activity"' in page and "waiting in the queue" in page
    assert "waiting in the queue" in client.get(f"/files/{file_id}/activity").text
    run_queue()
    done = client.get(f"/files/{file_id}/activity")
    assert done.headers.get("HX-Redirect") == f"/files/{file_id}"  # the page reloads by itself
    page = client.get(f"/files/{file_id}").text
    assert "Last run:" in page and "Open the review" in page


def test_preview_button_is_a_dry_run(client, archive, reference, no_baserow, settings):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    client.post("/actions", data={"tool": "renamer", "scope": "all", "mode": "preview"})
    run_queue()
    assert not any(settings.media_processed_files_path.rglob("*.mp3"))
    with session_scope() as s:
        assert all(j.dry_run for j in s.scalars(select(m.Job).where(m.Job.tool == "renamer")))


def test_approve_writes_baserow_live_even_in_dry_run_mode(
    client, archive, reference, settings, monkeypatch
):
    import httpx
    import respx

    from mam.helpers.baserow.client import BaserowClient, Field

    class AnyColumn(dict):  # every column exists and is plain text
        def get(self, name, default=None):
            return Field(name, "text", {})

    monkeypatch.setattr(BaserowClient, "fields", lambda self, table_id, refresh=False: AnyColumn())
    from mam.helpers.local_db.app_settings import baserow_writes_enabled

    assert baserow_writes_enabled() is False
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        file_id = ids(s)["R09_0004.MP3"]
    client.post("/actions", data={"tool": "renamer", "ids": [file_id], "mode": "run"})
    run_queue()
    with session_scope() as s:
        review_id = s.scalars(select(m.Review.id)).one()
        # the run itself did not write (the Baserow writes switch is off)
        assert not s.scalars(select(m.BaserowWriteLog)).all()
    assert (
        "Approving renames the copy in processed-files and writes the media row live"
        in client.get(f"/review/{review_id}").text
    )
    with respx.mock(base_url="https://baserow.test/api/database") as api:
        create = api.post("/rows/table/1/").mock(return_value=httpx.Response(200, json={"id": 999}))
        response = client.post(
            f"/review/{review_id}/decide",
            data=approve_form("2012-07-11_KKS_Kirtan_rs.mp3"),
            follow_redirects=False,
        )
    from urllib.parse import unquote

    assert create.called
    assert "written to Baserow (row 999)" in unquote(response.headers["location"])
    with session_scope() as s:
        log = s.scalars(select(m.BaserowWriteLog)).one()
        assert not log.dry_run and log.row_id == 999 and log.payload["date"] == "2012-07-11"
        assert s.get(m.MediaFile, file_id).baserow_row_id == 999


def test_run_result_says_what_happened_in_baserow(client, archive, reference, no_baserow):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    client.post("/actions", data={"tool": "renamer", "scope": "all", "mode": "run"})
    run_queue()
    with session_scope() as s:
        by_name = ids(s)
        summaries = {
            j.media_file_id: j.result["summary"]
            for j in s.scalars(select(m.Job).where(m.Job.tool == "renamer"))
        }
    clean = summaries[by_name["KKS_S.B. 1.19.30_28.8.11_Oslo_ .mp3"]]
    flagged = summaries[by_name["R09_0004.MP3"]]
    assert clean.endswith("Baserow: new row logged only (Baserow writes are off)")
    assert flagged.endswith("Baserow: written when you approve the review")
    page = client.get("/").text
    assert ">Run</button>" in page and "Run for real" not in page


def test_baserow_switch_on_the_home_page(client, settings):
    from mam.helpers.local_db.app_settings import baserow_writes_enabled

    page = client.get("/").text
    assert 'id="baserow-switch-nav"' in page and "<strong>off</strong>" in page
    assert 'id="baserow-switch-nav"' in client.get("/jobs").text  # in the top bar on every page
    response = client.post(
        "/settings/baserow-writes",
        data={"enabled": "on", "back": "/?status=__all"},
        follow_redirects=False,
    )
    assert response.headers["location"].startswith("/?status=__all&msg=")
    assert baserow_writes_enabled() is True
    assert 'text-danger">ON' in client.get("/").text
    client.post("/settings/baserow-writes", data={"back": "/"})  # unchecked switch -> off
    assert baserow_writes_enabled() is False


def test_run_writes_baserow_live_when_the_switch_is_on(
    client, archive, reference, settings, monkeypatch
):
    import httpx
    import respx

    from mam.helpers.baserow.client import BaserowClient, Field
    from mam.helpers.local_db.app_settings import set_baserow_writes

    class AnyColumn(dict):
        def get(self, name, default=None):
            return Field(name, "text", {})

    monkeypatch.setattr(BaserowClient, "fields", lambda self, table_id, refresh=False: AnyColumn())
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    with session_scope() as s:
        file_id = ids(s)["KKS_S.B. 1.19.30_28.8.11_Oslo_ .mp3"]
    set_baserow_writes(True)
    with respx.mock(base_url="https://baserow.test/api/database") as api:
        create = api.post("/rows/table/1/").mock(return_value=httpx.Response(200, json={"id": 77}))
        client.post("/actions", data={"tool": "renamer", "ids": [file_id], "mode": "run"})
        run_queue()
    assert create.called
    with session_scope() as s:
        job = s.scalars(
            select(m.Job).where(m.Job.media_file_id == file_id, m.Job.tool == "renamer")
        ).one()
        assert job.result["summary"].endswith("Baserow: new row written (row 77)")
        assert s.get(m.MediaFile, file_id).baserow_row_id == 77


def test_filters_have_clear_buttons(client):
    page = client.get("/").text
    for target in ("q", "status", "category", "kind", "sort"):
        assert f'data-target="{target}"' in page  # always visible
    # dimmed (disabled) while there is nothing to clear; status clears to All files
    assert 'clear-filter" disabled data-target="q"' in page
    assert 'clear-filter"  data-target="status" data-default="__all"' in page  # Needs attention
    page = client.get("/", params={"status": "__all", "q": "SB", "folder": "/x/sample/2008"}).text
    assert 'clear-filter" disabled data-target="status"' in page
    assert 'clear-filter"  data-target="q"' in page
    assert "Clear folder" in page


def test_what_is_shown_in_its_category_color(client, archive, reference, no_baserow):
    client.post("/import", data={"root": 0, "path": ""})
    run_queue()
    client.post("/actions", data={"tool": "renamer", "scope": "all", "mode": "run"})
    run_queue()
    page = client.get("/?status=__all").text
    # Srimad-bhagavatam is 'light-red' in category_terms_folder
    assert (
        'style="background-color:#edaba8;color:#1f2328;border-color:#edaba8" '
        'title="Srimad-bhagavatam">SB-1-19-30' in page
    )


def test_filter_reload_keeps_a_real_page_url(client):
    page = client.get("/").text
    assert 'hx-get="/" hx-select="#file-table"' in page  # the address bar stays on /?...
    assert 'id="file-count"' in page and 'id="scope-all"' in page  # counts update too


def test_status_list_and_guide_show_the_same_statuses_in_order(client):
    from mam.manager.app import STATUS_HELP, VIEW_HELP
    from mam.manager.queries import all_statuses

    statuses = all_statuses()
    assert statuses[:6] == [
        "Not processed",
        "Queued Renamer",
        "Processing Renamer",
        "Renamer Done",
        "Renamer Review",
        "Renamer Failed",
    ]
    assert statuses[-4:] == ["Skipped", "Excluded", "Corrupted", "Processing complete"]
    # the guide explains each status in the list, in the same order
    generic = [
        s if s in STATUS_HELP else next(k for k in STATUS_HELP if k in s.split()) for s in statuses
    ]
    assert list(dict.fromkeys(generic)) == list(STATUS_HELP)
    page = client.get("/?status=__all").text
    assert "Renamer Review (0)" in page and "Processing complete (0)" in page
    for view in VIEW_HELP:
        assert f'<dt class="col-sm-3 col-lg-2 fw-semibold">{view}</dt>' in page
    assert all_statuses({"Old tool Done": 1})[-1] == "Old tool Done"
