import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from pesu_agent.adapters.slack_cdp import SlackCdpAdapter

async def test_search_open_methods():
    async with SlackCdpAdapter() as cdp:
        # Method 1: Get bounding rect of [data-qa="top_nav_search"] and dispatch CDP mouse click
        rect = await cdp.evaluate_js("""
        (() => {
            const btn = document.querySelector('[data-qa="top_nav_search"]');
            if (!btn) return null;
            const r = btn.getBoundingClientRect();
            return { x: r.left + r.width / 2, y: r.top + r.height / 2, width: r.width, height: r.height };
        })()
        """)
        print("Search Button Rect:", rect)

        if rect:
            x, y = int(rect["x"]), int(rect["y"])
            # Dispatch CDP mouse click
            await cdp.dispatch_mouse_event(type_="mousePressed", x=x, y=y, button="left", click_count=1)
            await cdp.dispatch_mouse_event(type_="mouseReleased", x=x, y=y, button="left", click_count=1)
            print(f"Dispatched CDP mouse click at ({x}, {y})")

        await asyncio.sleep(1.0)

        # Type query into search input
        await asyncio.sleep(0.5)
        inject_res = await cdp.evaluate_js("""
        (() => {
            const editor = document.querySelector('[data-qa="search_input_box"] [contenteditable="true"]') ||
                           document.querySelector('[data-qa="focusable_search_input"] [contenteditable="true"]') ||
                           document.querySelector('.c-search__input_box .ql-editor') ||
                           document.querySelector('[data-qa="top_nav_search__input"]');
            if (editor) {
                editor.focus();
                // Set innerText or Quill content
                editor.innerHTML = '<p>수임</p>';
                editor.dispatchEvent(new Event('input', { bubbles: true }));
                return { injected: true, tag: editor.tagName, text: editor.innerText };
            }
            return { injected: false };
        })()
        """)
        print("Inject result:", inject_res)

        # Dispatch Enter key
        await cdp.dispatch_key_event(type_="rawKeyDown", key="Enter", code="Enter", windows_virtual_key_code=13)
        await cdp.dispatch_key_event(type_="keyUp", key="Enter", code="Enter", windows_virtual_key_code=13)
        print("Dispatched Enter key")

        # Wait 3s and check search results in DOM
        await asyncio.sleep(3.0)
        results_check = await cdp.evaluate_js("""
        (() => {
            const items = Array.from(document.querySelectorAll('[data-qa="search_result_item"], [data-qa="search_result_message"], .c-search_result_item, .c-search_result_message, .c-message_kit__message'));
            return {
                url: window.location.href,
                results_count: items.length,
                snippets: items.slice(0, 5).map(i => (i.innerText || '').slice(0, 50))
            };
        })()
        """)
        print("Results after search:", json.dumps(results_check, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(test_search_open_methods())
