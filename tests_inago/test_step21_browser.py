"""Q1.4 public DOM transport tests; fixture browser success is not live proof."""

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
import time
import subprocess
from unittest.mock import Mock
from urllib.parse import urlsplit

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
CHROMIUM_RESTRICTED_PORTS = frozenset(
    {
        0,
        1,
        7,
        9,
        11,
        13,
        15,
        17,
        19,
        20,
        21,
        22,
        23,
        25,
        37,
        42,
        43,
        53,
        69,
        77,
        79,
        87,
        95,
        101,
        102,
        103,
        104,
        109,
        110,
        111,
        113,
        115,
        117,
        119,
        123,
        135,
        137,
        139,
        143,
        161,
        179,
        389,
        427,
        465,
        512,
        513,
        514,
        515,
        526,
        530,
        531,
        532,
        540,
        548,
        554,
        556,
        563,
        587,
        601,
        636,
        989,
        990,
        993,
        995,
        1719,
        1720,
        1723,
        2049,
        3659,
        4045,
        5060,
        5061,
        6000,
        6566,
        6665,
        6666,
        6667,
        6668,
        6669,
        6697,
        10080,
    }
)


def _browser_safe_test_server(handler):
    # Chromium refuses navigations to its restricted ports, including on loopback.
    for _ in range(16):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        if server.server_port not in CHROMIUM_RESTRICTED_PORTS:
            return server
        server.server_close()
    raise RuntimeError("could not allocate a Chromium-safe loopback test port")


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
    direct_href=False,
    card_direct_href=False,
    wrapped_href=False,
    same_tab=False,
    same_tab_delay_ms=0,
    detail_delay_ms=0,
    scroll_marker=False,
):
    # Every ten source rows refer to the same item. All still occupy source slots.
    card_link = (
        '<a href="https://item.rakuten.co.jp/shop/item-1/">商品ページ</a>'
        if card_direct_href
        else ""
    )
    rows = "".join(
        f'<div class="items-search"><div class="item">{card_link}<div class="detail"><div class="imgLink" onclick="openPreview({n})"><img alt="合成商品{n}"></div></div></div></div>'
        for n in range(1, min(initial, count) + 1)
    )
    detail_control = (
        (
            f'<a target="_self" href="https://item.rakuten.co.jp/shop/item-1/" '
            f"onclick=\"event.preventDefault();setTimeout(()=>location.href='https://item.rakuten.co.jp/shop/item-1/',{same_tab_delay_ms})\">"
            "楽天市場で詳細を見る</a>"
            if same_tab_delay_ms
            else '<a target="_self" href="https://item.rakuten.co.jp/shop/item-1/">楽天市場で詳細を見る</a>'
        )
        if same_tab
        else (
            '<a target="_blank" href="https://item.rakuten.co.jp/shop/item-1/">楽天市場で詳細を見る</a>'
            if direct_href
            else (
                '<a target="_blank" href="https://item.rakuten.co.jp/shop/item-1/"><div>楽天市場で詳細を見る</div></a>'
                if wrapped_href
                else "<div class=\"market-detail-link\" onclick=\"window.open('https://item.rakuten.co.jp/shop/item-'+Math.ceil(current/10)+'/')\">楽天市場で詳細を見る</div>"
            )
        )
    )
    open_preview = (
        f"setTimeout(()=>document.querySelector('#modal').style.display='block',{detail_delay_ms})"
        if detail_delay_ms
        else "document.querySelector('#modal').style.display='block'"
    )
    return f"""<input id="search-box" value="{query}"><div class="search-detail-wrap" onclick="const popup=document.querySelector('.itemSearch-popup');popup.style.display=popup.style.display==='block'?'none':'block'">詳細検索</div>
    <div class="itemSearch-popup" style="display:none"><a class="close" onclick="this.parentElement.style.display='none'">閉じる</a>
    <div class="search-popup-project"><h4>いいね数</h4><div class="select"><div class="select-item">{likes}</div></div><span>以上</span></div>
    <div class="search-popup-project"><h4>コメント数</h4><div class="select"><div class="select-item">{comments}</div></div></div></div>
    <div id="rows">{rows}</div>
    <div id="end" style="display:none">検索結果は以上です</div>
    <div id="modal" style="display:none">{detail_control}<div class="close-button" onclick="document.querySelector('#modal').style.display='none'">閉じる</div></div>
    <script>let current=1; function openPreview(n){{current=n;{open_preview}}}
    function more(){{const rows=document.querySelector('#rows');if(rows.children.length<{count}){{for(let n=rows.children.length+1;n<={count};n++){{const e=document.createElement('div');e.className='items-search';e.innerHTML='<div class="item"><div class="detail"><div class="imgLink" onclick="openPreview('+n+')"><img alt="合成商品'+n+'"></div></div></div>';rows.appendChild(e)}}}} {'document.querySelector("#end").style.display="block";' if terminal else ''}}}
    window.addEventListener('scroll',more);document.body.style.height='3000px';
    {'window.addEventListener("scroll",()=>fetch("/room-scroll-observed"));' if scroll_marker else ''}
    {'more();' if count == 0 else ''}</script>"""


def trends_html(
    *,
    query="電子レンジ",
    count=25,
    delay=0,
    rising_empty=False,
    refresh_after_period=False,
    top_selected=False,
):
    selected_list = "人気" if top_selected else "注目"
    selected_mode = str(top_selected).lower()
    refresh_marker = "<!--fixture-period-refresh-->" if refresh_after_period else ""
    return f"""<input aria-label="検索キーワードを追加" value="{query}"><span>検索キーワード</span>
    <button>日本</button><md-select aria-label="Select time period">過去 90 日間</md-select><md-select aria-label="Select stories location">ウェブ検索</md-select>
    <div class="fe-related-queries"><h2>関連トピック</h2><a class="progress-label">誤ったトピック</a></div>
    <div class="fe-related-queries"><h2>関連キーワード</h2><md-select class="bullets-view-selector" onclick="document.querySelector('#choice').style.display='block'">{selected_list}</md-select>
    <div id="choice" style="display:none"><span role="option" onclick="document.querySelector('md-select.bullets-view-selector').textContent='人気';this.parentElement.style.display='none';setTimeout(()=>{{topMode=true;render()}},{delay})">人気</span></div><div id="words"></div>
    <button aria-label="Next" onclick="page++;render()">次へ</button></div><script>let page=0,topMode={selected_mode};
    function render(){{let s='';for(let rank=page*5+1;rank<=Math.min({count},page*5+5);rank++)s+='<a class="progress-label transition-width"><div class="label-line-number">'+rank+'</div><div class="label-text"><span>'+ (topMode?'関連  語':'注目 語') +rank+'</span></div><div class="progress-value">'+(101-rank)+'</div></a>';if({str(rising_empty).lower()} && !topMode || {count}===0)s='<span>データがありません</span>';document.querySelector('#words').innerHTML=s;document.querySelector('button[aria-label="Next"]').disabled=(page+1)*5>={count}}}render(){refresh_marker}</script>"""


