"""Observed Workday control shapes, with synthetic data only."""

import asyncio
from types import SimpleNamespace

from playwright.async_api import async_playwright

from src.pilot.engine import inspect, model
from src.pilot.facts import resolve
from src.pilot.workday import allowed_json_save


def test_workday_dropdown_chips_and_phone_extension():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content("""<form>
              <label for="prefix">Prefix*</label>
              <button id="prefix" name="title" type="button" aria-haspopup="listbox" aria-label="Prefix Select One Required" value="">Select One</button>
              <div data-automation-id="multiselectInputContainer">
                <label for="code">Country Phone Code*</label><input id="code" aria-required="true">
                <div data-automation-id="selectedItem"><p data-automation-id="promptOption">United Kingdom (+44)</p></div>
              </div>
              <label for="extension">Phone Extension</label><input id="extension">
              <fieldset><legend>Previously employed?*</legend><label><input id="yes" type="radio" name="previous" value="yes">Yes</label></fieldset>
            </form>""")
            fields = {f["field_id"]: f for f in await inspect(page)}
            assert fields["prefix"]["role"] == "combobox"
            assert fields["prefix"]["required"]
            assert not model(fields["prefix"]).has_observed_value
            assert fields["code"]["observed_value"] == "United Kingdom (+44)"
            assert model(fields["code"]).has_observed_value
            assert fields["yes"]["required"]
            assert fields["yes"]["option_label"] == "Yes"
            assert not model(fields["yes"]).has_observed_value
            assert (
                resolve(
                    model(fields["extension"]),
                    {"phone": "07123456789"},
                    SimpleNamespace(country="UK"),
                )
                == ""
            )
            await browser.close()

    asyncio.run(scenario())


def test_workday_navigation_writes_are_application_scoped_and_non_final():
    authority = {
        "navigation": {
            "origin": "https://tenant.myworkdayjobs.com",
            "tenant": "tenant",
            "application_id": "application123",
        }
    }

    def request(path, body='{"answers":[{"value":"Synthetic"}]}', method="POST"):
        return SimpleNamespace(
            method=method,
            url="https://tenant.myworkdayjobs.com" + path,
            headers={"content-type": "application/json;charset=UTF-8"},
            post_data=body,
        )

    allowed = request(
        "/wday/calypso/cxs/jobapplication/tenant/"
        "jobapplication/application123/questionnaire"
    )
    assert allowed_json_save(allowed, authority)
    assert not allowed_json_save(
        request(
            "/wday/calypso/cxs/jobapplication/tenant/"
            "jobapplication/different123/questionnaire"
        ),
        authority,
    )
    assert not allowed_json_save(
        request(
            "/wday/calypso/cxs/jobapplication/tenant/"
            "jobapplication/application123/submit",
            '{"submit":true}',
        ),
        authority,
    )
    assert not allowed_json_save(allowed, {"navigation": None})


def test_workday_navigation_binds_first_observed_draft_identity():
    authority = {
        "navigation": {
            "origin": "https://tenant.myworkdayjobs.com",
            "tenant": "tenant",
            "application_id": "",
        }
    }

    def request(application_id, operation="questionnaire"):
        return SimpleNamespace(
            method="POST",
            url=(
                "https://tenant.myworkdayjobs.com/wday/calypso/cxs/"
                f"jobapplication/tenant/jobapplication/{application_id}/{operation}"
            ),
            headers={"content-type": "application/json"},
            post_data='{"answers":[{"value":"Synthetic"}]}',
        )

    assert allowed_json_save(request("draft_application_123"), authority)
    assert authority["workday_application_id"] == "draft_application_123"
    assert not allowed_json_save(request("different_application_456"), authority)
    assert not allowed_json_save(
        request("draft_application_123", "withdraw"), authority
    )


def test_workday_source_save_can_bind_the_current_draft():
    payload = {"source": {"id": "source-id", "descriptor": "Bright Network"}}
    authority = {
        "json": {
            "workday_source": True,
            "origin": "https://tenant.myworkdayjobs.com",
            "tenant": "tenant",
            "application_id": "",
            "payload": payload,
        }
    }
    request = SimpleNamespace(
        method="POST",
        url=(
            "https://tenant.myworkdayjobs.com/wday/calypso/cxs/jobapplication/"
            "tenant/jobapplication/draft_application_123/source"
        ),
        headers={"content-type": "application/json"},
        post_data='{"source":{"id":"source-id","descriptor":"Bright Network"}}',
    )
    assert allowed_json_save(request, authority)
    assert authority["workday_application_id"] == "draft_application_123"


def test_workday_page_footer_save_is_non_final_navigation():
    async def scenario():
        from src.pilot.adapters import choose

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(
                '<main><button type="button" data-automation-id="pageFooterNextButton">'
                "Save and Continue</button></main>"
            )
            action = await choose(
                "https://tenant.myworkdayjobs.com/job/REQ-123"
            ).next_action(page)
            assert action is not None
            assert await action.inner_text() == "Save and Continue"
            await browser.close()

    asyncio.run(scenario())
