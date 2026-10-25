"""Real-browser native baseline acceptance; explicitly invokes local Qwen via the UI."""
import argparse
import json
from pathlib import Path
import time
import httpx
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--browser", default=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    parser.add_argument("--output", default="results/baseline-only-ui-acceptance")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with httpx.Client(base_url=args.url, trust_env=False, timeout=10) as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser, headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1100}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        page.get_by_label('Native run type').wait_for()
        page.get_by_label('Native run type').select_option('snapshot')
        page.wait_for_function("document.querySelector('.native-ready')?.innerText.includes('AVAILABLE')")
        assert page.get_by_label('Native run type').input_value() == 'snapshot'
        assert page.locator('.metadata-sidebar').count() == 0
        assert page.get_by_role('link', name='LAB LIVE', exact=True).count() == 0
        print('Native workspace loaded with five agent cards.', flush=True)
        page.get_by_label('Native input observation').select_option('custom')
        page.get_by_label('Native custom game state').fill('[]')
        page.get_by_role('button', name='Run five agents', exact=True).click()
        page.get_by_role('alert').filter(has_text='raw game state').wait_for()
        page.get_by_label('Native input observation').select_option('sample')
        page.get_by_label('Native local model').select_option('qwen2.5:14b')
        page.get_by_label('Native repetitions').select_option('2')
        page.get_by_label('Native controlled side').select_option('1')
        with page.expect_response(lambda response: '/api/native/runs' in response.url and response.request.method == 'POST') as launched:
            page.get_by_role('button', name='Run five agents', exact=True).click()
        response = launched.value
        assert response.status == 202, response.text()
        run_id = response.json()['id']
        page.wait_for_function("document.querySelector('.native-run-state')?.innerText==='RUNNING'")
        page.locator('.link-status').click()
        assert page.locator('.link-status').inner_text() == 'Refresh paused'
        start = time.monotonic()
        partial = False
        while time.monotonic()-start < 150:
            report = client.get(f'/api/native/runs/{run_id}').json()
            if 0 < report['totals']['decisions'] < 10:
                partial = True
            if report['status'] != 'RUNNING':
                break
            time.sleep(.5)
        assert report['status'] == 'COMPLETED', report
        assert report['totals']['decisions'] == 10
        assert page.locator('.native-agent').count() == 5
        print(f'Native execution {run_id} completed while browser refresh was paused.', flush=True)
        page.locator('.link-status').click()
        page.wait_for_function("document.querySelector('.native-run-state')?.innerText==='COMPLETED'")
        page.wait_for_function("document.querySelectorAll('.native-decision-row').length===10")
        assert page.locator('.pitch-player').count() == 10
        assert 'NO MATCH SIMULATION' in page.locator('.pitch-caption').inner_text()
        for i in range(5):
            page.get_by_test_id(f'native-agent-{i}').click()
            assert f'0{i}' in page.locator('.native-inspector h3').inner_text()
        page.get_by_label('Native inspected decision').select_option('1')
        assert '2 → 4 messages' in page.locator('.native-inspector-spec').inner_text()
        page.get_by_role('button', name='Prompt', exact=True).click()
        assert 'You are an AI soccer' in page.locator('.native-inspector pre').inner_text()
        page.get_by_role('button', name='Response', exact=True).click()
        assert page.locator('.native-inspector pre').inner_text()
        page.get_by_role('button', name='Logs', exact=True).click()
        assert 'agent invoked' in page.locator('.native-inspector pre').inner_text()
        page.get_by_role('button', name='Actions', exact=True).click()
        assert 'commandType' in page.locator('.native-inspector pre').inner_text()
        with page.expect_download() as download:
            page.get_by_role('button', name='Export evaluation', exact=True).click()
        download.value.save_as(output / 'exported-report.json')
        assert json.loads((output / 'exported-report.json').read_text())['totals']['decisions'] == 10
        page.get_by_label('Native evaluation history').select_option(run_id)
        page.reload()
        page.wait_for_function("document.querySelectorAll('.native-decision-row').length===10")
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(output / 'desktop.png'), full_page=True)
        page.get_by_label('Toggle dark theme').click()
        page.wait_for_timeout(350)
        page.screenshot(path=str(output / 'dark.png'), full_page=True)
        page.get_by_label('Toggle dark theme').click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(350)
        page.get_by_role('button', name='Configure evaluation', exact=True).click()
        assert page.get_by_label('Native local model').is_visible()
        page.get_by_role('button', name='Close setup', exact=True).click()
        page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        overflow = page.evaluate('document.documentElement.scrollWidth>window.innerWidth')
        assert not overflow, 'Mobile horizontal overflow'
        page.set_viewport_size({"width": 1600, "height": 1100})
        assert page.locator('.native-latency-row').count() == 5
        assert page.locator('.native-action-summary').is_visible()
        for route in ['/live', '/archive', '/compare', '/evaluate', '/review']:
            page.goto(args.url + route)
            page.get_by_role('heading', name='Understand every decision.').wait_for()
            page.wait_for_url('**/native')
            assert page.get_by_role('link', name='LAB LIVE', exact=True).count() == 0
        assert not errors, errors
        result = {"status": "PASS", "run_id": run_id, "decisions": 10,
                  "five_agent_cards": True, "original_prompt_and_response_inspection": True,
                  "incremental_results_observed": partial, "browser_pause_did_not_stop_execution": True,
                  "refresh_and_history_recovery": True, "report_download": True,
                  "custom_json_validation": True, "mobile_horizontal_overflow": overflow,
                  "baseline_only_routes": True, "mobile_setup_toggle": True, "per_agent_latency_chart": True, "page_errors": errors,
                  "totals": report['totals']}
        (output / 'browser-check.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(result, indent=2), flush=True)
        browser.close()


if __name__ == '__main__':
    main()
