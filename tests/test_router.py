"""Tests for src/mypcc/router.py"""
import pytest
from src.mypcc.router import (
    is_personal_query,
    is_course_recommend_query,
    is_schedule_plan_query,
)


# ── is_personal_query ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "what are my courses this term?",
    "show me my balance",
    "who is my advisor?",
    "how many credits do I have?",
    "what requirements do I still need?",
    "my degree progress",
    "am I enrolled in any classes?",
    "tôi đang học những môn gì",
    "của tôi còn thiếu môn nào",
    "kỳ này tôi học gì",
])
def test_personal_query_true(q):
    assert is_personal_query(q), f"Expected personal query: {q!r}"


@pytest.mark.parametrize("q", [
    "what is tuition at PCC?",
    "how do I apply for financial aid?",
    "F-1 visa requirements",
    "hi",
])
def test_personal_query_false(q):
    assert not is_personal_query(q), f"Expected NOT personal query: {q!r}"


# ── is_course_recommend_query ─────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "what courses should I take next term?",
    "which classes can I enroll in?",
    "recommend some courses for me",
    "what requirements do I still need to complete?",
    "summer 2026 courses available",
    "can you recommend some classes for next term?",
    "gợi ý cho tôi môn học",
    "còn thiếu môn gì để tốt nghiệp",
    "tốt nghiệp còn cần gì",
])
def test_recommend_query_true(q):
    assert is_course_recommend_query(q), f"Expected recommend query: {q!r}"


@pytest.mark.parametrize("q", [
    "hi",
    "what is the weather?",
    "help me build a schedule with 12 credits",
])
def test_recommend_query_false(q):
    assert not is_course_recommend_query(q), f"Expected NOT recommend query: {q!r}"


# ── is_schedule_plan_query ────────────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "help me build a schedule",
    "plan my schedule for next term",
    "I want to take 12 credits for next term, what combo?",
    "I want 9 credits online only not available Tuesday",
    "prefer morning classes",
    "not available Friday",
    "done by 4pm",
    "lập lịch học cho tôi",
    "tôi muốn đăng ký 12 tín chỉ kỳ tới",
])
def test_schedule_plan_query_true(q):
    assert is_schedule_plan_query(q), f"Expected schedule plan query: {q!r}"


@pytest.mark.parametrize("q", [
    "hi",
    "what is tuition?",
    "what courses should I take?",
    "my courses this term",
])
def test_schedule_plan_query_false(q):
    assert not is_schedule_plan_query(q), f"Expected NOT schedule plan query: {q!r}"
