from types import SimpleNamespace

from yuxi.agents.buildin.chatbot.prompt import build_prompt_with_context
from yuxi.agents.skills.service import SKILL_CATEGORY_CODES


def test_chatbot_prompt_declares_workspace_visibility_and_default_write_boundary():
    prompt = build_prompt_with_context(
        SimpleNamespace(
            workdir_path="/home/gem/user-data/projects/11111111-1111-4111-8111-111111111111",
            system_prompt="",
        )
    )

    assert "可以读取其他 Project 目录作为参考" in prompt
    assert "未经用户明确要求，不得在当前 Project Workdir 之外" in prompt
    assert "/home/gem/user-data/agents/skills/" in prompt
    assert "先在当前 Project Workdir 创建目录和 SKILL.md，再调用 install_skill" in prompt
    assert "不要直接在 /home/gem/user-data/agents/skills/ 下新建" in prompt
    assert "SKILL.md" in prompt and "category" in prompt
    assert "确实无法归类" in prompt
    assert all(category in prompt for category in SKILL_CATEGORY_CODES)
    assert "install_skill" in prompt
    assert "交互式智能体“联合智擎”" in prompt
    assert "语析" not in prompt
    assert "html:preview" not in prompt
