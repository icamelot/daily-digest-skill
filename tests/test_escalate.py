"""Tests for escalation detector."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from digest.scripts.escalate import check_escalation


def test_no_escalation_on_normal_chat():
    config = {"digest": {"escalation_keywords": ["报错", "失败", "冲突"]}}
    messages = [
        {"sender_name": "AgentA", "text": "今天天气不错"},
        {"sender_name": "AgentB", "text": "是的，适合写代码"},
    ]
    result = check_escalation(messages, config)
    assert result["should_escalate"] is False


def test_escalation_on_keyword():
    config = {"digest": {"escalation_keywords": ["报错"]}}
    messages = [
        {"sender_name": "AgentA", "text": "构建报错了，需要修复"},
    ]
    result = check_escalation(messages, config)
    assert result["should_escalate"] is True
    assert "报错" in result["reasons"][0]


def test_no_escalation_on_empty():
    config = {"digest": {"escalation_keywords": ["报错"]}}
    result = check_escalation([], config)
    assert result["should_escalate"] is False


def test_escalation_on_repetition():
    config = {"digest": {"escalation_keywords": []}}
    messages = [
        {"sender_name": "AgentA", "text": "需要改这个接口吗？" + "x" * 20}
        for _ in range(5)
    ]
    result = check_escalation(messages, config)
    assert result["should_escalate"] is True
