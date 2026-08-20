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

async def test_wait_channel_dom():
    manager = SlackLifecycleManager()
    status = manager.ensure_agent_ready(allow_restart=False)

    async with SlackCdpAdapter(lifecycle_manager=manager) as cdp:
        team_id = "T32QA15GC"
        chan_id = "C3R53LYV8"
        raw_ts = "1787028341517949"
        client_url = f"https://app.slack.com/client/{team_id}/{chan_id}?p=p{raw_ts}"

        print(f"Navigating to: {client_url}")
        await cdp.evaluate_js(f"window.location.href = {json.dumps(client_url)};")

        # Poll for message items
        for poll in range(12):
            await asyncio.sleep(0.5)
            js_check = f"""
            (() => {{
                const items = Array.from(document.querySelectorAll(
                    '.c-virtual_list__item, [data-qa="virtual-list-item"], [data-qa="message_container"], [role="listitem"]'
                ));

                const parsed = [];
                let targetFound = false;

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

                    const isTarget = href.includes({json.dumps(raw_ts)}) ||
                                     itemKey.includes({json.dumps(raw_ts)}) ||
                                     (text && text.includes('신영선') && text.includes('수임이 완료되어'));

                    if (text && !text.startsWith("새 항목") && text !== "날짜로 이동") {{
                        if (isTarget) targetFound = true;
                        parsed.push({{
                            idx: parsed.length,
                            sender: sender,
                            time: timeStr,
                            text: text.replace(/\\n/g, ' ').substring(0, 100),
                            is_target: isTarget,
                            has_thread: hasThread,
                            reply_count: replyCount
                        }});
                    }}
                }});

                return {{
                    poll: {poll+1},
                    items_count: items.length,
                    parsed_count: parsed.length,
                    target_found: targetFound,
                    sample_target: parsed.find(p => p.is_target),
                    total_parsed: parsed
                }};
            }})()
            """
            res = await cdp.evaluate_js(js_check)
            print(f"Poll {poll+1} ({(poll+1)*0.5}s): items={res.get('items_count')}, parsed={res.get('parsed_count')}, target_found={res.get('target_found')}")
            if res.get("target_found"):
                print("\n[TARGET MESSAGE FOUND & VERIFIED!]")
                print("Target details:", res.get("sample_target"))
                print(f"Total conversation messages around target: {res.get('parsed_count')}")
                break

asyncio.run(test_wait_channel_dom())
