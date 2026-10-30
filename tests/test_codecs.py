import copy
import json

import pytest
from conftest import chunk

from anyllama.codecs import Accumulator, decode_request, native_message, native_request, ollama_messages
from anyllama.models import ProxyError


def tool(name, args):
    return {"function": {"name": name, "arguments": args}}


def test_parallel_same_name_tools_are_correlated_deterministically():
    history = [
        {"role": "user", "content": "Read two files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [tool("read", {"path": "a"}), tool("read", {"path": "b"})],
        },
        {"role": "tool", "tool_name": "read", "content": "A"},
        {"role": "tool", "tool_name": "read", "content": "B"},
    ]
    original = copy.deepcopy(history)
    first = ollama_messages(history)
    assert first == ollama_messages(history)
    assert history == original
    calls = first[1]["tool_calls"]
    assert calls[0]["id"] != calls[1]["id"]
    assert first[2]["tool_call_id"] == calls[0]["id"]
    assert first[3]["tool_call_id"] == calls[1]["id"]
    native = native_request({"model": "test", "stream": False, "messages": first})
    assert native["messages"][1]["tool_calls"][0]["function"]["arguments"] == {"path": "a"}
    assert native["messages"][2]["tool_name"] == "read"


def test_explicit_out_of_order_results_preserve_ids():
    calls = [{"id": "a", **tool("read", {})}, {"id": "b", **tool("read", {})}]
    messages = [
        {"role": "assistant", "tool_calls": calls},
        {"role": "tool", "tool_call_id": "b", "content": "B"},
        {"role": "tool", "tool_call_id": "a", "content": "A"},
    ]
    assert ollama_messages(messages)[1]["tool_call_id"] == "b"


def test_orphaned_tool_result_is_rejected():
    with pytest.raises(ProxyError, match="Cannot match"):
        ollama_messages([{"role": "tool", "content": "orphan"}])


def test_fragmented_parallel_arguments_accumulate_by_index():
    acc = Accumulator("alias")
    acc.add(
        chunk(
            {
                "tool_calls": [
                    {"index": 1, "id": "b", "function": {"name": "read", "arguments": '{"path":'}},
                    {"index": 0, "id": "a", "function": {"name": "read", "arguments": '{"path":'}},
                ]
            }
        )
    )
    acc.add(
        chunk(
            {
                "tool_calls": [
                    {"index": 0, "function": {"arguments": '"a"}'}},
                    {"index": 1, "function": {"arguments": '"b"}'}},
                ]
            },
            "tool_calls",
        )
    )
    calls = acc.completion()["choices"][0]["message"]["tool_calls"]
    assert [c["id"] for c in calls] == ["a", "b"]
    assert json.loads(calls[1]["function"]["arguments"]) == {"path": "b"}


def test_images_and_reasoning_remain_separate():
    data = decode_request(
        {
            "model": "a",
            "stream": False,
            "messages": [
                {"role": "user", "content": "Describe", "images": ["YWJj"]},
                {"role": "assistant", "content": "Answer", "thinking": "Thought"},
            ],
        },
        "ollama",
    )
    native = native_request(data)
    assert native["messages"][0]["images"] == ["YWJj"]
    assert native["messages"][1]["thinking"] == "Thought"
    assert native["messages"][1]["content"] == "Answer"


def test_opaque_reasoning_replay_fails_explicitly():
    with pytest.raises(ProxyError, match="reasoning replay"):
        native_request(
            {
                "model": "a",
                "stream": False,
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "reasoning_details": [{"type": "reasoning.encrypted", "data": "signed"}],
                    }
                ],
            }
        )


def test_stream_accumulator_does_not_retain_conversation_text():
    acc = Accumulator("a", retain_content=False)
    acc.add(chunk({"content": "private" * 100, "reasoning": "private thought"}, "stop"))
    assert acc.content == acc.reasoning == ""


def test_plain_reasoning_details_are_translated_without_losing_text():
    message = {"content": "Answer", "reasoning_details": [{"type": "reasoning.text", "text": "Thinking"}]}
    assert native_message(message) == {"role": "assistant", "content": "Answer", "thinking": "Thinking"}
    native = native_request({"model": "a", "stream": False, "messages": [{"role": "assistant", **message}]})
    assert native["messages"][0]["thinking"] == "Thinking"
    assert "reasoning_details" not in native["messages"][0]


def test_jpeg_images_keep_the_correct_media_type():
    import base64

    image = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 40).decode()
    request = decode_request(
        {"model": "a", "messages": [{"role": "user", "content": "Look", "images": [image]}]}, "ollama"
    )
    assert request["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_invalid_fragmented_tool_json_is_not_a_success():
    acc = Accumulator("a")
    acc.add(
        chunk(
            {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "read", "arguments": '{"x":'}}]},
            "tool_calls",
        )
    )
    with pytest.raises(ProxyError, match="valid JSON"):
        acc.completion()


def test_malformed_content_parts_are_rejected_before_provider_call():
    with pytest.raises(ProxyError) as caught:
        decode_request({"model": "a", "messages": [{"role": "user", "content": ["invalid"]}]}, "openai")
    assert caught.value.status == 400


@pytest.mark.parametrize(
    "body", [{}, {"model": "a", "messages": []}, {"model": "a", "stream": "true", "messages": []}]
)
def test_invalid_requests(body):
    with pytest.raises(ProxyError):
        decode_request(body, "openai")
