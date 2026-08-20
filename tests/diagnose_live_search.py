import asyncio
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from pesu_agent.adapters.slack_cdp import SlackCdpAdapter

async def main():
    adapter = SlackCdpAdapter()
    async with adapter:
        click_res = await adapter.evaluate_js("""
        (() => {
            const btn = document.querySelector('[data-qa="top_nav_search"]');
            if (btn) {
                btn.click();
                return { clicked: true };
            }
            return { clicked: false };
        })()
        """)
        print("Click result:", click_res)
        await asyncio.sleep(1.0)
        dom_after = await adapter.evaluate_js("""
        (() => {
            const inputs = Array.from(document.querySelectorAll('input, [role="combobox"], [data-qa*="search"], [data-qa*="input"]')).map(el => ({
                tag: el.tagName,
                qa: el.getAttribute('data-qa'),
                aria: el.getAttribute('aria-label'),
                cls: (typeof el.className === 'string') ? el.className : '',
                id: el.id,
                type: el.getAttribute('type'),
                placeholder: el.getAttribute('placeholder')
            }));
            return inputs;
        })()
        """)
        print("Inputs after click:", json.dumps(dom_after, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
