"""Exercise the deliverable through real domain resolution and product projection."""
import importlib.util
from pathlib import Path
import pytest


@pytest.mark.anyio
async def test_offline_product_examples_keep_sources_and_distinguish_failure(tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/build_offline_product_preview.py"
    spec = importlib.util.spec_from_file_location("offline_product_preview", path)
    preview = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preview)
    examples = await preview.build_examples()
    cases = {example["id"]: example for example in examples}
    completed = cases["completed"]["payload"]
    assert completed["work_items"][0]["status"] == "COMPLETED"
    assert completed["work_items"][0]["evidence_ids"] == ["E1"]
    assert completed["evidence"][0]["has_valid_permalink"] is True
    blocked = cases["blocked"]["payload"]
    assert blocked["work_items"][0]["status"] == "BLOCKED"
    assert blocked["work_items"][0]["blocker_details"] == ["필수 접근 승인이 없어 사용자 전환을 중단했습니다."]
    assert cases["insufficient"]["payload"]["is_conclusive"] is False
    assert cases["insufficient"]["trace"]["stop_reason"] != cases["provider-failure"]["trace"]["stop_reason"]
    assert cases["provider-failure"]["trace"]["stop_reason"] == "PROVIDER_EXECUTION_FAILURE"
    preview.build_preview(tmp_path, examples)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "합성 데이터" in html and "실제 Slack 조사나 AI 판단 결과가 아닙니다" in html
    assert '<script src=' not in html and 'href="app.css"' not in html
    assert "renderGroundedAnswer" in html
