import asyncio
import json
import logging
import sys

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, "src")
from pesu_agent.lifecycle.slack_lifecycle import SlackLifecycleManager
from pesu_agent.adapters.slack_cdp import SlackCdpAdapter

async def list_sidebar_channels():
    manager = SlackLifecycleManager()
    status = manager.ensure_agent_ready(allow_restart=True)
    await asyncio.sleep(4.0)

    async with SlackCdpAdapter(lifecycle_manager=manager) as cdp:
        js_channels = """
        (() => {
            const chanElems = Array.from(document.querySelectorAll(
                '[data-qa="channel_sidebar_name_general"], [data-qa-channel-sidebar-channel-type], .p-channel_sidebar__channel, a[href*="/client/"]'
            ));
            
            const channels = [];
            for (const el of chanElems) {
                const href = el.getAttribute('href') || (el.querySelector('a') ? el.querySelector('a').getAttribute('href') : '');
                const text = el.innerText.trim();
                if (href && text) {
                    const match = href.match(/\\/client\\/([A-Z0-9]+)\\/([A-Z0-9]+)/);
                    if (match) {
                        channels.push({
                            name: text,
                            channel_id: match[2],
                            href: href
                        });
                    }
                }
            }
            return channels;
        })()
        """
        chans = await cdp.evaluate_js(js_channels)
        print(f"Found {len(chans)} sidebar items:")
        for c in chans[:20]:
            print(f"  Channel: {c['name']} -> ID: {c['channel_id']}")

if __name__ == "__main__":
    asyncio.run(list_sidebar_channels())