def trends_home_html(*, input_delay_ms=0):
    return f"""<form id="search-form"><input aria-label="検索"><button type="submit">検索</button></form>
    <script>if ({input_delay_ms}) document.querySelector('input').addEventListener('input',()=>{{
      const until=Date.now()+{input_delay_ms};while(Date.now()<until){{}}
    }});
    document.querySelector('#search-form').addEventListener('submit',event=>{{
      event.preventDefault();const url=new URL('/trends/explore',location.origin);
      url.searchParams.set('date','today 12-m');url.searchParams.set('geo','JP');
      url.searchParams.set('q',document.querySelector('input').value);url.searchParams.set('hl','ja');
      location.href=url.href;
    }});</script>"""


def trends_period_html(*, query="電子レンジ", count=20, refresh_after_period=False):
    base = trends_html(
        query=query, count=count, refresh_after_period=refresh_after_period
    )
    return trends_with_period_picker(base)


def trends_with_period_picker(base):
    refresh_after_period = "<!--fixture-period-refresh-->" in base
    base = base.replace("<!--fixture-period-refresh-->", "")
    refresh_script = (
        "const refreshedRelatedNode=document.querySelector('.fe-related-queries .label-text span')||"
        "document.querySelector('.fe-related-queries #words span');"
        "if(refreshedRelatedNode)refreshedRelatedNode.replaceWith(refreshedRelatedNode.cloneNode(true));"
        if refresh_after_period
        else ""
    )
    base = base.replace(
        '<md-select aria-label="Select time period">過去 90 日間</md-select>',
        '<button aria-label="Select time period" onclick="openPeriod()">過去 12 か月</button>'
        '<div id="period-menu" style="display:none"><md-menu-item role="option" '
        'onclick="selectPeriod()">過去 90 日間</md-menu-item></div>',
    )
    return base.replace(
        "</script>",
        "</script><script>function openPeriod(){document.querySelector('#period-menu').style.display='block'}"
        "function selectPeriod(){document.querySelector('button[aria-label^=\\\"Select time period\\\"]').textContent='過去 90 日間';"
        "const url=new URL(location.href);url.searchParams.set('date','today 3-m');history.replaceState({},'',url);"
        "document.querySelector('#period-menu').style.display='none';"
        + refresh_script
        + "}</script>",
    )


@pytest.fixture
def ui_server():
    state = {
        "html": "",
        "status": 200,
        "requests": [],
        "pages": {},
        "retry_after": None,
        "pending_request_started": threading.Event(),
        "pending_request_release": threading.Event(),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            if urlsplit(self.path).path == "/hang-pending":
                state["pending_request_started"].set()
                state["pending_request_release"].wait(timeout=10)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"released")
                except OSError:
                    pass
                return
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
            if state.get("retry_after") is not None:
                self.send_header("Retry-After", str(state["retry_after"]))
            self.end_headers()
            if self.path.startswith("/product-"):
                body = "Forbidden"
            else:
                body = state["pages"].get(urlsplit(self.path).path, state["html"])
            self.wfile.write(body.encode())

        def log_message(self, *_):
            pass

    server = _browser_safe_test_server(Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}/"
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def test_fixture_server_retries_chromium_restricted_loopback_port(monkeypatch):
    allocated_ports = iter((6667, 49152))
    servers = []

    def fake_server(*_args):
        server = Mock()
        server.server_port = next(allocated_ports)
        servers.append(server)
        return server

    monkeypatch.setitem(globals(), "ThreadingHTTPServer", fake_server)

    selected = _browser_safe_test_server(Mock())

    assert selected is servers[1]
    servers[0].server_close.assert_called_once_with()
    servers[1].server_close.assert_not_called()


def source(
    tmp_path,
    ui_server,
    timeout=25,
    fixture_product_delay_ms=0,
    fixture_browser_startup_delay_ms=0,
    fixture_room_scroll_delay_ms=0,
    fixture_expire_deadline_before_room_restore=False,
):
    if not CHROME.is_file():
        pytest.fail("explicit test Chrome executable unavailable")
    if ui_server["html"] and not ui_server["pages"]:
        ui_server["pages"].update(
            {
                "/trends/": trends_home_html(),
                "/trends/explore": trends_with_period_picker(ui_server["html"]),
            }
        )
    return PublicBrowserSource(
        PublicSourceConfig(
            chrome_executable=CHROME,
            evidence_root=tmp_path / "observations",
            environment="fixture",
            timeout_seconds=timeout,
            fixture_url=ui_server["url"],
            headless=True,
            fixture_product_delay_ms=fixture_product_delay_ms,
            fixture_browser_startup_delay_ms=fixture_browser_startup_delay_ms,
            fixture_room_scroll_delay_ms=fixture_room_scroll_delay_ms,
            fixture_expire_deadline_before_room_restore=(
                fixture_expire_deadline_before_room_restore
            ),
        )
    )


def test_default_source_timeout_covers_measured_50_row_scan(tmp_path):
    config = PublicSourceConfig(chrome_executable=CHROME, evidence_root=tmp_path)

    assert config.timeout_seconds == 17100


def test_node_fixture_accepts_the_full_daily_source_timeout(tmp_path, ui_server):
    ui_server["html"] = room_html(1, initial=1, terminal=True)
    port = PublicBrowserSource(
        PublicSourceConfig(
            chrome_executable=CHROME,
            evidence_root=tmp_path / "observations",
            environment="fixture",
            timeout_seconds=17100,
            fixture_url=ui_server["url"],
            headless=True,
        )
    )

    payload = port.search(room_request())

    assert payload["status"] == "observed", payload.get("error_code")
    assert len(payload["posts"]) == 1


def test_expired_room_deadline_does_not_launch_a_browser(
    tmp_path, ui_server, monkeypatch
):
    from datetime import datetime, timedelta, timezone

    from automation.inago import live_sources

    ui_server["html"] = room_html(1, initial=1, terminal=True)
    request = room_request()
    expired = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
    request = RoomSearchRequest(
        request.attempt_id,
        request.run_id,
        request.query,
        request.parent_refs,
        request.level,
        request.attempt_no,
        request.retry_of,
        expired,
        query_kind=request.query_kind,
        keyword_snapshot_ref=request.keyword_snapshot_ref,
        selected_word_ref=request.selected_word_ref,
    )
    port = source(tmp_path, ui_server)
    monkeypatch.setattr(
        live_sources.subprocess,
        "Popen",
        Mock(side_effect=AssertionError("expired request must not launch Node")),
    )

    payload = port.search(request)

    assert payload["status"] == "unavailable"
    assert payload["error_code"] == "source_timeout"
    assert ui_server["requests"] == []


