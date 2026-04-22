import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
from harness.context_builder import ContextBuilder, estimate_tokens

def test_build_within_budget():
    cb = ContextBuilder(token_budget=100)
    cb.add_system_prompt("系统提示词")
    cb.add_task_input("用户输入内容")
    result = cb.build()
    assert "系统提示词" in result
    assert "用户输入内容" in result

def test_build_respects_priority():
    cb = ContextBuilder(token_budget=50)
    cb.add_system_prompt("A" * 10)   # priority 0, must be kept
    cb.add_task_input("B" * 10)      # priority 4
    cb.segments.append((3, "C" * 200, "large_low_priority"))
    result = cb.build()
    assert "A" * 10 in result
    assert "C" * 200 not in result

def test_build_continues_after_skipping_large_segment():
    """After skipping an oversized segment, smaller later segments should still be included."""
    cb = ContextBuilder(token_budget=30)
    cb.segments = [
        (0, "A" * 5, "small_high"),
        (1, "B" * 100, "large_mid"),
        (2, "C" * 5, "small_low"),
    ]
    result = cb.build()
    assert "A" * 5 in result
    assert "C" * 5 in result  # key: continues after skipping large segment

def test_estimate_tokens():
    text = "hello world"
    assert 1 <= estimate_tokens(text) <= 20
