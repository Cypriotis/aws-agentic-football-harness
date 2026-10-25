"""Real local Qwen + Edge acceptance for native 5v5 match observation UI."""
import argparse
import json
from pathlib import Path
import time
import httpx
from playwright.sync_api import sync_playwright
from football_lab.aws_balanced.match import verify_match


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8770")
    parser.add_argument("--browser", default=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    parser.add_argument("--output", default="results/native-match-ui-acceptance")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with httpx.Client(base_url=args.url, trust_env=False, timeout=20) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.browser, headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        page.get_by_label("Native run type").wait_for()
        page.get_by_label("Native run type").select_option("match")
        page.get_by_label("Match duration").select_option("12")
        page.get_by_label("Match decision interval").select_option("2")
        page.get_by_label("Native controlled side").select_option("0")
        page.wait_for_function("document.querySelector('.native-ready')?.innerText.includes('AVAILABLE')")
        with page.expect_response(lambda r: '/api/native/runs' in r.url and r.request.method == 'POST') as started:
            page.get_by_role("button", name="Start 5v5 match", exact=True).click()
        response = started.value
        assert response.status == 202, response.text()
        run_id = response.json()["id"]
        root = f"/api/native/runs/{run_id}"
        print(f"Started native match {run_id}", flush=True)
        page.get_by_role("heading", name="Watch the team. Understand the decisions.").wait_for()
        page.get_by_role("button", name="Pause match", exact=True).click()

        def wait_for(predicate, seconds=150):
            start = time.monotonic()
            while time.monotonic() - start < seconds:
                report = client.get(root).json()
                assert report["status"] != "FAILED", report.get("error")
                if predicate(report):
                    return report
                time.sleep(.25)
            raise AssertionError("Match did not reach expected state")

        wait_for(lambda r: (r.get("match") or {}).get("phase") == "paused")
        page.get_by_role("button", name="Step one interval", exact=True).wait_for(state='visible')
        page.wait_for_function("[...document.querySelectorAll('button')].some(b=>b.textContent==='Step one interval'&&!b.disabled)")
        page.get_by_role("button", name="Step one interval", exact=True).click()
        report = wait_for(lambda r: r["match"]["phase"] == "paused" and r["match"]["completed_rounds"] >= 1)
        first_round = report['match']['completed_rounds']
        assert report["totals"]["decisions"] == first_round * 5
        page.wait_for_function("document.querySelectorAll('.pitch-player').length===10")
        page.wait_for_timeout(1000)
        page.screenshot(path=str(output / "paused.png"), full_page=True)
        page.get_by_role("button", name="Resume", exact=True).click()
        partial = False
        start = time.monotonic()
        while time.monotonic() - start < 180:
            report = client.get(root).json()
            if first_round * 5 < report["totals"]["decisions"] < 30:
                partial = True
            if report["status"] != 'RUNNING':
                break
            time.sleep(.3)
        assert report["status"] == "COMPLETED", report
        assert report["totals"]["decisions"] == 30
        assert report["match"]["state"]["tick"] == 120
        print("Six decision intervals completed using real native Qwen workers.", flush=True)
        page.wait_for_function("document.querySelector('.native-run-state')?.innerText==='COMPLETED'")
        page.wait_for_function("document.querySelector('.replay-labels')?.innerText.includes('120')")
        for pid in range(5):
            page.get_by_test_id(f"match-agent-{pid}").click()
        page.get_by_test_id("match-agent-2").click()
        page.get_by_label("Match inspected decision").select_option('0')
        page.get_by_role("button", name="View input", exact=True).click()
        assert "tick 0" in page.locator('.evidence-flow').inner_text()
        page.get_by_role("button", name="View outcome", exact=True).click()
        assert page.locator('.observed-trail').count() == 1
        page.get_by_role("button", name="Game state", exact=True).click()
        assert '"tick": 0' in page.locator('.match-code pre').inner_text()
        page.get_by_role("button", name="Exact prompts", exact=True).click()
        assert "You are an AI soccer" in page.locator('.match-prompt-grid').inner_text()
        assert page.locator('.match-prompt-grid pre').nth(1).inner_text() != 'No user message captured.'
        page.get_by_role("button", name="Model response", exact=True).click()
        assert page.locator('.match-code pre').inner_text()
        page.get_by_role("button", name="Handler logs", exact=True).click()
        assert "agent invoked" in page.locator('.match-code pre').inner_text()
        page.get_by_role("button", name="Decision & outcome", exact=True).click()
        page.get_by_label("Match replay position").fill('0')
        assert '00:00' in page.locator('.match-score').inner_text()
        page.get_by_role("button", name="Play replay", exact=True).click()
        page.wait_for_timeout(450)
        page.get_by_role("button", name="Pause replay", exact=True).click()
        assert int(page.get_by_label("Match replay position").input_value()) > 0
        page.get_by_role("button", name="Follow latest", exact=True).click()
        page.wait_for_timeout(350)
        with page.expect_download() as exported:
            page.get_by_role("button", name="Export evaluation", exact=True).click()
        exported.value.save_as(output / 'report.json')
        artifact = json.loads((output / 'report.json').read_text())
        assert len(artifact['frames']) == 121
        assert len(artifact['actions']) == 6
        for index in range(6):
            rows = [artifact['roles'][role]['decisions'][index] for role in artifact['roles']]
            assert len({r['observation_sha256'] for r in rows}) == 1
            assert all(r['outcome'] is not None for r in rows)
        assert all(len(v['decisions']) == 6 for v in artifact['roles'].values())
        states = [r['observation'] for r in artifact['roles']['midfielder']['decisions']]
        assert len({json.dumps(s['players']) for s in states}) > 1
        assert artifact['roles']['midfielder']['decisions'][-1]['conversation_messages_before'] > 0
        replay = verify_match(Path('results') / run_id)
        assert replay['verified'], replay
        page.evaluate('scrollTo(0,0)')
        page.screenshot(path=str(output / 'desktop.png'), full_page=True)
        page.get_by_label('Toggle dark theme').click()
        page.wait_for_timeout(350)
        page.screenshot(path=str(output / 'dark.png'), full_page=True)
        page.get_by_label('Toggle dark theme').click()
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_timeout(350)
        page.get_by_role('button', name='Configure evaluation', exact=True).click()
        assert page.get_by_label('Match duration').is_visible()
        page.get_by_role('button', name='Close setup', exact=True).click()
        page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'), 'Mobile horizontal overflow'
        page.reload()
        page.get_by_role('heading', name='Watch the team. Understand the decisions.').wait_for()
        page.wait_for_function("document.querySelector('.replay-labels')?.innerText.includes('120')")
        assert not errors, errors
        result = {'status': 'PASS', 'run_id': run_id, 'real_qwen_decisions': 30,
                  'evolving_state': True, 'pause_step_resume': True, 'partial_results': partial,
                  'replay_without_model_calls': replay, 'observation_action_outcome': True,
                  'all_five_same_round_input_hashes': True, 'native_history_retained': True,
                  'exact_prompt_response_logs': True, 'report_export': True,
                  'mobile_horizontal_overflow': False, 'browser_errors': errors,
                  'totals': report['totals']}
        (output / 'browser-check.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2), flush=True)
        browser.close()


if __name__ == '__main__':
    main()