def test_deadline_with_less_than_one_second_does_not_launch_node(
    tmp_path, ui_server, monkeypatch
):
    from automation.inago import live_sources

    ui_server["html"] = room_html(1, initial=1, terminal=True)
    port = source(tmp_path, ui_server)
    monkeypatch.setattr(port, "_request_timeout_seconds", lambda _request: 0.5)
    monkeypatch.setattr(
        live_sources.subprocess,
        "Popen",
        Mock(side_effect=AssertionError("sub-second request must not launch Node")),
    )

    payload = port.search(room_request())

    assert payload["status"] == "unavailable"
    assert payload["error_code"] == "source_timeout"
    assert ui_server["requests"] == []


def test_deadline_crossing_before_popen_does_not_launch_node(
    tmp_path, ui_server, monkeypatch
):
    from automation.inago import live_sources

    ui_server["html"] = room_html(1, initial=1, terminal=True)
    port = source(tmp_path, ui_server)
    monkeypatch.setattr(port, "_request_timeout_seconds", Mock(side_effect=[2.0, 0.5]))
    monkeypatch.setattr(
        live_sources.subprocess,
        "Popen",
        Mock(side_effect=AssertionError("expired recheck must not launch Node")),
    )

    payload = port.search(room_request())

    assert payload["status"] == "unavailable"
    assert payload["error_code"] == "source_timeout"
    assert ui_server["requests"] == []


def test_node_deadline_check_precedes_browser_launch(tmp_path, ui_server):
    from datetime import datetime, timedelta, timezone

    request = asdict(room_request())
    request["deadline_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=2)
    ).isoformat()
    payload = {
        "kind": "room",
        "request": request,
        "config": {
            "chrome_executable": str(tmp_path / "must-not-launch-chrome.exe"),
            "environment": "fixture",
            "fixture_url": ui_server["url"],
            "timeout_seconds": 10,
            "headless": True,
            "user_data_dir": None,
            "fixture_product_delay_ms": 0,
            "fixture_browser_startup_delay_ms": 0,
        },
    }
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(ROOT / "automation" / "inago" / "adapters" / "public_sources.mjs"),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 1
    result = json.loads(completed.stdout)
    assert result == {"error_code": "source_timeout"}
    assert "deadline_reached_before_browser_launch" in completed.stderr
    assert "browser_launch_begin" not in completed.stderr
    assert ui_server["requests"] == []


def test_node_startup_that_crosses_deadline_never_navigates(tmp_path, ui_server):
    from datetime import datetime, timedelta, timezone

    request = asdict(room_request())
    request["deadline_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=3)
    ).isoformat()
    payload = {
        "kind": "room",
        "request": request,
        "config": {
            "chrome_executable": str(CHROME),
            "environment": "fixture",
            "fixture_url": ui_server["url"],
            "timeout_seconds": 10,
            "headless": True,
            "user_data_dir": None,
            "fixture_product_delay_ms": 0,
            "fixture_browser_startup_delay_ms": 2500,
        },
    }
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(ROOT / "automation" / "inago" / "adapters" / "public_sources.mjs"),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )

    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {"error_code": "source_timeout"}
    assert "browser_launch_complete" in completed.stderr
    assert "browser_page_begin" not in completed.stderr
    assert ui_server["requests"] == []


def test_room_does_not_scroll_for_more_results_after_deadline(tmp_path, ui_server):
    from dataclasses import replace
    from datetime import datetime, timedelta, timezone

    ui_server["html"] = room_html(
        count=50, initial=1, terminal=False, scroll_marker=True
    )
    request = replace(
        room_request(),
        deadline_at=(datetime.now(timezone.utc) + timedelta(seconds=4)).isoformat(),
    )
    payload = source(
        tmp_path,
        ui_server,
        timeout=10,
        fixture_room_scroll_delay_ms=10000,
    ).search(request)

    assert payload["status"] == "unavailable"
    assert payload["error_code"] == "source_timeout"
    assert "/room-scroll-observed" not in ui_server["requests"]


def test_source_timeout_is_bounded_by_daily_window(tmp_path):
    with pytest.raises(ValueError, match="source timeout"):
        PublicBrowserSource(
            PublicSourceConfig(
                chrome_executable=CHROME,
                evidence_root=tmp_path,
                timeout_seconds=17101,
            )
        )


def test_public_source_defaults_to_headed_and_allows_explicit_headless_mode(tmp_path):
    default = PublicSourceConfig(chrome_executable=CHROME, evidence_root=tmp_path)
    headless = PublicSourceConfig(
        chrome_executable=CHROME, evidence_root=tmp_path, headless=True
    )

    assert default.headless is False
    assert headless.headless is True


def test_source_rejects_non_boolean_browser_mode(tmp_path):
    with pytest.raises(ValueError, match="headless must be a boolean"):
        PublicBrowserSource(
            PublicSourceConfig(
                chrome_executable=CHROME,
                evidence_root=tmp_path,
                headless="false",
            )
        )


def test_public_source_uses_only_the_owned_persistent_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    profile = tmp_path / "InagoPlaywrightProfile"
    profile.mkdir()
    port = PublicBrowserSource(
        PublicSourceConfig(
            chrome_executable=CHROME, evidence_root=tmp_path / "evidence"
        )
    )

    assert port._user_data_dir == profile.resolve()
    assert port.fixture_only is False


def test_public_source_rejects_the_ordinary_chrome_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    ordinary = tmp_path / "Google" / "Chrome" / "User Data"
    ordinary.mkdir(parents=True)
    with pytest.raises(ValueError, match="only LOCALAPPDATA"):
        PublicBrowserSource(
            PublicSourceConfig(
                chrome_executable=CHROME,
                evidence_root=tmp_path / "evidence",
                user_data_dir=ordinary,
            )
        )


def test_public_source_rejects_other_existing_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    owned_profile = tmp_path / "InagoPlaywrightProfile"
    owned_profile.mkdir()
    other_profile = tmp_path / "OtherAutomationProfile"
    other_profile.mkdir()

    with pytest.raises(ValueError, match="only LOCALAPPDATA"):
        PublicBrowserSource(
            PublicSourceConfig(
                chrome_executable=CHROME,
                evidence_root=tmp_path / "evidence",
                user_data_dir=other_profile,
            )
        )


