"""chat._strip_visible_chain_of_thought：剥离误写入 content 的 Thinking Process。"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from chat import (
    _FALLBACK_WHEN_COULD_NOT_STRIP_COT,
    _assistant_message_text,
    _strip_visible_chain_of_thought,
)


def test_strip_thinking_process_mixed_single_newlines():
    text = """**Thinking Process:**

1. **Analyze the Request**
**Role**: MiniMem
**Input**: 用户说了克罗地亚水果风味
**Constraints**: NO Thinking Process

2. **Evaluate Memory**

克罗地亚那款水果风味确实很特别，下次可以试试配点气泡水！
"""
    out = _strip_visible_chain_of_thought(text)
    assert "Thinking" not in out
    assert "Analyze" not in out
    assert "克罗地亚那款水果风味" in out


def test_strip_short_english_then_chinese_tail():
    text = """Thinking Process:
1. **Analyze the Request**
The user likes fruit flavors and mentioned Croatia.
克罗地亚那款很好喝，你可以再找找类似风味。"""
    out = _strip_visible_chain_of_thought(text)
    assert "克罗地亚" in out
    assert "Analyze" not in out


def test_final_polish_make_it_then_quoted_chinese():
    """Final Polish 节后先英文 Make it 再引号中文，只留中文给用户。"""
    text = (
        "Thinking Process:\n\n5. **Final Polish:**\n"
        "Make it sound more like a friend.\n"
        '"哎呀，今天星期二呀～"\n\n'
        "Wait, I need to verify.\n\n"
        "Actually no.\n"
    )
    out = _strip_visible_chain_of_thought(text)
    assert "Make it" not in out and "Wait" not in out
    assert "星期二" in out


def test_cot_only_long_english_falls_back():
    """无 Final Polish、正文几乎全英文时，返回兜底中文而非整页 CoT。"""
    text = (
        '我们刚刚聊了啥还记得吗\nThinking Process:\n\n1. **Analyze:**\n'
        '*   **User Input:** "我们刚刚聊了啥还记得吗"\n'
        '*   *Wait, looking at the actual conversation history:*\n'
        '*   *Correction:* The prompt shows nested bullets.\n'
        '*   *Actually, looking at the raw input:*\n'
        '    * User: 你好啊\n'
        'English padding without a Chinese reply paragraph for the end user. '
        'The model got truncated before Final Polish. '
        'More English to exceed two hundred characters total length easily here.\n'
    )
    out = _strip_visible_chain_of_thought(text)
    assert out == _FALLBACK_WHEN_COULD_NOT_STRIP_COT


def test_last_final_polish_skips_tail_meta():
    """多次 *Final Polish:* 取最后一次；*Final Plan:* 等尾部元信息丢弃。"""
    text = """Thinking Process:
1. **Drafting Response:**
    *Final Polish:*
    中间草稿不要。

    *Final Polish:*
    最终这句给用户。
    第二段也要。

    *Wait, one more check:*

    *Final Plan:*这是什么玩意
"""
    out = _strip_visible_chain_of_thought(text)
    assert "最终这句给用户" in out
    assert "中间草稿" not in out
    assert "什么玩意" not in out
    assert "Thinking" not in out


def test_strip_explicit_think_xml_tags():
    msg = {
        "content": "<think>内部推理不能给用户看</think>\n你好呀！有什么想聊的吗？"
    }
    out = _assistant_message_text(msg)
    assert "内部推理" not in out
    assert "<think>" not in out
    assert "你好呀" in out


if __name__ == "__main__":
    test_strip_thinking_process_mixed_single_newlines()
    test_strip_short_english_then_chinese_tail()
    test_final_polish_make_it_then_quoted_chinese()
    test_cot_only_long_english_falls_back()
    test_last_final_polish_skips_tail_meta()
    test_strip_explicit_think_xml_tags()
    print("test_chat_cot_strip: OK")
