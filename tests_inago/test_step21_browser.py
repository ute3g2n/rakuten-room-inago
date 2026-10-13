"""Q1.4 public DOM transport tests; fixture browser success is not live proof."""

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import subprocess
from unittest.mock import Mock

import pytest

from automation.inago.live_sources import PublicBrowserSource, PublicSourceConfig
from automation.inago.room_adapter import RoomSearchRequest, parse_room_response
from automation.inago.trends_adapter import TrendsRequest, parse_grouped_top_response


ROOT = Path(__file__).resolve().parents[1]
CHROME = Path(
    os.environ.get(
        "INAGO_TEST_CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    )
)


def room_request():
    return RoomSearchRequest(
        "S1",
        "R1",
        "調理器具",
        ("T1",),
        "theme_fallback",
        1,
        None,
        "2099-10-13T17:45:00+09:00",
        query_kind="theme_fallback",
    )


def room_html(
    count=80,
    *,
    initial=30,
    terminal=False,
    query="調理器具",
    likes="300件",
    comments="コメント数指定なし",
):
    # Every ten source rows refer to the same item. All still occupy source slots.
    rows = "".join(
        f'<div class="items-search"><div class="item"><div class="detail"><div class="imgLink" onclick="openPreview({n})"><img alt="合成商品{n}"></div></div></div></div>'
        for n in range(1, min(initial, count) + 1)
    )
    return f"""<input id="search-box" value="{query}"><div class="search-detail-wrap" onclick="document.querySelector('.itemSearch-popup').style.display='block'">詳細検索</div>
    <div class="itemSearch-popup" style="display:none"><a class="close" onclick="this.parentElement.style.display='none'">閉じる</a>
    <div class="search-popup-project"><h4>いいね数</h4><div class="select"><div class="select-item">{likes}</div></div><span>以上</span></div>
    <div class="search-popup-project"><h4>コメント数</h4><div class="select"><div class="select-item">{comments}</div></div></div></div>
    <div id="rows">{rows}</div>
    <div id="end" style="display:none">検索結果は以上です</div>
    <div id="modal" style="display:none"><div class="buy-button" onclick="window.open('https://item.rakuten.co.jp/shop/item-'+Math.ceil(current/10)+'/')">楽天市場で詳細を見る</div><div class="close-button" onclick="document.querySelector('#modal').style.display='none'">閉じる</div></div>
    <script>let current=1; function openPreview(n){{current=n;document.querySelector('#modal').style.display='block'}}
    function more(){{const rows=document.querySelector('#rows');if(rows.children.length<{count}){{for(let n=rows.children.length+1;n<={count};n++){{const e=document.createElement('div');e.className='items-search';e.innerHTML='<div class="item"><div class="detail"><div class="imgLink" onclick="openPreview('+n+')"><img alt="合成商品'+n+'"></div></div></div>';rows.appendChild(e)}}}} {'document.querySelector("#end").style.display="block";' if terminal else ''}}}
    window.addEventListener('scroll',more);document.body.style.height='3000px';
    {'more();' if count == 0 else ''}</script>"""


def trends_html(*, query="電子レンジ", count=25, delay=0, rising_empty=False):
    return f"""<input aria-label="検索キーワードを追加" value="{query}"><span>検索キーワード</span>
    <button>日本</button><md-select aria-label="Select time period">過去 90 日間</md-select><md-select aria-label="Select stories location">ウェブ検索</md-select>
    <div class="fe-related-queries"><h2>関連トピック</h2><a class="progress-label">誤ったトピック</a></div>
    <div class="fe-related-queries"><h2>関連キーワード</h2><md-select class="bullets-view-selector" onclick="document.querySelector('#choice').style.display='block'">注目</md-select>
    <div id="choice" style="display:none"><span role="option" onclick="document.querySelector('md-select.bullets-view-selector').textContent='人気';this.parentElement.style.display='none';setTimeout(()=>{{topMode=true;render()}},{delay})">人気</span></div><div id="words"></div>
    <button aria-label="Next" onclick="page++;render()">次へ</button></div><script>let page=0,topMode=false;
    function render(){{let s='';for(let rank=page*5+1;rank<=Math.min({count},page*5+5);rank++)s+='<a class="progress-label transition-width"><div class="label-line-number">'+rank+'</div><div class="label-text"><span>'+ (topMode?'関連  語':'注目 語') +rank+'</span></div><div class="progress-value">'+(101-rank)+'</div></a>';if({str(rising_empty).lower()} && !topMode || {count}===0)s='<span>データがありません</span>';document.querySelector('#words').innerHTML=s;document.querySelector('button[aria-label="Next"]').disabled=(page+1)*5>={count}}}render()</script>"""


@pytest.fixture
def ui_server():
    state = {"html": "", "status": 200, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            if self.path.startswith("/hang-script.js"):
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                time.sleep(4)
                return
            status = (
                int(self.path.split("-")[-1])
                if self.path.startswith("/product-")
                else state["status"]
            )
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"Forbidden"
                if self.path.startswith("/product-")
                else state["html"].encode()
            )

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/"
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def source(tmp_path, ui_server, timeout=25):
    if not CHROME.is_file():
        pytest.fail("explicit test Chrome executable unavailable")
    return PublicBrowserSource(
        PublicSourceConfig(
            chrome_executable=CHROME,
            evidence_root=tmp_path / "observations",
            environment="fixture",
            timeout_seconds=timeout,
            fixture_url=ui_server["url"],
        )
    )


def test_room_30_to_80_uses_first_50_before_product_dedup(tmp_path, ui_server):
    ui_server["html"] = room_html()
    port = source(tmp_path, ui_server)
    payload = port.search(room_request())
    result = parse_room_response(payload, room_request())
    assert result.status == "observed", payload.get("error_code")
    assert (
        len(result.posts) == 50
        and result.loaded_posts == 80
        and result.unused_posts == 30
    )
    assert result.end_reason == "limit_reached"
    assert [post.order_index for post in result.posts] == list(range(1, 51))
    assert {post.offer_key for post in result.posts} == {
        f"shop:item-{n}" for n in range(1, 6)
    }
    assert all(
        post.likes is None and post.stable_room_id is None for post in result.posts
    )
    assert len(payload["posts"]) == 50 and port.cancel_owned_jobs("R1")
    record = json.loads(
        next((tmp_path / "observations").glob("*.json")).read_text(encoding="utf-8")
    )
    assert record["response"]["posts"] == payload["posts"]


@pytest.mark.parametrize("count", [0, 37])
def test_explicit_room_end_preserves_normal_actual_count(tmp_path, ui_server, count):
    ui_server["html"] = room_html(count, terminal=True)
    payload = source(tmp_path, ui_server).search(room_request())
    result = parse_room_response(payload, room_request())
    assert result.status == "observed", payload.get("error_code")
    assert (
        len(result.posts) == count
        and result.end_reached
        and result.end_reason == "exhausted"
    )


def test_incomplete_room_is_failure_not_zero(tmp_path, ui_server):
    ui_server["html"] = room_html(3, initial=3)
    payload = source(tmp_path, ui_server, timeout=3).search(room_request())
    result = parse_room_response(payload, room_request())
    assert result.status == "unavailable" and result.error_code
    assert len(result.posts) == 0


@pytest.mark.parametrize("kind", ["room", "trends"])
@pytest.mark.parametrize(
    "status,html",
    [(429, "Too Many Requests"), (403, "Forbidden"), (200, "<h1>CAPTCHA</h1>")],
)
def test_denial_stops_source_without_second_route(
    tmp_path, ui_server, kind, status, html
):
    ui_server.update(status=status, html=html)
    port = source(tmp_path, ui_server)
    request = (
        room_request()
        if kind == "room"
        else TrendsRequest("K1", "R1", "V1", "電子レンジ", range="3m")
    )
    payload = port.search(request) if kind == "room" else port.fetch(request)
    assert payload["status"] == "unavailable"
    before = len(ui_server["requests"])
    again = port.search(request) if kind == "room" else port.fetch(request)
    assert again["error_code"] == "source_access_stopped"
    assert len(ui_server["requests"]) == before


def test_trends_popularity_only_original_top20_preserves_raw(tmp_path, ui_server):
    ui_server["html"] = trends_html()
    request = TrendsRequest("K1", "R1", "V1", "電子レンジ", range="3m")
    payload = source(tmp_path, ui_server).fetch(request)
    result = parse_grouped_top_response(payload, request)
    assert result.status == "observed", payload.get("error_code")
    assert [word.source_rank for word in result.words] == list(range(1, 21))
    assert result.words[0].raw == "関連  語1"
    assert result.ui_period_label == "過去 90 日間"
    assert all(word.popularity_index == 101 - word.source_rank for word in result.words)


@pytest.mark.parametrize("count", [0, 3])
def test_trends_explicit_zero_and_fewer_rows_do_not_fill_from_other_source(
    tmp_path, ui_server, count
):
    ui_server["html"] = trends_html(count=count)
    request = TrendsRequest("K1", "R1", "V1", "電子レンジ", range="3m")
    result = parse_grouped_top_response(
        source(tmp_path, ui_server).fetch(request), request
    )
    assert result.status == "observed" and len(result.words) == count


@pytest.mark.parametrize("kind", ["room", "trends"])
def test_wrong_visible_query_never_returns_old_results(tmp_path, ui_server, kind):
    ui_server["html"] = (
        room_html(query="旧検索") if kind == "room" else trends_html(query="旧検索")
    )
    port = source(tmp_path, ui_server)
    payload = (
        port.search(room_request())
        if kind == "room"
        else port.fetch(TrendsRequest("K1", "R1", "V1", "電子レンジ", range="3m"))
    )
    assert (
        payload["status"] == "unavailable"
        and payload["error_code"] == "query_not_reflected"
    )


def test_cancel_terminates_owned_process_and_returns_no_candidate(tmp_path, ui_server):
    ui_server["html"] = "<h1>loading</h1>"
    port = source(tmp_path, ui_server, timeout=30)
    responses = []
    thread = threading.Thread(
        target=lambda: responses.append(port.search(room_request()))
    )
    thread.start()
    deadline = time.monotonic() + 10
    while port.active_process_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert port.active_process_count == 1
    assert port.cancel_owned_jobs("R1")
    thread.join(10)
    assert not thread.is_alive() and port.active_process_count == 0
    assert responses[0]["status"] == "unavailable"


def test_fixture_rejects_remote_url_and_live_rejects_fixture_endpoint(tmp_path):
    with pytest.raises(ValueError):
        PublicBrowserSource(
            PublicSourceConfig(
                CHROME,
                tmp_path,
                environment="fixture",
                fixture_url="https://example.com/",
            )
        )
    with pytest.raises(ValueError):
        PublicBrowserSource(
            PublicSourceConfig(
                CHROME,
                tmp_path,
                environment="public_ui",
                fixture_url="http://127.0.0.1/",
            )
        )


def test_restart_loads_already_captured_product_fact_without_browser(
    tmp_path, ui_server
):
    port = source(tmp_path, ui_server)
    fact = {
        "offer_key": "shop:item-1",
        "name": "合成商品",
        "item_url": "https://item.rakuten.co.jp/shop/item-1/",
        "evidence_ref": "S1:row:1",
    }
    path = tmp_path / "observations" / "room-S1-observation-01.json"
    path.write_text(
        json.dumps(
            {
                "kind": "room",
                "request": {"attempt_id": "S1", "run_id": "R1"},
                "product_facts": {"S1:row:1": fact},
            }
        ),
        encoding="utf-8",
    )
    assert port.product_fact("S1:row:1") == fact
    assert port.active_process_count == 0 and ui_server["requests"] == []


@pytest.mark.parametrize(
    "likes,comments",
    [
        ("いいね数指定なし", "コメント数指定なし"),
        ("299件", "コメント数指定なし"),
        ("300件", "5件"),
    ],
)
def test_room_unapplied_visible_conditions_are_rejected(
    tmp_path, ui_server, likes, comments
):
    ui_server["html"] = room_html(likes=likes, comments=comments)
    payload = source(tmp_path, ui_server).search(room_request())
    assert (
        payload["status"] == "unavailable"
        and payload["error_code"] == "search_conditions_unverified"
    )


def test_trends_delayed_top_switch_does_not_accept_rising_rows(tmp_path, ui_server):
    ui_server["html"] = trends_html(delay=700)
    request = TrendsRequest("K1", "R1", "V1", "電子レンジ", range="3m")
    result = parse_grouped_top_response(
        source(tmp_path, ui_server).fetch(request), request
    )
    assert result.status == "observed" and len(result.words) == 20
    assert all(word.raw.startswith("関連  語") for word in result.words)


@pytest.mark.parametrize("status", [403, 429])
def test_popup_initial_denial_stops_whole_source(tmp_path, ui_server, status):
    ui_server["html"] = room_html().replace(
        "'https://item.rakuten.co.jp/shop/item-'+Math.ceil(current/10)+'/'",
        f"'/product-{status}'",
    )
    payload = source(tmp_path, ui_server, timeout=10).search(room_request())
    assert (
        payload["status"] == "unavailable" and payload["error_code"] == f"http_{status}"
    )
    assert sum(path.startswith("/product-") for path in ui_server["requests"]) == 1


def test_saved_denial_survives_source_recreation_for_same_run(tmp_path, ui_server):
    ui_server.update(status=429, html="Too Many Requests")
    first = source(tmp_path, ui_server).search(room_request())
    assert first["error_code"] == "http_429"
    before = len(ui_server["requests"])
    again = source(tmp_path, ui_server).search(room_request())
    assert again["error_code"] == "source_access_stopped"
    assert len(ui_server["requests"]) == before


def test_cleanup_failure_is_recorded_and_not_reported_as_success(
    tmp_path, ui_server, monkeypatch
):
    port = source(tmp_path, ui_server, timeout=1)
    process = Mock()
    process.communicate.side_effect = subprocess.TimeoutExpired("owned", 1)
    process.poll.return_value = None
    process.pid = 123456
    process.returncode = None
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(port, "_terminate_tree", lambda process: False)
    result = port.search(room_request())
    assert result["error_code"] == "owned_cleanup_unconfirmed"
    assert port.active_process_count == 1 and not port.cancel_owned_jobs("R1")
    assert list((tmp_path / "observations").glob("room-*.json"))


@pytest.mark.parametrize(
    "failure", [OSError("synthetic"), subprocess.TimeoutExpired("owned", 5), 1]
)
def test_termination_command_failure_cannot_confirm_tree_exit(monkeypatch, failure):
    process = Mock()
    process.pid = 123456
    process.poll.return_value = None
    if isinstance(failure, int):
        monkeypatch.setattr(
            subprocess, "run", Mock(return_value=Mock(returncode=failure))
        )
    else:
        monkeypatch.setattr(subprocess, "run", Mock(side_effect=failure))
    assert PublicBrowserSource._terminate_tree(process) is False
    process.kill.assert_not_called()


def test_metadata_trace_excludes_raw_logs_and_unexpected_fields():
    raw = "\n".join(
        [
            "not JSON or credentials",
            json.dumps(
                {
                    "event": "public_source_stage",
                    "stage": "room_resolve_products",
                    "loaded_posts": 80,
                    "accepted_posts": 50,
                    "token": "SECRET-FAKE",
                }
            ),
            json.dumps({"event": "other", "stage": "unexpected"}),
        ]
    )
    events = PublicBrowserSource._metadata_events(raw)
    assert events == [
        {
            "event": "public_source_stage",
            "stage": "room_resolve_products",
            "loaded_posts": 80,
            "accepted_posts": 50,
        }
    ]
    assert "SECRET-FAKE" not in json.dumps(events)


def test_windows_colon_id_is_saved_in_ordinary_file_not_ads(tmp_path, ui_server):
    ui_server["html"] = trends_html(count=3)
    request = TrendsRequest("K:1", "R1", "V1", "電子レンジ", range="3m")
    result = parse_grouped_top_response(
        source(tmp_path, ui_server).fetch(request), request
    )
    assert result.status == "observed" and len(result.words) == 3
    assert list((tmp_path / "observations").glob("trends-K%3A1-observation-*.json"))


def test_cleanup_stop_is_restored_without_new_browser(tmp_path, ui_server):
    root = tmp_path / "observations"
    root.mkdir()
    (root / "access-stop-R1.json").write_text(
        json.dumps(
            {"run_id": "R1", "kind": "room", "error_code": "owned_cleanup_unconfirmed"}
        ),
        encoding="utf-8",
    )
    payload = source(tmp_path, ui_server).search(room_request())
    assert (
        payload["error_code"] == "source_access_stopped" and ui_server["requests"] == []
    )


def test_other_post_text_cannot_claim_search_exhaustion(tmp_path, ui_server):
    ui_server["html"] = room_html(3, initial=3).replace(
        '<img alt="合成商品1">', '<img alt="合成商品1"><p>検索結果は以上です</p>'
    )
    payload = source(tmp_path, ui_server, timeout=3).search(room_request())
    assert payload["status"] == "unavailable" and payload["error_code"]


def test_denied_observation_before_marker_also_stops_recreated_source(
    tmp_path, ui_server
):
    root = tmp_path / "observations"
    root.mkdir()
    (root / "trends-K1-observation-01.json").write_text(
        json.dumps(
            {
                "kind": "trends",
                "request": {"run_id": "R1", "request_id": "K1"},
                "error_code": "http_429",
            }
        ),
        encoding="utf-8",
    )
    payload = source(tmp_path, ui_server).search(room_request())
    assert (
        payload["error_code"] == "source_access_stopped" and ui_server["requests"] == []
    )


def test_known_denial_takes_priority_over_simultaneous_cancel(
    tmp_path, ui_server, monkeypatch
):
    port = source(tmp_path, ui_server)
    process = Mock()
    process.poll.return_value = 0
    process.returncode = 0

    def communicate(*args, **kwargs):
        port._cancelled_runs.add("R1")
        return json.dumps({"error_code": "http_429"}), ""

    process.communicate.side_effect = communicate
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    payload = port.search(room_request())
    assert payload["error_code"] == "http_429"
    assert (
        source(tmp_path, ui_server).search(room_request())["error_code"]
        == "source_access_stopped"
    )


def test_rising_empty_waits_for_delayed_top_data(tmp_path, ui_server):
    ui_server["html"] = trends_html(count=3, delay=900, rising_empty=True)
    request = TrendsRequest("K1", "R1", "V1", "電子レンジ", range="3m")
    result = parse_grouped_top_response(
        source(tmp_path, ui_server).fetch(request), request
    )
    assert result.status == "observed" and len(result.words) == 3
    assert all(word.raw.startswith("関連  語") for word in result.words)


def test_observed_http_denial_overrides_navigation_timeout(tmp_path, ui_server):
    ui_server.update(
        status=429,
        html='<html><head><script src="/hang-script.js"></script></head><body>loading</body>',
    )
    payload = source(tmp_path, ui_server, timeout=2).search(room_request())
    assert payload["error_code"] == "http_429"


def test_old_empty_hide_then_reshow_never_establishes_top_zero(tmp_path, ui_server):
    ui_server["html"] = trends_html(count=3, delay=4000, rising_empty=True).replace(
        "setTimeout(()=>{topMode=true;render()}",
        "setTimeout(()=>document.querySelector('#words span').style.display='none',100);setTimeout(()=>document.querySelector('#words span').style.display='block',200);setTimeout(()=>{topMode=true;render()}",
    )
    request = TrendsRequest("K1", "R1", "V1", "電子レンジ", range="3m")
    result = parse_grouped_top_response(
        source(tmp_path, ui_server).fetch(request), request
    )
    assert result.status == "observed" and len(result.words) == 3