def test_public_source_rejects_owned_profile_reparse_point(tmp_path, monkeypatch):
    from automation.inago import live_sources

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    owned_profile = tmp_path / "InagoPlaywrightProfile"
    owned_profile.mkdir()
    real_lstat = Path.lstat
    real_is_symlink = Path.is_symlink

    class ReparseMetadata:
        st_file_attributes = 0x400

    monkeypatch.setattr(
        Path,
        "lstat",
        lambda path: (ReparseMetadata() if path == owned_profile else real_lstat(path)),
    )
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: False if path == owned_profile else real_is_symlink(path),
    )

    with pytest.raises(ValueError, match="symlink or junction"):
        PublicBrowserSource(
            PublicSourceConfig(
                chrome_executable=CHROME, evidence_root=tmp_path / "evidence"
            )
        )


def test_reparse_point_detector_reads_windows_reparse_attribute(monkeypatch, tmp_path):
    from automation.inago import live_sources

    class ReparseMetadata:
        st_file_attributes = 0x400

    monkeypatch.setattr(Path, "lstat", lambda _path: ReparseMetadata())
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)

    assert live_sources.PublicBrowserSource._has_reparse_point(tmp_path / "profile")


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


def test_room_resolves_visible_link_on_detail_label_ancestor(tmp_path, ui_server):
    ui_server["html"] = room_html(1, initial=1, terminal=True, wrapped_href=True)
    payload = source(tmp_path, ui_server).search(room_request())

    assert payload["status"] == "observed", payload.get("error_code")
    assert payload["posts"][0]["item_url"] == "https://item.rakuten.co.jp/shop/item-1/"
    assert payload["posts"][0]["availability"] == "observed"
    assert not any(path.startswith("/product-") for path in ui_server["requests"])
    record = json.loads(
        next((tmp_path / "observations").glob("room-*.json")).read_text(
            encoding="utf-8"
        )
    )
    link_trace = next(
        event
        for event in record["node_events"]
        if event.get("stage") == "room_detail_control_inspected"
    )
    assert link_trace["link_scope"] == "ancestor"
    stages = {event.get("stage") for event in record["node_events"]}
    assert {
        "room_preview_click_begin",
        "room_detail_button_ready",
        "room_detail_button_click_begin",
        "room_product_page_rendered",
    } <= stages


def test_room_waits_over_two_seconds_for_detail_label_then_product_render(
    tmp_path, ui_server
):
    ui_server["html"] = room_html(1, initial=1, terminal=True, detail_delay_ms=12000)
    payload = source(tmp_path, ui_server).search(room_request())

    assert payload["status"] == "observed", payload.get("error_code")
    assert payload["posts"][0]["item_url"] == "https://item.rakuten.co.jp/shop/item-1/"
    record = json.loads(
        next((tmp_path / "observations").glob("room-*.json")).read_text(
            encoding="utf-8"
        )
    )
    stages = {
        event.get("stage") for event in record["node_events"] if isinstance(event, dict)
    }
    assert {
        "room_preview_click_begin",
        "room_detail_button_ready",
        "room_detail_button_click_begin",
        "room_product_page_rendered",
    } <= stages


def test_room_product_navigation_waits_longer_than_old_fifteen_second_cap(
    tmp_path, ui_server
):
    ui_server["html"] = room_html(1, initial=1, terminal=True)
    request = room_request()
    payload = source(tmp_path, ui_server, fixture_product_delay_ms=16000).search(
        request
    )
    result = parse_room_response(payload, request)

    assert result.status == "observed", payload.get("error_code")
    assert len(result.posts) == 1
    assert result.posts[0].item_url == "https://item.rakuten.co.jp/shop/item-1/"
    record = json.loads(
        next((tmp_path / "observations").glob("room-*.json")).read_text(
            encoding="utf-8"
        )
    )
    stages = {
        event.get("stage") for event in record["node_events"] if isinstance(event, dict)
    }
    assert {
        "room_detail_button_ready",
        "room_detail_button_click_begin",
        "room_product_navigation_complete",
        "room_product_page_rendered",
    } <= stages


@pytest.mark.parametrize("count", [0, 37])
def test_explicit_room_end_preserves_normal_actual_count(tmp_path, ui_server, count):
    ui_server["html"] = room_html(count, terminal=True)
    if count == 0:
        ui_server["html"] = ui_server["html"].replace(
            "検索結果は以上です", "検索結果がありません"
        )
    payload = source(tmp_path, ui_server).search(room_request())
    result = parse_room_response(payload, room_request())
    assert result.status == "observed", payload.get("error_code")
    assert (
        len(result.posts) == count
        and result.end_reached
        and result.end_reason == "exhausted"
    )


