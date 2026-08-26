from agentcli.routing.classifier import CHAT, CODE, REASONING, classify


def test_code_category():
    assert classify("fix this bug: def foo(): pass") == CODE
    assert classify("```python\nprint('hi')\n```") == CODE
    assert classify("the tests/test_cli.py file fails on windows") == CODE
    assert classify("I get a traceback when I run the script") == CODE
    assert classify("run docker ps and paste the output") == CODE
    assert classify("SELECT id FROM users WHERE name = 'x'") == CODE


def test_reasoning_category():
    assert classify("explain why the sky is blue") == REASONING
    assert classify("compare the trade-offs between the two designs") == REASONING
    assert classify("what are the pros and cons here?") == REASONING
    assert classify("solve this: 12 * 8 =") == REASONING
    assert classify("walk me through the proof step by step") == REASONING


def test_chat_is_default():
    assert classify("hey there!") == CHAT
    assert classify("") == CHAT
    assert classify("tell me a joke") == CHAT
    assert classify("good morning") == CHAT


def test_code_wins_over_reasoning():
    assert classify("explain why this traceback happens in main.py") == CODE


def test_reasoning_wins_over_chat():
    assert classify("why do cats purr?") == REASONING
