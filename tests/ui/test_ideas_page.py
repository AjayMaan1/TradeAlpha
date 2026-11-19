"""The Ideas page (Phase 3.5): inbox with states, version detail with the state reason and lifecycle links, the
trade-event stream, archive - under the CSP without console errors."""
from __future__ import annotations

from datetime import date

import pytest

from tests.support.lab.bt_synth import seed_universe
from tests.ui.conftest import assert_clean

expect = pytest.importorskip("playwright.sync_api", reason="UI tests need playwright + Chromium").expect


def test_ideas_page_inbox_detail_and_archive(live_app, page):
    from tests.integration.ideas.test_ideas import SECRET, _wait_jobs, make_spec
    base, sv, _ = live_app(now="2025-03-28T16:00:00+05:30")
    seed_universe(sv, ["AAA", "BBB", "CCC"], date(2024, 10, 1), date(2025, 3, 28))
    sv.backtests.update_config({"workers": 1, "holdout_months": 0, "holdout_from": "2024-01-01"})
    sv.runner.start()
    sv.ideas.set_secret("ideas_intake_secret", SECRET, "test")
    sv.ideas.update_config({"auto": False})                      # validation only: the page shows the states
    sv.ideas.submit(make_spec(name="good-one", source={"generator": "genie", "data_end": "2023-12-29"}))
    sv.ideas.submit(make_spec(name="peeker", source={"generator": "genie", "data_end": "2024-06-01"}))
    _wait_jobs(sv)
    page.goto(base + "/ideas")
    expect(page.locator("#ideas-table tbody tr")).to_have_count(2)
    expect(page.locator("#ideas-chips")).to_contain_text("evaluating")
    row = page.locator("#ideas-table tr", has_text="peeker")
    row.click()
    expect(page.locator("#idea-detail")).to_contain_text("embargo")
    expect(page.locator("#events-table tbody tr").first).to_be_visible()
    expect(page.locator("#trade-events-card")).to_contain_text("idea.state")
    page.on("dialog", lambda d: d.accept())
    page.click("#idea-archive")
    expect(page.locator("#idea-archive")).to_be_hidden()                 # archived: the button goes away
    with sv.meta.session() as s:
        from sqlalchemy import select

        from tradealpha.platform.db.models import Idea
        assert s.scalar(select(Idea).where(Idea.name == "peeker")).status == "archived"
    assert_clean(page)