def test_short_nonterminal_room_batch_finishes_after_bounded_stable_scrolls(
    tmp_path, ui_server
):
    ui_server["html"] = room_html(1, initial=1, terminal=False)
    request = room_request()

    payload = source(tmp_path, ui_server, timeout=12).search(request)
    result = parse_room_response(payload, request)

    assert result.status == "observed", payload.get("error_code")
    assert len(result.posts) == result.loaded_posts == 1
    assert result.end_reached
    record = json.loads(
        next((tmp_path / "observations").glob("room-*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert record["diagnostics"]["end_evidence"] == "stable_no_progress"
    assert any(
        event.get("stage") == "room_short_batch_stable"
        for event in record["node_events"]
    )


def test_room_card_item_url_still_uses_preview_and_market_details_route(
    tmp_path, ui_server
):
    ui_server["html"] = room_html(1, initial=1, terminal=True, card_direct_href=True)
    request = room_request()
    payload = source(tmp_path, ui_server).search(request)
    result = parse_room_response(payload, request)
    assert result.status == "observed", payload.get("error_code")
    assert len(result.posts) == 1
    assert result.posts[0].offer_key == "shop:item-1"
    assert result.posts[0].item_url == "https://item.rakuten.co.jp/shop/item-1/"
    record = json.loads(
        next((tmp_path / "observations").glob("room-*.json")).read_text(
            encoding="utf-8"
        )
    )
    stages = {event.get("stage") for event in record["node_events"]}
    assert {
        "room_preview_click_begin",
        "room_detail_button_ready",
        "room_detail_button_click_begin",
        "room_product_page_rendered",
    } <= stages


def test_room_detail_button_can_navigate_same_tab_and_restore_search_results(
    tmp_path, ui_server
):
    ui_server["html"] = room_html(2, initial=2, terminal=True, same_tab=True)
    payload = source(tmp_path, ui_server).search(room_request())
    result = parse_room_response(payload, room_request())

    assert result.status == "observed", payload.get("error_code")
    assert len(result.posts) == 2
    assert all(
        post.item_url == "https://item.rakuten.co.jp/shop/item-1/"
        for post in result.posts
    )
    record = json.loads(
        next((tmp_path / "observations").glob("room-*.json")).read_text(
            encoding="utf-8"
        )
    )
    stages = {
        event.get("stage") for event in record["node_events"] if isinstance(event, dict)
    }
    assert {
        "room_detail_button_ready",
        "room_same_tab_navigation_observed",
        "room_product_page_rendered",
        "room_search_restored",
    } <= stages


def test_slow_same_tab_navigation_is_waited_for_after_detail_button(
    tmp_path, ui_server
):
    ui_server["html"] = room_html(
        1, initial=1, terminal=True, same_tab=True, same_tab_delay_ms=2500
    )
    payload = source(tmp_path, ui_server).search(room_request())
    result = parse_room_response(payload, room_request())

    assert result.status == "observed", payload.get("error_code")
    assert result.posts[0].item_url == "https://item.rakuten.co.jp/shop/item-1/"
    record = json.loads(
        next((tmp_path / "observations").glob("room-*.json")).read_text(
            encoding="utf-8"
        )
    )
    stages = {
        event.get("stage") for event in record["node_events"] if isinstance(event, dict)
    }
    assert {
        "room_same_tab_navigation_observed",
        "room_product_page_rendered",
        "room_search_restored",
    } <= stages


def test_same_tab_navigation_after_click_timeout_restores_and_fails_source(
    tmp_path, ui_server
):
    ui_server["html"] = room_html(1, initial=1, terminal=True, same_tab=True)
    payload = source(tmp_path, ui_server, fixture_product_delay_ms=16000).search(
        room_request()
    )

    assert payload["status"] == "observed"
    assert len(payload["posts"]) == 1
    assert payload["posts"][0]["item_url"] is None
    record = json.loads(
        next((tmp_path / "observations").glob("room-*.json")).read_text(
            encoding="utf-8"
        )
    )
    stages = {
        event.get("stage") for event in record["node_events"] if isinstance(event, dict)
    }
    click_error = next(
        event
        for event in record["node_events"]
        if event.get("stage") == "room_detail_button_click_error"
    )
    assert click_error["scope"] == "row"
    assert "room_same_tab_navigation_observed" not in stages
    assert "room_search_restored" not in stages


def test_same_tab_search_restore_is_skipped_after_room_deadline(tmp_path, ui_server):
    ui_server["html"] = room_html(1, initial=1, terminal=True, same_tab=True)
    payload = source(
        tmp_path,
        ui_server,
        fixture_expire_deadline_before_room_restore=True,
    ).search(room_request())

    assert payload["status"] == "unavailable"
    assert payload["error_code"] == "source_timeout"
    record = json.loads(
        next((tmp_path / "observations").glob("room-*.json")).read_text(
            encoding="utf-8"
        )
    )
    stages = {
        event.get("stage") for event in record["node_events"] if isinstance(event, dict)
    }
    assert "room_same_tab_navigation_observed" in stages
    assert "room_search_restore_skipped_deadline" in stages
    assert "room_search_restored" not in stages


def test_last_room_product_timeout_is_not_reported_as_success(tmp_path, ui_server):
    from datetime import datetime, timedelta, timezone

    ui_server["html"] = room_html(1, initial=1, terminal=True, detail_delay_ms=12000)
    request = room_request()
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=3)).isoformat()
    request = RoomSearchRequest(
        request.attempt_id,
        request.run_id,
        request.query,
        request.parent_refs,
        request.level,
        request.attempt_no,
        request.retry_of,
        deadline,
        query_kind=request.query_kind,
        keyword_snapshot_ref=request.keyword_snapshot_ref,
        selected_word_ref=request.selected_word_ref,
    )

    payload = source(tmp_path, ui_server, timeout=25).search(request)

    assert payload["status"] == "unavailable"
    assert payload["error_code"] == "source_timeout"


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


def test_durable_trends_http429_request_ids_can_retry_same_public_ui(
    tmp_path, ui_server
):
    ui_server.update(status=429, html="Too Many Requests", retry_after="125")
    port = source(tmp_path, ui_server)
    first = TrendsRequest(
        "RETRY-RUN:initial:01:T01-a1", "RETRY-RUN", "V1", "電子レンジ", range="3m"
    )
    second = TrendsRequest(
        "RETRY-RUN:initial:01:T01-a2", "RETRY-RUN", "V1", "電子レンジ", range="3m"
    )

    first_result = port.fetch(first)
    second_result = port.fetch(second)
    assert first_result["error_code"] == "http_429"
    assert first_result["retry_after_seconds"] == 125
    assert second_result["error_code"] == "http_429"
    assert sum(path == "/trends/" for path in ui_server["requests"]) == 2


def test_trends_source_timeout_remains_retryable_after_response_validation(
    tmp_path, ui_server
):
    from datetime import datetime, timedelta, timezone

    port = source(tmp_path, ui_server)
    request = TrendsRequest(
        "TRENDS-TIMEOUT",
        "RUN-TIMEOUT",
        "V1",
        "電子レンジ",
        range="3m",
        deadline_at=(
            datetime.now(timezone.utc) + timedelta(milliseconds=700)
        ).isoformat(),
    )

    response = port.fetch(request)
    snapshot = parse_grouped_top_response(response, request)

    assert response["status"] == "unavailable"
    assert response["error_code"] == "source_timeout"
    assert "deadline_at" not in response
    assert snapshot.status == "unavailable"
    assert snapshot.error_code == "source_timeout"
    assert ui_server["requests"] == []


def test_trends_popularity_only_original_top20_preserves_raw(tmp_path, ui_server):
    ui_server["html"] = trends_html(refresh_after_period=True)
    request = TrendsRequest("K1", "R1", "V1", "電子レンジ", range="3m")
    payload = source(tmp_path, ui_server).fetch(request)
    result = parse_grouped_top_response(payload, request)
    assert result.status == "observed", payload.get("error_code")
    assert [word.source_rank for word in result.words] == list(range(1, 21))
    assert result.words[0].raw == "関連  語1"
    assert result.ui_period_label == "過去 90 日間"
    assert all(word.popularity_index == 101 - word.source_rank for word in result.words)


def test_trends_top_rows_are_complete_without_network_idle(tmp_path, ui_server):
    ui_server["html"] = trends_html(count=20, refresh_after_period=True).replace(
        "topMode=true;render()",
        "topMode=true;render();fetch('/hang-pending').catch(()=>{})",
    )
    port = source(tmp_path, ui_server, timeout=4)
    request = TrendsRequest(
        "TRENDS-NETWORKIDLE",
        "RUN-TRENDS-NETWORKIDLE",
        "V1",
        "電子レンジ",
        range="3m",
    )

    try:
        response = port.fetch(request)
    finally:
        ui_server["pending_request_release"].set()

    assert ui_server["pending_request_started"].is_set()
    assert response["status"] == "observed", response.get("error_code")
    assert len(response["words"]) == 20


def test_trends_deadline_after_home_query_fill_does_not_submit_explore(
    tmp_path, ui_server
):
    from datetime import datetime, timedelta, timezone

    ui_server["pages"].update(
        {
            "/trends/": trends_home_html(input_delay_ms=8000),
            "/trends/explore": trends_period_html(),
        }
    )
    request = asdict(
        TrendsRequest(
            "K-DEADLINE",
            "R1",
            "V1",
            "電子レンジ",
            range="3m",
            deadline_at=(datetime.now(timezone.utc) + timedelta(seconds=7)).isoformat(),
        )
    )
    payload = {
        "kind": "trends",
        "request": request,
        "config": {
            "chrome_executable": str(CHROME),
            "environment": "fixture",
            "fixture_url": ui_server["url"],
            "timeout_seconds": 25,
            "headless": True,
            "user_data_dir": None,
            "fixture_product_delay_ms": 0,
            "fixture_browser_startup_delay_ms": 0,
        },
    }
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(ROOT / "automation" / "inago" / "adapters" / "public_sources.mjs"),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {"error_code": "source_timeout"}
    assert "/trends/" in ui_server["requests"]
    assert not any(
        path.startswith("/trends/explore?") for path in ui_server["requests"]
    )


def test_trends_top_option_that_appears_after_deadline_is_not_clicked(
    tmp_path, ui_server
):
    from datetime import datetime, timedelta, timezone

    explore_html = (
        trends_period_html(refresh_after_period=True)
        .replace(
            "onclick=\"document.querySelector('#choice').style.display='block'\"",
            "onclick=\"setTimeout(()=>document.querySelector('#choice').style.display='block',9000)\"",
        )
        .replace(
            "document.querySelector('md-select.bullets-view-selector').textContent='人気';",
            "fetch('/trends-top').catch(()=>{});"
            "document.querySelector('md-select.bullets-view-selector').textContent='人気';",
        )
    )
    ui_server["pages"].update(
        {"/trends/": trends_home_html(), "/trends/explore": explore_html}
    )
    request = asdict(
        TrendsRequest(
            "K-TOP-DEADLINE",
            "R1",
            "V1",
            "電子レンジ",
            range="3m",
            deadline_at=(
                datetime.now(timezone.utc) + timedelta(seconds=10)
            ).isoformat(),
        )
    )
    payload = {
        "kind": "trends",
        "request": request,
        "config": {
            "chrome_executable": str(CHROME),
            "environment": "fixture",
            "fixture_url": ui_server["url"],
            "timeout_seconds": 25,
            "headless": True,
            "user_data_dir": None,
            "fixture_product_delay_ms": 0,
            "fixture_browser_startup_delay_ms": 0,
        },
    }
    completed = subprocess.run(
        [
            shutil.which("node"),
            str(ROOT / "automation" / "inago" / "adapters" / "public_sources.mjs"),
        ],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert completed.returncode == 1
    assert json.loads(completed.stdout) == {"error_code": "source_timeout"}
    assert "trends_switch_top" in completed.stderr
    assert "/trends/" in ui_server["requests"]
    assert any(path.startswith("/trends/explore?") for path in ui_server["requests"])
    assert "/trends-top" not in ui_server["requests"]


def test_trends_starts_from_home_searches_and_selects_rendered_90_day_period(
    tmp_path, ui_server
):
    ui_server["pages"].update(
        {
            "/trends/": trends_home_html(),
            "/trends/explore": trends_period_html(refresh_after_period=True),
        }
    )
    request = TrendsRequest("K-HOME", "R1", "V1", "電子レンジ", range="3m")

    result = parse_grouped_top_response(
        source(tmp_path, ui_server).fetch(request), request
    )

    assert result.status == "observed" and len(result.words) == 20
    assert result.ui_period_label == "過去 90 日間"
    explore_request = next(
        path for path in ui_server["requests"] if path.startswith("/trends/explore?")
    )
    assert "/trends/" in ui_server["requests"]
    assert "date=today+12-m" in explore_request
    assert (
        "geo=JP" in explore_request
        and "q=%E9%9B%BB%E5%AD%90%E3%83%AC%E3%83%B3%E3%82%B8" in explore_request
    )
    record = json.loads(
        next((tmp_path / "observations").glob("trends-*.json")).read_text(
            encoding="utf-8"
        )
    )
    home_navigation = [
        event
        for event in record["node_events"]
        if event.get("event") == "public_source_diagnostic"
        and event.get("host_class") in {"other", "trends"}
        and event.get("page_role") == "main"
        and event.get("stage")
        in {"trends_home_navigation", "trends_home_commit_observed"}
    ]
    assert any(
        event.get("signal") == "navigation_response" for event in home_navigation
    )
    assert any(
        event.get("signal") == "navigation_finished" for event in home_navigation
    )
    stages = {
        event.get("stage")
        for event in record["node_events"]
        if event.get("event") == "public_source_stage"
    }
    assert {
        "trends_home_commit_observed",
        "trends_home_search_wait_begin",
        "trends_home_search_visible",
        "trends_home_submit_control",
        "trends_home_submit_sent",
        "trends_period_menu_opened",
        "trends_period_option_visible",
        "trends_period_option_clicked",
        "trends_period_reflected",
    } <= stages


def test_trends_home_search_ambiguity_fails_closed_with_safe_dom_counts(
    tmp_path, ui_server
):
    ui_server["pages"].update(
        {
            "/trends/": (
                '<form><input aria-label="検索"><input aria-label="検索"></form>'
            ),
            "/trends/explore": trends_period_html(),
        }
    )
    request = TrendsRequest("K-HOME-AMBIGUOUS", "R1", "V1", "電子レンジ", range="3m")

    payload = source(tmp_path, ui_server, timeout=10).fetch(request)

    assert payload["status"] == "unavailable"
    assert payload["error_code"] == "home_search_ambiguous"
    record = json.loads(
        next((tmp_path / "observations").glob("trends-*.json")).read_text(
            encoding="utf-8"
        )
    )
    probe = next(
        event
        for event in record["node_events"]
        if event.get("stage") == "trends_home_search_candidates"
    )
    assert probe["search_candidate_count"] == 2
    assert probe["visible_input_count"] == 2
    assert probe["document_ready_state"] in {"interactive", "complete"}


def test_trends_home_search_waits_for_open_shadow_dom_control(tmp_path, ui_server):
    ui_server["pages"].update(
        {
            "/trends/": """<div id="search-host"></div><script>
              const root = document.querySelector('#search-host').attachShadow({mode:'open'});
              root.innerHTML = '<form><input aria-label="検索"><button type="submit">検索</button></form>';
              root.querySelector('form').addEventListener('submit', event => {
                event.preventDefault();
                const url = new URL('/trends/explore', location.origin);
                url.searchParams.set('date', 'today 12-m');
                url.searchParams.set('geo', 'JP');
                url.searchParams.set('q', root.querySelector('input').value);
                location.href = url.href;
              });
            </script>""",
            "/trends/explore": trends_period_html(refresh_after_period=True),
        }
    )
    request = TrendsRequest("K-HOME-SHADOW", "R1", "V1", "電子レンジ", range="3m")

    payload = source(tmp_path, ui_server, timeout=10).fetch(request)

    result = parse_grouped_top_response(payload, request)
    assert result.status == "observed" and len(result.words) == 20
    assert any(path.startswith("/trends/explore?") for path in ui_server["requests"])


def test_trends_home_search_clicks_visible_explore_button_when_enter_does_not_submit(
    tmp_path, ui_server
):
    ui_server["pages"].update(
        {
            "/trends/": """<input aria-label="検索"><button id="search" type="button">調べる</button>
              <script>
                document.querySelector('input').addEventListener('keydown', event => {
                  if (event.key === 'Enter') event.preventDefault();
                });
                document.querySelector('#search').addEventListener('click', () => {
                  const url = new URL('/trends/explore', location.origin);
                  url.searchParams.set('date', 'today 12-m');
                  url.searchParams.set('geo', 'JP');
                  url.searchParams.set('q', document.querySelector('input').value);
                  location.href = url.href;
                });
              </script>""",
            "/trends/explore": trends_period_html(refresh_after_period=True),
        }
    )
    request = TrendsRequest("K-HOME-BUTTON", "R1", "V1", "電子レンジ", range="3m")

    payload = source(tmp_path, ui_server, timeout=8).fetch(request)

    result = parse_grouped_top_response(payload, request)
    assert result.status == "observed" and len(result.words) == 20
    assert any(path.startswith("/trends/explore?") for path in ui_server["requests"])


@pytest.mark.parametrize("count", [0, 3])
def test_trends_explicit_zero_and_fewer_rows_do_not_fill_from_other_source(
    tmp_path, ui_server, count
):
    html = trends_html(
        count=count,
        refresh_after_period=True,
        top_selected=count == 0,
    )
    ui_server["html"] = html
    request = TrendsRequest("K1", "R1", "V1", "電子レンジ", range="3m")
    result = parse_grouped_top_response(
        source(tmp_path, ui_server).fetch(request), request
    )
    assert (result.status, len(result.words)) == ("observed", count), result.error_code


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
    from datetime import datetime, timedelta, timezone

    port = source(tmp_path, ui_server)
    fact = {
        "offer_key": "shop:item-1",
        "name": "合成商品",
        "item_url": "https://item.rakuten.co.jp/shop/item-1/",
        "evidence_ref": "S1:row:1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    path = tmp_path / "observations" / "room-S1-observation-01.json"
    path.write_text(
        json.dumps(
            {
                "kind": "room",
                "request": {"attempt_id": "S1", "run_id": "R1"},
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "retention_expires_at": (
                    datetime.now(timezone.utc) + timedelta(days=90)
                ).isoformat(),
                "product_facts": {"S1:row:1": fact},
            }
        ),
        encoding="utf-8",
    )
    assert port.product_fact("S1:row:1") == fact
    assert port.active_process_count == 0 and ui_server["requests"] == []


def test_expired_public_source_observation_cannot_be_replayed_or_used(
    tmp_path, ui_server
):
    from datetime import datetime, timedelta, timezone

    port = source(tmp_path, ui_server)
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    root = tmp_path / "observations"
    trends_record = root / "trends-K-EXPIRED-observation-01.json"
    trends_record.write_text(
        json.dumps(
            {
                "kind": "trends",
                "request": {
                    "request_id": "K-EXPIRED",
                    "run_id": "R1",
                    "settings_version": "V1",
                    "parent_word": "電子レンジ",
                },
                "recorded_at": expired_at,
                "retention_expires_at": expired_at,
                "response": {"status": "observed", "words": []},
            }
        ),
        encoding="utf-8",
    )
    room_record = root / "room-S-EXPIRED-observation-01.json"
    room_record.write_text(
        json.dumps(
            {
                "kind": "room",
                "request": {"attempt_id": "S-EXPIRED", "run_id": "R1"},
                "recorded_at": expired_at,
                "retention_expires_at": expired_at,
                "product_facts": {
                    "S-EXPIRED:row:1": {
                        "offer_key": "shop:item",
                        "item_url": "https://item.rakuten.co.jp/shop/item/",
                        "evidence_ref": "S-EXPIRED:row:1",
                        "observed_at": expired_at,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expired"):
        port.load_saved_response("K-EXPIRED")
    assert port.product_fact("S-EXPIRED:row:1") is None
    port._facts["S-EXPIRED:row:1"] = {
        "offer_key": "shop:item",
        "item_url": "https://item.rakuten.co.jp/shop/item/",
        "evidence_ref": "S-EXPIRED:row:1",
        "observed_at": (datetime.now(timezone.utc) - timedelta(days=91)).isoformat(),
    }
    assert port.product_fact("S-EXPIRED:row:1") is None


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
    ui_server["html"] = trends_html(delay=700, refresh_after_period=True)
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
    payload = source(tmp_path, ui_server, timeout=30).search(room_request())
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
                    "stage": "room_detail_button_ready",
                    "elapsed_ms": 1234,
                    "loaded_posts": 80,
                    "accepted_posts": 50,
                    "row_index": 7,
                    "phase": "detail_button_ready",
                    "popup_url_kind": "blank",
                    "popup_url_length": 11,
                    "link_scope": "ancestor",
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
            "stage": "room_detail_button_ready",
            "elapsed_ms": 1234,
            "loaded_posts": 80,
            "accepted_posts": 50,
            "row_index": 7,
            "phase": "detail_button_ready",
            "popup_url_kind": "blank",
            "popup_url_length": 11,
            "link_scope": "ancestor",
        }
    ]
    assert "SECRET-FAKE" not in json.dumps(events)


def test_product_navigation_timeout_trace_keeps_only_safe_destination_metadata():
    raw = json.dumps(
        {
            "event": "public_source_stage",
            "stage": "room_product_timeout",
            "row_index": 42,
            "phase": "product_navigation",
            "popup_url_kind": "rakuten_other",
            "popup_url_length": 37,
            "url": "https://example.invalid/private/path?token=secret",
        }
    )

    events = PublicBrowserSource._metadata_events(raw)

    assert events == [
        {
            "event": "public_source_stage",
            "stage": "room_product_timeout",
            "row_index": 42,
            "phase": "product_navigation",
            "popup_url_kind": "rakuten_other",
            "popup_url_length": 37,
        }
    ]
    assert "private" not in json.dumps(events)
    assert "secret" not in json.dumps(events)


def test_metadata_trace_keeps_records_beyond_old_one_thousand_line_cap():
    raw = "\n".join(
        json.dumps(
            {
                "event": "public_source_stage",
                "stage": "room_product_timeout",
                "row_index": index % 50 + 1,
                "phase": "product_navigation",
                "popup_url_kind": "rakuten_other",
                "popup_url_length": 37,
            }
        )
        for index in range(1501)
    )

    events = PublicBrowserSource._metadata_events(raw)

    assert len(events) == 1501
    assert events[-1]["row_index"] == 1


def test_diagnostic_trace_keeps_only_allowlisted_browser_error_metadata():
    raw = "\n".join(
        [
            json.dumps(
                {
                    "event": "public_source_diagnostic",
                    "signal": "page_error",
                    "stage": "product_navigation",
                    "row_index": 4,
                    "page_role": "popup",
                    "error_class": "TypeError",
                    "count": 2,
                    "message": "contains private page text",
                    "url": "https://example.invalid/private/path?token=secret",
                }
            ),
            json.dumps(
                {
                    "event": "public_source_diagnostic",
                    "signal": "navigation_request",
                    "stage": "product_navigation",
                    "row_index": 4,
                    "page_role": "popup",
                    "count": 1,
                    "first_elapsed_ms": 321,
                    "host_class": "product",
                    "resource_type": "document",
                    "url": "https://item.rakuten.co.jp/private/path?token=secret",
                }
            ),
            json.dumps(
                {
                    "event": "public_source_diagnostic",
                    "signal": "navigation_response",
                    "stage": "product_navigation",
                    "row_index": 4,
                    "page_role": "popup",
                    "count": 1,
                    "first_elapsed_ms": 654,
                    "host_class": "product",
                    "resource_type": "document",
                    "status": 200,
                    "url": "https://item.rakuten.co.jp/private/path?token=secret",
                }
            ),
            json.dumps(
                {
                    "event": "public_source_diagnostic",
                    "signal": "summary",
                    "page_count": 2,
                    "console_error_count": 0,
                    "page_error_count": 0,
                    "failed_request_count": 1,
                    "popup_navigation_count": 1,
                    "dropped_groups": 0,
                    "token": "SECRET-FAKE",
                }
            ),
        ]
    )

    events = PublicBrowserSource._metadata_events(raw)

    assert events == [
        {
            "event": "public_source_diagnostic",
            "signal": "page_error",
            "stage": "product_navigation",
            "row_index": 4,
            "page_role": "popup",
            "error_class": "TypeError",
            "count": 2,
        },
        {
            "event": "public_source_diagnostic",
            "signal": "navigation_request",
            "stage": "product_navigation",
            "page_role": "popup",
            "count": 1,
            "first_elapsed_ms": 321,
            "row_index": 4,
            "host_class": "product",
            "resource_type": "document",
        },
        {
            "event": "public_source_diagnostic",
            "signal": "navigation_response",
            "stage": "product_navigation",
            "page_role": "popup",
            "count": 1,
            "first_elapsed_ms": 654,
            "row_index": 4,
            "host_class": "product",
            "resource_type": "document",
            "status": 200,
        },
        {
            "event": "public_source_diagnostic",
            "signal": "summary",
            "page_count": 2,
            "console_error_count": 0,
            "page_error_count": 0,
            "failed_request_count": 1,
            "popup_navigation_count": 1,
            "dropped_groups": 0,
        },
    ]
    assert "private" not in json.dumps(events)
    assert "secret" not in json.dumps(events)


def test_source_failure_trace_keeps_only_safe_error_class_and_network_code():
    raw = json.dumps(
        {
            "event": "public_source_failure",
            "stage": "trends_home_navigation",
            "error_code": "browser_adapter_error",
            "error_class": "Error",
            "error_kind": "network_error",
            "network_code": "ERR_CONNECTION_RESET",
            "message": "private page text",
            "url": "https://example.invalid/private/path?token=secret",
        }
    )

    events = PublicBrowserSource._metadata_events(raw)

    assert events == [
        {
            "event": "public_source_failure",
            "stage": "trends_home_navigation",
            "error_code": "browser_adapter_error",
            "error_class": "Error",
            "error_kind": "network_error",
            "network_code": "ERR_CONNECTION_RESET",
        }
    ]
    assert "private" not in json.dumps(events)
    assert "secret" not in json.dumps(events)


def test_windows_colon_id_is_saved_in_ordinary_file_not_ads(tmp_path, ui_server):
    ui_server["html"] = trends_html(count=3, refresh_after_period=True)
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
    ui_server["html"] = trends_html(
        count=3, delay=900, rising_empty=True, refresh_after_period=True
    )
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
    ui_server["html"] = trends_html(
        count=3, delay=4000, rising_empty=True, refresh_after_period=True
    ).replace(
        "setTimeout(()=>{topMode=true;render()}",
        "setTimeout(()=>document.querySelector('#words span').style.display='none',100);setTimeout(()=>document.querySelector('#words span').style.display='block',200);setTimeout(()=>{topMode=true;render()}",
    )
    request = TrendsRequest("K1", "R1", "V1", "電子レンジ", range="3m")
    result = parse_grouped_top_response(
        source(tmp_path, ui_server).fetch(request), request
    )
    assert result.status == "observed" and len(result.words) == 3


def test_room_without_deadline_ignores_source_duration_limit(tmp_path, ui_server):
    from dataclasses import replace

    ui_server["html"] = room_html(1, initial=1, terminal=True)
    port = source(tmp_path, ui_server, timeout=1, fixture_product_delay_ms=1500)
    request = replace(room_request(), deadline_at=None)

    assert port._request_timeout_seconds(request) is None
    response = port.search(request)

    assert response["status"] == "observed", response.get("error_code")
    assert len(response["posts"]) == 1
    assert port._active == {}
