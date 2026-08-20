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

async def diagnose_dom_state_and_threads():
    manager = SlackLifecycleManager()
    status = manager.ensure_agent_ready(allow_restart=True)
    await asyncio.sleep(5.0)

    async with SlackCdpAdapter(lifecycle_manager=manager) as cdp:
        # 1. Inspect current view snapshot
        js_snapshot = """
        (() => {
            const sc = document.querySelector('.c-virtual_list__scroll_container') ||
                       document.querySelector('.p-workspace__primary_view_body');
            
            const titleElem = document.querySelector('[data-qa="channel_name"]') ||
                              document.querySelector('.p-ia4_top_nav__title');
            const convName = titleElem ? titleElem.innerText.trim().replace(/^[#@]/, '') : '';

            const url = window.location.href;
            const match = url.match(/\\/client\\/([A-Z0-9]+)\\/([A-Z0-9]+)/);
            const chanId = match ? match[2] : null;

            const items = Array.from(document.querySelectorAll(
                '.c-virtual_list__item, [data-qa="virtual-list-item"], [data-qa="message_container"], [role="listitem"]'
            ));

            const visibleItems = [];
            items.forEach((item, idx) => {
                const textElem = item.querySelector('[data-qa="message_content"], .c-message_kit__blocks, .p-rich_text_section, .c-message__body');
                const text = textElem ? textElem.innerText.trim() : (item.innerText ? item.innerText.trim() : "");
                const senderElem = item.querySelector('[data-qa="message_sender_name"], .c-message__sender_button, button[data-message-sender]');
                const sender = senderElem ? senderElem.innerText.trim() : null;
                const timeElem = item.querySelector('[data-qa="message_timestamp"], .c-timestamp');
                const timeStr = timeElem ? timeElem.innerText.trim() : null;
                const linkElem = item.querySelector('a[href*="/archives/"][href*="/p"]') || item.querySelector('a.c-timestamp');
                const href = linkElem ? linkElem.getAttribute('href') : '';
                const itemKey = item.getAttribute('data-item-key') || item.id || '';

                // Thread check
                const replyElem = item.querySelector('[data-qa="reply_count"], .c-message__reply_count, [data-qa="message_reply_count"]');
                const replyText = replyElem ? replyElem.innerText.trim() : '';
                const replyMatch = replyText.match(/(\\d+)/);
                const replyCount = replyMatch ? parseInt(replyMatch[1]) : 0;
                const hasThread = replyCount > 0 || !!item.querySelector('.c-message__reply_bar');

                if (text && !text.startsWith("새 항목") && text !== "날짜로 이동") {
                    visibleItems.push({
                        idx: idx,
                        sender: sender,
                        time: timeStr,
                        text: text.substring(0, 60),
                        href: href,
                        itemKey: itemKey,
                        has_thread: hasThread,
                        reply_count: replyCount
                    });
                }
            });

            return {
                url: url,
                channel_id: chanId,
                conversation_name: convName,
                scroll_top: sc ? sc.scrollTop : 0,
                scroll_height: sc ? sc.scrollHeight : 0,
                client_height: sc ? sc.clientHeight : 0,
                total_visible_items: visibleItems.length,
                first_item: visibleItems.length > 0 ? visibleItems[0] : null,
                last_item: visibleItems.length > 0 ? visibleItems[visibleItems.length - 1] : null,
                items_with_threads: visibleItems.filter(v => v.has_thread)
            };
        })()
        """
        snap = await cdp.evaluate_js(js_snapshot)
        print("=== CURRENT VIEW SNAPSHOT ===")
        print("URL:", snap.get("url"))
        print("Channel ID:", snap.get("channel_id"))
        print("Conversation Name:", snap.get("conversation_name"))
        print(f"ScrollTop: {snap.get('scroll_top')} / {snap.get('scroll_height')} (ClientHeight: {snap.get('client_height')})")
        print(f"Visible items: {snap.get('total_visible_items')}")
        print("First item:", snap.get("first_item"))
        print("Last item:", snap.get("last_item"))
        print(f"Items with threads: {len(snap.get('items_with_threads', []))}")
        for th in snap.get("items_with_threads", []):
            print("  Thread Item:", th)

        # 2. Test Scrolling within current container
        print("\n--- Testing Scroll Manipulation ---")
        js_scroll_test = """
        (() => {
            const sc = document.querySelector('.c-virtual_list__scroll_container') ||
                       document.querySelector('.p-workspace__primary_view_body');
            if (!sc) return { ok: false, error: 'no_scroll_container' };
            const before = sc.scrollTop;
            sc.scrollTop = 500;
            return { ok: true, before: before, after: sc.scrollTop };
        })()
        """
        scroll_res = await cdp.evaluate_js(js_scroll_test)
        print("Scroll test result:", scroll_res)

if __name__ == "__main__":
    asyncio.run(diagnose_dom_state_and_threads())
