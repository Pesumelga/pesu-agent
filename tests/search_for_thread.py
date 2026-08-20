import asyncio
import json
import logging
import sys

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, "src")
from pesu_agent.lifecycle.slack_lifecycle import SlackLifecycleManager
from pesu_agent.search.slack_search import SlackSearch
from pesu_agent.adapters.slack_cdp import SlackCdpAdapter

async def search_for_thread_message():
    manager = SlackLifecycleManager()
    status = manager.ensure_agent_ready(allow_restart=False)

    searcher = SlackSearch(lifecycle_manager=manager)
    queries = ["배포", "수정", "오류", "이슈", "회의", "테스트", "공유", "질문"]

    for q in queries:
        print(f"\nSearching for: {q!r}")
        session = await searcher.search(query=q, max_scrolls=0)
        print(f"Results for {q}: {session.result_count}")
        for r in session.results[:6]:
            print(f"  [{r.channel_name}] {r.author} | {r.timestamp_raw} | {r.result_url}")
            print(f"    Text: {r.text[:60]!r}")
            # Navigate and check if has thread
            async with SlackCdpAdapter(lifecycle_manager=manager) as cdp:
                import re
                m = re.search(r"/archives/([A-Z0-9]+)/p(\d+)", r.result_url)
                if m:
                    chan_id, raw_ts = m.group(1), m.group(2)
                    nav_url = f"https://app.slack.com/client/T32QA15GC/{chan_id}?p=p{raw_ts}"
                    await cdp.evaluate_js(f"window.location.href = '{nav_url}'")
                    await asyncio.sleep(2.5)

                    js_check = """
                    (() => {
                        const items = Array.from(document.querySelectorAll('.c-virtual_list__item, [data-qa="virtual-list-item"]'));
                        for (const item of items) {
                            const replyElem = item.querySelector('[data-qa="reply_count"], .c-message__reply_count, [data-qa="message_reply_count"], [aria-label*="댓글"], [aria-label*="replies"], [aria-label*="reply"]');
                            if (replyElem) {
                                return {
                                    has_thread: true,
                                    reply_text: replyElem.innerText.trim() || replyElem.getAttribute('aria-label'),
                                    author: item.querySelector('[data-qa="message_sender_name"]')?.innerText.trim(),
                                    text: item.innerText.substring(0, 80)
                                };
                            }
                        }
                        return { has_thread: false };
                    })()
                    """
                    th = await cdp.evaluate_js(js_check)
                    if th and th.get("has_thread"):
                        print(f"\n>>> [POSITIVE THREAD FOUND!] <<<")
                        print(f"Query: {q}")
                        print(f"Target: {r.result_url}")
                        print(f"Thread Details: {th}")
                        return r, th
        await asyncio.sleep(1.0)

if __name__ == "__main__":
    asyncio.run(search_for_thread_message())
