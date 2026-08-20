import asyncio
import json
import logging
import sys

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, "src")
from pesu_agent.lifecycle.slack_lifecycle import SlackLifecycleManager
from pesu_agent.adapters.slack_cdp import SlackCdpAdapter

async def check_dom_inputs():
    manager = SlackLifecycleManager()
    status = manager.ensure_agent_ready(allow_restart=False)

    async with SlackCdpAdapter(lifecycle_manager=manager) as cdp:
        # Check current inputs
        js_list = """
        (() => {
            return Array.from(document.querySelectorAll('input, button, [role="button"], [data-qa]')).map(el => ({
                tag: el.tagName.toLowerCase(),
                qa: el.getAttribute('data-qa'),
                placeholder: el.getAttribute('placeholder'),
                aria: el.getAttribute('aria-label'),
                cls: el.className,
                type: el.getAttribute('type'),
                visible: el.offsetParent !== null,
                text: el.innerText ? el.innerText.trim().substring(0, 30) : ''
            })).filter(x => x.qa || x.placeholder || x.aria || x.tag === 'input');
        })()
        """
        all_items = await cdp.evaluate_js(js_list)
        print(f"Total elements found: {len(all_items)}")
        for it in all_items[:25]:
            print(" ", it)

        # Click top nav search button
        js_click = """
        (() => {
            const btn = document.querySelector('[data-qa="top_nav_search"]') ||
                        document.querySelector('button[aria-label*="검색"]') ||
                        document.querySelector('.p-top_nav__search');
            if (btn) {
                btn.click();
                return { clicked: true, qa: btn.getAttribute('data-qa') };
            }
            return { clicked: false };
        })()
        """
        clk_res = await cdp.evaluate_js(js_click)
        print("\nClick result:", clk_res)

        await asyncio.sleep(1.0)

        # Re-check inputs after click
        after_items = await cdp.evaluate_js(js_list)
        print(f"\nAfter Click Elements: {len(after_items)}")
        for it in after_items:
            if it.get('tag') == 'input' or 'search' in str(it.get('qa', '')).lower() or '검색' in str(it.get('aria', '')):
                print("  [MATCH]", it)

asyncio.run(check_dom_inputs())
