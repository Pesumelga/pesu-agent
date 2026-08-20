import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
import websockets
import psutil

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

sys.path.insert(0, "src")
from pesu_agent.lifecycle.slack_lifecycle import SlackLifecycleManager
from pesu_agent.adapters.slack_cdp import SlackCdpAdapter

async def test_secondary_renderer():
    manager = SlackLifecycleManager()
    
    # 1. Start Slack in Agent Mode if needed
    status = manager.ensure_agent_ready(allow_restart=False)
    if status.status != status.status.AGENT_READY:
        slack_bin = manager.find_slack_app_binary()
        for p in psutil.process_iter(["name"]):
            if p.info["name"] and "slack" in p.info["name"].lower():
                try: p.kill()
                except: pass
        time.sleep(1.5)
        subprocess.Popen([slack_bin, "--remote-debugging-port=9222"])
        for i in range(25):
            time.sleep(1.0)
            if manager.check_cdp_ready()[0]: break
        await asyncio.sleep(6.0)

    # 2. Query Main Renderer info
    with urllib.request.urlopen("http://127.0.0.1:9222/json") as r:
        targets_before = json.loads(r.read().decode("utf-8"))

    main_page = next(t for t in targets_before if t.get("type") == "page")
    print(f"Main Renderer BEFORE: Title={main_page.get('title')!r}, URL={main_page.get('url')}")

    test_permalink = "https://heumlabs.slack.com/archives/C3R53LYV8/p1787028341517949"
    print(f"Test Permalink to open: {test_permalink}")

    # Connect to Main Page WS or Browser WS
    async with SlackCdpAdapter(lifecycle_manager=manager) as cdp:
        # Method A: Try Target.createTarget via CDP
        # Note: Runtime.evaluate window.open or Target.createTarget
        print("\n--- Testing Target Creation ---")
        
        # In CDP, we can call Target.createTarget or window.open in Main Renderer
        js_open_window = f"""
        (() => {{
            try {{
                const win = window.open({json.dumps(test_permalink)}, '_blank');
                return {{ opened: !!win }};
            }} catch (err) {{
                return {{ opened: false, error: err.toString() }};
            }}
        }})()
        """
        win_res = await cdp.evaluate_js(js_open_window)
        print(f"window.open result: {win_res}")

        await asyncio.sleep(3.0)

        # Check targets now
        with urllib.request.urlopen("http://127.0.0.1:9222/json") as r:
            targets_after = json.loads(r.read().decode("utf-8"))

        print(f"\nTargets after window.open: {len(targets_after)} targets found")
        for idx, t in enumerate(targets_after):
            print(f"  Target [{idx}]: Type={t.get('type')}, Title={t.get('title')!r}, URL={t.get('url')}")

        # Check if new page target exists
        new_targets = [t for t in targets_after if t.get("id") != main_page.get("id") and t.get("type") == "page"]
        if new_targets:
            sec_target = new_targets[0]
            print(f"\n[SUCCESS] Found Secondary Page Target: {sec_target.get('title')!r} (ID: {sec_target.get('id')})")
            print(f"Connecting to Secondary Target WebSocket: {sec_target.get('webSocketDebuggerUrl')} ...")

            ws_sec = await websockets.connect(sec_target["webSocketDebuggerUrl"])
            msg_id = 0
            async def eval_sec(expr):
                nonlocal msg_id
                msg_id += 1
                await ws_sec.send(json.dumps({"id": msg_id, "method": "Runtime.evaluate", "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
                while True:
                    raw = await ws_sec.recv()
                    resp = json.loads(raw)
                    if resp.get("id") == msg_id:
                        return resp.get("result", {}).get("result", {}).get("value")

            # Wait 3s and check DOM in secondary target
            await asyncio.sleep(3.0)
            sec_dom = await eval_sec("""
            (() => {
                return {
                    title: document.title,
                    url: window.location.href,
                    messages_count: document.querySelectorAll('.c-virtual_list__item, [data-qa="message_container"], [role="listitem"]').length
                };
            })()
            """)
            print(f"Secondary Target DOM info: {sec_dom}")

            # Close secondary target via window.close() or CDP Target.closeTarget
            print("Closing secondary target...")
            await eval_sec("window.close();")
            await ws_sec.close()
            print("Secondary target closed.")

        else:
            print("\nwindow.open did not create a new target (Electron may have intercepted or opened in external browser).")

        # Verify Main Renderer URL after
        with urllib.request.urlopen("http://127.0.0.1:9222/json") as r:
            targets_final = json.loads(r.read().decode("utf-8"))
        main_page_final = next((t for t in targets_final if t.get("id") == main_page.get("id")), None)
        print(f"\nMain Renderer AFTER: Title={main_page_final.get('title')!r}, URL={main_page_final.get('url')}")
        print(f"Main Renderer URL unchanged? {main_page.get('url') == main_page_final.get('url')}")

if __name__ == "__main__":
    asyncio.run(test_secondary_renderer())
