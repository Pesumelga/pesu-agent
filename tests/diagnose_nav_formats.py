import asyncio
import json
import logging
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, "src")
from pesu_agent.lifecycle.slack_lifecycle import SlackLifecycleManager
from pesu_agent.adapters.slack_cdp import SlackCdpAdapter

async def test_nav_formats():
    manager = SlackLifecycleManager()
    status = manager.ensure_agent_ready(allow_restart=False)

    async with SlackCdpAdapter(lifecycle_manager=manager) as cdp:
        # Check current URL and team ID
        curr_url = await cdp.evaluate_js("window.location.href")
        print("Current URL:", curr_url)
        # Extract team ID T32QA15GC
        team_match = re.search(r"/client/([A-Z0-9]+)", curr_url)
        team_id = team_match.group(1) if team_match else "T32QA15GC"
        print("Team ID:", team_id)

        chan_id = "C3R53LYV8"
        raw_ts = "1787028341517949"

        # Format 1: Direct in-app client URL
        client_url = f"https://app.slack.com/client/{team_id}/{chan_id}?p=p{raw_ts}"
        print(f"\nTesting Navigation to client_url: {client_url}")
        
        js_nav = f"""
        (() => {{
            window.location.href = {json.dumps(client_url)};
            return {{ ok: true }};
        }})()
        """
        await cdp.evaluate_js(js_nav)
        await asyncio.sleep(4.0)

        # Check DOM in channel
        js_check = """
        (() => {
            const titleElem = document.querySelector('[data-qa="channel_name"]') ||
                              document.querySelector('.p-ia4_top_nav__title');
            const items = Array.from(document.querySelectorAll(
                '.c-virtual_list__item, [data-qa="virtual-list-item"], [data-qa="message_container"], [role="listitem"]'
            ));
            return {
                url: window.location.href,
                title: titleElem ? titleElem.innerText.trim() : document.title,
                items_count: items.length,
                sample_texts: items.slice(0, 5).map(it => it.innerText ? it.innerText.trim().substring(0, 50) : '')
            };
        })()
        """
        res = await cdp.evaluate_js(js_check)
        print("Channel View Info:")
        print("URL:", res.get("url"))
        print("Title:", res.get("title"))
        print("Items count:", res.get("items_count"))
        for st in res.get("sample_texts", []):
            print("  Item:", st)

asyncio.run(test_nav_formats())
