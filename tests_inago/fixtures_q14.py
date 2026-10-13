"""Current Q1.4 observed-word fixture planner; no external source access."""

from automation.inago import orchestrator as daily
from automation.inago import trends_adapter as trends


class CurrentPlanner:
    fixture_only = True

    def __init__(self, case, *, word_count=5, fail_at=None):
        self.case = case
        self.word_count = word_count
        self.fail_at = fail_at
        self.calls = []
        self.counts_before_plan = []

    def plan_theme(self, connection, **values):
        theme = values["theme_id"]
        self.calls.append(theme)
        self.counts_before_plan.append(
            connection.execute(
                "SELECT committed_count FROM run WHERE run_id=?", (values["run_id"],)
            ).fetchone()[0]
        )
        if self.fail_at == len(self.calls):
            raise trends.KeywordAcquisitionStopped("fixture_http_429")
        sid = f"KW-{theme}"
        theme_word = f"theme::{theme}"
        request = trends.TrendsRequest(
            sid, values["run_id"], values["settings_version"], theme_word, range="3m"
        )
        payload = {
            "request_id": sid,
            "snapshot_id": sid,
            "run_id": values["run_id"],
            "settings_version": values["settings_version"],
            "source": "google_trends_public_ui",
            "parent_word": theme_word,
            "current_parent": theme_word,
            "refresh_complete": True,
            "language": "ja",
            "geo": "JP",
            "search_type": "web",
            "range": "3m",
            "list_type": "Top",
            "ui_period_label": "過去3か月間",
            "status": "observed",
            "fetched_at": self.case.clock.now_jst().isoformat(),
            "error_code": None,
            "words": [
                {
                    "word_id": f"W{n}",
                    "raw": f"related-{n}::{theme}",
                    "source_rank": n,
                    "popularity_index": None,
                }
                for n in range(1, self.word_count + 1)
            ],
        }
        snapshot = trends.parse_grouped_top_response(payload, request)
        decisions = trends.validate_grouped_key_response(
            snapshot,
            {
                "snapshot_id": sid,
                "query_word": theme_word,
                "decisions": [
                    {
                        "word_id": word.word_id,
                        "decision": "candidate",
                        "group_ref": word.word_id,
                        "reason": "独立したfixture語",
                    }
                    for word in snapshot.words
                ],
            },
        )
        trends.save_grouped_snapshot(
            connection, snapshot, decisions=decisions, seed=values["seed"]
        )
        stored = trends.load_grouped_snapshot(connection, sid, request=request)
        searches = tuple(
            daily.DailySearchPlan(
                task_id=f"related-{index}",
                theme_id=theme,
                query=word.raw,
                parent_refs=(theme,),
                level="related",
                kind="related",
                keyword_snapshot_ref=sid,
                selected_word_ref=word.word_id,
            )
            for index, word in enumerate(stored.selected, 1)
        )
        fallback = daily.DailySearchPlan(
            "theme-fallback",
            theme,
            theme_word,
            (theme,),
            "theme_fallback",
            "theme_fallback",
        )
        return daily.DailyThemePlan(
            theme_id=theme,
            keyword_refs=tuple(word.word_id for word in stored.selected),
            related=searches,
            fallback=fallback,
            snapshot_ref=sid,
        )
