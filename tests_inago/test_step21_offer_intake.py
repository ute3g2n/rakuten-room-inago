from __future__ import annotations

import json

import pytest

from automation.inago.orchestrator import OfferIntakeRequest
from automation.inago.offer_intake import OfferIntakeError, SQLitePublicOfferIntake
from automation.inago.daily_runtime import compose_public_daily_ports
from tests_inago.test_step13 import runtime_case


OBSERVED_AT = "2026-10-12T13:20:00+09:00"


class FixtureProductFactSource:
    fixture_only = True

    def __init__(self, fact):
        self.fact = fact
        self.cancelled = []
        self.evidence_root = None

    def product_fact(self, evidence_ref):
        if self.fact is None:
            return None
        return dict(self.fact)

    def cancel_owned_jobs(self, run_id):
        self.cancelled.append(run_id)
        return True


def _request(**overrides):
    values = {
        "run_id": "RUN-TEST",
        "settings_version": "confirmed-0.3-step46-all132",
        "theme_id": "G01-T01",
        "offer_key": "shop-a:item-a",
        "source_key": "post-1",
        "item_url": "https://item.rakuten.co.jp/shop-a/item-a/",
        "image_ref": "https://image.example/item-a.jpg",
        "evidence_ref": "SEARCH-1:row:1",
        "observed_at": OBSERVED_AT,
    }
    values.update(overrides)
    return OfferIntakeRequest(**values)


def _fact(**overrides):
    value = {
        "offer_key": "shop-a:item-a",
        "name": "保存容器セット",
        "item_url": "https://item.rakuten.co.jp/shop-a/item-a/",
        "image_ref": "https://image.example/item-a.jpg",
        "evidence_ref": "SEARCH-1:row:1",
        "observed_at": OBSERVED_AT,
        "origin": "public_product_image_label",
    }
    value.update(overrides)
    return value


def test_public_observation_is_persisted_as_offer_with_evidence(runtime_case):
    source = FixtureProductFactSource(_fact())
    intake = SQLitePublicOfferIntake(source)

    intake.persist_offer(runtime_case.connection, _request())

    row = runtime_case.connection.execute(
        "SELECT shop_id,item_id,name,url,image_refs_json,main_theme_id,facts_json "
        "FROM offer WHERE offer_key=?",
        ("shop-a:item-a",),
    ).fetchone()
    assert tuple(row[:4]) == (
        "shop-a",
        "item-a",
        "保存容器セット",
        "https://item.rakuten.co.jp/shop-a/item-a/",
    )
    assert json.loads(row[4]) == ["https://image.example/item-a.jpg"]
    assert row[5] == "G01-T01"
    assert json.loads(row[6]) == [
        {
            "field": "name",
            "value": "保存容器セット",
            "availability": "observed",
            "source_ref": "SEARCH-1:row:1",
            "observed_at": OBSERVED_AT,
            "original_display": "保存容器セット",
        }
    ]


def test_missing_label_is_explicitly_unknown_and_does_not_block_offer_intake(
    runtime_case,
):
    source = FixtureProductFactSource(_fact(name=None))

    SQLitePublicOfferIntake(source).persist_offer(runtime_case.connection, _request())

    row = runtime_case.connection.execute(
        "SELECT name,facts_json FROM offer WHERE offer_key=?",
        ("shop-a:item-a",),
    ).fetchone()
    assert row[0] == "商品名未取得"
    assert json.loads(row[1]) == [
        {
            "field": "name",
            "value": None,
            "availability": "unknown",
            "source_ref": "SEARCH-1:row:1",
            "observed_at": OBSERVED_AT,
            "original_display": None,
        }
    ]


@pytest.mark.parametrize(
    "request_value,fact,reason",
    [
        (_request(offer_key="shop-a:other"), _fact(), "identity_mismatch"),
        (_request(evidence_ref="SEARCH-1:row:2"), _fact(), "evidence_mismatch"),
        (
            _request(),
            _fact(item_url="https://item.rakuten.co.jp/shop-b/item-a/"),
            "identity_mismatch",
        ),
        (_request(), _fact(origin="room_post_text"), "product_fact_origin_invalid"),
    ],
)
def test_offer_intake_rejects_cross_product_or_cross_evidence_fact(
    runtime_case, request_value, fact, reason
):
    source = FixtureProductFactSource(fact)
    intake = SQLitePublicOfferIntake(source)

    with pytest.raises(OfferIntakeError, match=reason):
        intake.persist_offer(runtime_case.connection, request_value)

    assert (
        runtime_case.connection.execute(
            "SELECT 1 FROM offer WHERE offer_key=?", ("shop-a:item-a",)
        ).fetchone()
        is None
    )


