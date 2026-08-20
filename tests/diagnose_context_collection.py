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

async def test_context_collection():
    manager = SlackLifecycleManager()
    status = manager.ensure_agent_ready(allow_restart=False)
    print(f"Status: {status.status.value}")

    async with SlackCdpAdapter(lifecycle_manager=manager) as cdp:
        # 1. Record original state before investigation
        js_save_state = """
        (() => {
            const sc = document.querySelector('.c-virtual_list__scroll_container');
            const titleElem = document.querySelector('[data-qa="channel_name"]') ||
                              document.querySelector('.p-ia4_top_nav__title');
            return {
                original_url: window.location.href,
                original_conversation: titleElem ? titleElem.innerText.trim() : document.title,
                original_scroll_top: sc ? sc.scrollTop : 0
            };
        })()
        """
        orig_state = await cdp.evaluate_js(js_save_state)
        print("Original State:", orig_state)

        # 2. Navigate to permalink
        permalink = "https://heumlabs.slack.com/archives/C3R53LYV8/p1787028341517949"
        # Extract channel_id and ts
        # C3R53LYV8, p1787028341517949 -> 1787028341.517949
        m = re.search(r"/archives/([A-Z0-9]+)/p(\d+)", permalink)
        chan_id = m.group(1) if m else "C3R53LYV8"
        raw_ts = m.group(2) if m else "1787028341517949"
        ts_float_str = f"{raw_ts[:-6]}.{raw_ts[-6:]}" if len(raw_ts) > 6 else raw_ts
        print(f"Navigating to Channel: {chan_id}, Target TS: {ts_float_str}")

        # In Slack webapp URL: https://app.slack.com/client/T32QA15GC/{chan_id}
        # or window.location.href = permalink
        js_nav = f"""
        (() => {{
            window.location.href = {json.dumps(permalink)};
            return {{ navigating: true, to: {json.dumps(permalink)} }};
        }})()
        """
        nav_res = await cdp.evaluate_js(js_nav)
        print("Navigation result:", nav_res)

        # Wait 4s for channel conversation hydration & target highlight
        await asyncio.sleep(4.0)

        # 3. Locate Target Message & Extract Context
        js_extract_context = f"""
        (() => {{
            const items = Array.from(document.querySelectorAll(
                '.c-virtual_list__item, [data-qa="virtual-list-item"], [data-qa="message_container"], [role="listitem"]'
            ));

            const parsedMsgs = [];
            let targetIdx = -1;

            items.forEach((item, idx) => {{
                const senderElem = item.querySelector('[data-qa="message_sender_name"], .c-message__sender_button, button[data-message-sender]');
                const sender = senderElem ? senderElem.innerText.trim().replace(/\\n앱$/, '') : null;

                const timeElem = item.querySelector('[data-qa="message_timestamp"], .c-timestamp, a.c-timestamp');
                const timeStr = timeElem ? timeElem.innerText.trim() : null;

                const textElem = item.querySelector('[data-qa="message_content"], .c-message_kit__blocks, .p-rich_text_section, .c-message__body');
                const text = textElem ? textElem.innerText.trim() : (item.innerText ? item.innerText.trim() : "");

                const linkElem = item.querySelector('a[href*="/archives/"][href*="/p"]') || item.querySelector('a.c-timestamp');
                const href = linkElem ? linkElem.getAttribute('href') : '';

                const itemKey = item.getAttribute('data-item-key') || item.id || '';

                // Check thread
                const replyElem = item.querySelector('[data-qa="reply_count"], .c-message__reply_count, [data-qa="message_reply_count"]');
                const replyText = replyElem ? replyElem.innerText.trim() : '';
                const replyMatch = replyText.match(/(\\d+)/);
                const replyCount = replyMatch ? parseInt(replyMatch[1]) : 0;
                const hasThread = replyCount > 0 || !!item.querySelector('.c-message__reply_bar');

                // Target match heuristic: permalink match or text match
                const isTarget = href.includes({json.dumps(raw_ts)}) ||
                                 itemKey.includes({json.dumps(raw_ts)}) ||
                                 (text && text.includes('신영선') && text.includes('수임이 완료되어'));

                if (text && !text.startsWith("새 항목") && text !== "날짜로 이동") {{
                    const msgObj = {{
                        idx: parsedMsgs.length,
                        sender: sender,
                        time: timeStr,
                        text: text.replace(/\\n/g, ' ').substring(0, 150),
                        href: href,
                        itemKey: itemKey,
                        is_target: isTarget,
                        has_thread: hasThread,
                        reply_count: replyCount
                    }};
                    if (isTarget && targetIdx === -1) {{
                        targetIdx = parsedMsgs.length;
                    }}
                    parsedMsgs.push(msgObj);
                }}
            }});

            const beforeMsgs = targetIdx >= 0 ? parsedMsgs.slice(Math.max(0, targetIdx - 20), targetIdx) : [];
            const targetMsg = targetIdx >= 0 ? parsedMsgs[targetIdx] : null;
            const afterMsgs = targetIdx >= 0 ? parsedMsgs.slice(targetIdx + 1, targetIdx + 21) : [];

            return {{
                url_now: window.location.href,
                total_messages_in_dom: parsedMsgs.length,
                target_found: targetIdx >= 0,
                target_index: targetIdx,
                target_message: targetMsg,
                before_count: beforeMsgs.length,
                before_sample: beforeMsgs.slice(-3),
                after_count: afterMsgs.length,
                after_sample: afterMsgs.slice(0, 3)
            }};
        }})()
        """
        ctx_res = await cdp.evaluate_js(js_extract_context)
        print("\n=== Context Extraction Results ===")
        print(f"Target found: {ctx_res.get('target_found')} at index {ctx_res.get('target_index')}")
        print("Target message:", ctx_res.get("target_message"))
        print(f"Before messages count: {ctx_res.get('before_count')}")
        for bm in ctx_res.get("before_sample", []):
            print("  [Before]", bm.get("sender"), bm.get("time"), bm.get("text")[:50])
        print(f"After messages count: {ctx_res.get('after_count')}")
        for am in ctx_res.get("after_sample", []):
            print("  [After]", am.get("sender"), am.get("time"), am.get("text")[:50])

        # 4. State Restoration
        print("\n--- Restoring Original State ---")
        orig_url = orig_state.get("original_url")
        if orig_url and orig_url != "about:blank":
            js_restore = f"""
            (() => {{
                window.location.href = {json.dumps(orig_url)};
                return {{ restoring: true }};
            }})()
            """
            await cdp.evaluate_js(js_restore)
            await asyncio.sleep(3.0)
            now_url = await cdp.evaluate_js("window.location.href")
            print(f"Restored to URL: {now_url} (Matches original? {now_url == orig_url})")

if __name__ == "__main__":
    asyncio.run(test_context_collection())
