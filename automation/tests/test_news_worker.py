from pathlib import Path

from automation.fleet_sync.core import content_hash, dump_json, load_json
from automation.fleet_sync.news_jobs import plan_news_jobs, queue_from_events, reading_context
from automation.fleet_sync.news_review import REVIEW_VERSION
from automation.install_news_worker import LABEL, launch_agent
from automation.news_worker import check_once


def manifest(jobs=None):
    jobs = jobs or []
    return {"newsQueue": {"schemaVersion": 1, "jobs": jobs, "pendingCount": len(jobs), "fingerprint": content_hash(jobs, length=64)}}


def fail_if_called(_):
    raise AssertionError("publisher/Codex must not run")


def test_no_server_work_means_no_git_clone_or_model_invocation(tmp_path):
    result = check_once(tmp_path, fetch=manifest, publisher=fail_if_called, now=100)
    assert result == {"ok": True, "status": "idle", "modelInvoked": False}


def test_one_server_event_is_consumed_once_even_while_old_deployment_is_cached(tmp_path):
    payload = manifest([{"url": "https://www.deepwater.com/news/detail?ID=1"}])
    calls = []
    publish = lambda root: calls.append(root) or {"ok": True, "changed": True}
    assert check_once(tmp_path, fetch=lambda: payload, publisher=publish, now=100)["status"] == "processed"
    assert check_once(tmp_path, fetch=lambda: payload, publisher=fail_if_called, now=200)["status"] == "already-handled"
    assert calls == [tmp_path]
    payload = manifest([{"url": "https://www.deepwater.com/news/detail?ID=2"}])
    assert check_once(tmp_path, fetch=lambda: payload, publisher=publish, now=300)["status"] == "processed"
    assert calls == [tmp_path, tmp_path]


def test_failure_retries_with_backoff_without_acknowledging_work(tmp_path):
    payload = manifest([{"url": "news"}])
    def failed(_):
        raise RuntimeError("ChatGPT login expired")
    assert not check_once(tmp_path, fetch=lambda: payload, publisher=failed, now=100)["ok"]
    assert check_once(tmp_path, fetch=lambda: payload, publisher=fail_if_called, now=200)["status"] == "retry-backoff"
    assert check_once(tmp_path, fetch=lambda: payload, publisher=lambda _: {"ok": True}, now=400)["status"] == "processed"
    assert load_json(tmp_path / ".cache/news-worker-state.json")["lastError"] is None


def test_busy_publisher_does_not_lose_server_event(tmp_path):
    fetch = lambda: manifest([{"url": "news"}])
    assert check_once(tmp_path, fetch=fetch, publisher=lambda _: {"ok": True, "skipped": "locked"})["status"] == "busy"
    assert check_once(tmp_path, fetch=fetch, publisher=lambda _: {"ok": True})["status"] == "processed"


def test_invalid_checkpoint_recovers_and_is_replaced_atomically(tmp_path):
    dump_json(tmp_path / ".cache/news-worker-state.json", [])
    assert check_once(tmp_path, fetch=manifest, publisher=fail_if_called)["status"] == "idle"
    assert load_json(tmp_path / ".cache/news-worker-state.json")["status"] == "idle"
    assert not (tmp_path / ".cache/news-worker-state.tmp").exists()


def test_corrupt_or_missing_queue_fails_closed_without_starting_codex(tmp_path):
    for payload in ({}, {"newsQueue": {"schemaVersion": 1, "jobs": [], "pendingCount": 1, "fingerprint": "a"*64}}):
        assert not check_once(tmp_path, fetch=lambda: payload, publisher=fail_if_called)["ok"]


def test_launch_agent_uses_cli_not_the_desktop_app_or_a_model_to_poll():
    agent = launch_agent(Path("/project"), "/project/.venv/bin/python", ["/bin", "/bin"])
    assert agent["Label"] == LABEL
    assert agent["StartInterval"] == 300 and agent["RunAtLoad"]
    assert agent["ProgramArguments"] == ["/project/.venv/bin/python", "-u", "-m", "automation.news_worker", "--root", "/project"]
    assert agent["EnvironmentVariables"] == {"PATH": "/bin"}


def test_server_queue_reuses_readers_exact_context_and_deduplicates_vessels(tmp_path):
    fleet = [{"company": "Noble", "name": "Rig A", "contracts": []}]
    dates = {"Noble": "2026-08-01"}
    event = {"company": "Noble", "url": "https://noblecorp.com/news", "publishedAt": "2026-10-01",
             "title": "News", "classification": "official-news-signal", "articleSha256": "a"*64}
    _, _, context = reading_context(tmp_path, fleet, [event], dates, [], event)
    review = {"version": REVIEW_VERSION, "company": "Noble", "url": event["url"], "title": "News",
              "publishedAt": "2026-10-01", "sourceSha256": "a"*64, "contextSha256": context, "result": {"vessels": []}, "reader": {}}
    assert plan_news_jobs(tmp_path, fleet, [event, event], dates, [review]) == []
    changed = {**event, "articleSha256": "b"*64}
    jobs = plan_news_jobs(tmp_path, fleet, [changed, changed], dates, [review])
    assert len(jobs) == 1
    queued = [{**changed, "readingJob": jobs[0]}, {**changed, "readingJob": jobs[0]}]
    assert queue_from_events(queued)["pendingCount"] == 1
    assert plan_news_jobs(tmp_path, fleet, [{**changed, "articleReadError": "403"}], dates, [review]) == []
    assert len(plan_news_jobs(tmp_path, [{**fleet[0], "yearBuilt": 2020}], [event], dates, [review])) == 1
    assert plan_news_jobs(tmp_path, fleet, [event], {"Noble": "2026-10-02"}, [review]) == []