def test_public_daily_ports_reuse_one_root_for_copy_and_key_and_cancel_owned_sources(
    tmp_path,
):
    class Source(FixtureProductFactSource):
        def __init__(self):
            super().__init__(None)
            self.evidence_root = None
            self.config = type("Config", (), {"evidence_root": None})()

        def search(self, _request):
            return {}

        def fetch(self, _request):
            return {}

    class Root:
        fixture_only = True

        def __init__(self):
            self.cancelled = []

        def run(self, request):
            raise AssertionError("test only composes ports")

        def cancel_owned_jobs(self, run_id):
            self.cancelled.append(run_id)
            return True

    class Metrics:
        fixture_only = True

        def collect(self, *_args, **_kwargs):
            return "not_started"

    source = Source()
    data_root = tmp_path / "runtime"
    evidence_root = data_root / "observations"
    evidence_root.mkdir(parents=True)
    source.config.evidence_root = evidence_root
    source.evidence_root = source.config.evidence_root
    root = Root()
    copy_spec_factory = lambda *_args, **_kwargs: None
    copy_spec_factory.fixture_only = True
    metrics = Metrics()
    cancel_metrics = lambda _run_id: True
    cancel_metrics.fixture_only = True

    ports = compose_public_daily_ports(
        data_root=data_root,
        source=source,
        ai_root=root,
        copy_spec_factory=copy_spec_factory,
        metrics_port=metrics,
        cancel_metrics_job=cancel_metrics,
    )

    assert ports.room_port is source
    assert ports.copy_runner is root
    assert ports.theme_planner.key_runner is root
    assert ports.offer_intake.source is source
    assert ports.fixture_only is True
    assert ports.cancel_owned_jobs("RUN-TEST") is True
    assert source.cancelled == ["RUN-TEST"]
    assert root.cancelled == ["RUN-TEST"]


def test_composite_cancel_attempts_ai_root_when_source_cancel_raises(tmp_path):
    class Source(FixtureProductFactSource):
        def __init__(self, evidence_root):
            super().__init__(None)
            self.evidence_root = evidence_root
            self.config = type("Config", (), {"evidence_root": evidence_root})()

        def search(self, _request):
            return {}

        def fetch(self, _request):
            return {}

        def cancel_owned_jobs(self, _run_id):
            raise RuntimeError("fixture source cancel failure")

    class Root:
        fixture_only = True

        def __init__(self):
            self.cancelled = []

        def run(self, _request):
            raise AssertionError("test only composes ports")

        def cancel_owned_jobs(self, run_id):
            self.cancelled.append(run_id)
            return True

    class Metrics:
        fixture_only = True

        def collect(self, *_args, **_kwargs):
            return "not_started"

    data_root = tmp_path / "runtime"
    evidence_root = data_root / "observations"
    evidence_root.mkdir(parents=True)
    source = Source(evidence_root)
    root = Root()
    copy_spec_factory = lambda *_args, **_kwargs: None
    copy_spec_factory.fixture_only = True
    cancel_metrics = lambda _run_id: True
    cancel_metrics.fixture_only = True
    ports = compose_public_daily_ports(
        data_root=data_root,
        source=source,
        ai_root=root,
        copy_spec_factory=copy_spec_factory,
        metrics_port=Metrics(),
        cancel_metrics_job=cancel_metrics,
    )

    assert ports.cancel_owned_jobs("RUN-TEST") is False
    assert root.cancelled == ["RUN-TEST"]


def test_public_scheduler_entry_invokes_daily_with_composed_ports(
    monkeypatch, tmp_path
):
    from automation.inago import trigger

    class Source(FixtureProductFactSource):
        def __init__(self, evidence_root):
            super().__init__(None)
            self.evidence_root = evidence_root
            self.config = type("Config", (), {"evidence_root": evidence_root})()

        def search(self, _request):
            return {}

        def fetch(self, _request):
            return {}

    class Root:
        fixture_only = True

        def run(self, _request):
            raise AssertionError("test only checks the scheduler port")

        def cancel_owned_jobs(self, _run_id):
            return True

    class Metrics:
        fixture_only = True

        def collect(self, *_args, **_kwargs):
            return "not_started"

    data_root = tmp_path / "runtime"
    evidence_root = data_root / "observations"
    evidence_root.mkdir(parents=True)
    source = Source(evidence_root)
    root = Root()
    copy_spec_factory = lambda *_args, **_kwargs: None
    copy_spec_factory.fixture_only = True
    cancel_metrics = lambda _run_id: True
    cancel_metrics.fixture_only = True
    observed = {}

    def fake_run(connection, *, data_root, ports, clock, **kwargs):
        observed.update(
            connection=connection,
            data_root=data_root,
            ports=ports,
            clock=clock,
            kwargs=kwargs,
        )
        return "daily-result"

    monkeypatch.setattr(trigger, "run_scheduled_daily", fake_run)
    marker_connection = object()
    result = trigger.run_public_scheduled_daily(
        marker_connection,
        data_root=data_root,
        source=source,
        ai_root=root,
        copy_spec_factory=copy_spec_factory,
        metrics_port=Metrics(),
        cancel_metrics_job=cancel_metrics,
    )

    assert result == "daily-result"
    assert observed["connection"] is marker_connection
    assert observed["data_root"] == data_root
    assert observed["ports"].room_port is source
    assert observed["ports"].theme_planner.key_runner is root
    assert observed["ports"].copy_runner is root
    assert observed["ports"].offer_intake.source is source
