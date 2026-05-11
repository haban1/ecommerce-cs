"""
Skill 加载器
从 SKILL.md 文件加载 Skill 定义
"""

import re
import logging
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
import yaml

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """Skill 数据类"""
    name: str
    version: str
    description: str
    triggers: List[str]
    tools: List[str]
    priority: str
    content: str  # Markdown 正文
    file_path: str
    keywords: List[str]


class SkillLoader:
    """Skill 加载器"""

    _PRIORITY_ORDER = {'high': 0, 'medium': 1, 'low': 2}
    _STOP_WORDS = {
        '怎么', '如何', '怎样', '请问', '一下', '这个', '那个', '可以', '一下子',
        '我的', '你们', '我们', '是否', '支持', '有关', '相关', '一下儿', '一下吗',
        '使用', '用', '问题', '方式', '方法', '流程', '帮我', '一下吧'
    }
    _TEXT_VARIANTS = {
        '怎么使用': '怎么用',
        '如何使用': '怎么用',
        '怎样使用': '怎么用',
        '如何用': '怎么用',
        '使用方法': '怎么用',
        '使用说明': '说明书',
        '连不上': '连接失败',
        '连不上手机': '连接失败',
        '用不了': '不能用',
        '无法使用': '不能用',
        '开不了机': '不能用',
        '售后服务': '售后',
        '退钱': '退款',
        '发什么快递': '配送',
        '物流': '配送',
    }

    def __init__(self, skills_dir: str = None):
        if skills_dir is None:
            # 默认为 src/skills 目录
            current_file = Path(__file__)
            skills_dir = current_file.parent

        self.skills_dir = Path(skills_dir)
        self.skills: Dict[str, Skill] = {}
        self._load_all_skills()

    def _load_all_skills(self):
        """加载所有 Skill"""
        if not self.skills_dir.exists():
            logger.warning(f"Skills 目录不存在: {self.skills_dir}")
            return

        # 遍历所有子目录
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            # 跳过特殊目录
            if skill_dir.name.startswith('__') or skill_dir.name.startswith('.'):
                continue

            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                logger.warning(f"未找到 SKILL.md: {skill_dir}")
                continue

            try:
                skill = self._load_skill_file(skill_file)
                self.skills[skill.name] = skill
                logger.info(f"加载 Skill: {skill.name} (v{skill.version})")
            except Exception as e:
                logger.error(f"加载 Skill 失败 {skill_file}: {e}")

    def _load_skill_file(self, file_path: Path) -> Skill:
        """加载单个 SKILL.md 文件"""
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 解析 YAML frontmatter
        if not content.startswith('---'):
            raise ValueError("SKILL.md 必须以 YAML frontmatter 开头")

        parts = content.split('---', 2)
        if len(parts) < 3:
            raise ValueError("SKILL.md 格式错误")

        yaml_content = parts[1].strip()
        markdown_content = parts[2].strip()

        # 解析 YAML
        metadata = yaml.safe_load(yaml_content)

        # 创建 Skill 对象
        skill = Skill(
            name=metadata['name'],
            version=metadata['version'],
            description=metadata['description'],
            triggers=metadata.get('triggers', []),
            tools=metadata.get('tools', []),
            priority=metadata.get('priority', 'medium'),
            content=markdown_content,
            file_path=str(file_path),
            keywords=metadata.get('keywords', []),
        )

        return skill

    def _normalize_text(self, text: str) -> str:
        """归一化常见问法，减少表达差异导致的漏匹配。"""
        normalized = text.lower().strip()
        for source, target in self._TEXT_VARIANTS.items():
            normalized = normalized.replace(source, target)
        return normalized

    def _extract_keywords(self, text: str) -> set[str]:
        """从文本中抽取可用于粗匹配的中文/英文短词。"""
        candidates = re.findall(r'[\u4e00-\u9fff]{2,8}|[a-z0-9_\-]{2,}', text.lower())
        return {token for token in candidates if token not in self._STOP_WORDS}

    def _score_skill(self, skill: Skill, normalized_input: str, input_keywords: set[str]) -> tuple[int, str]:
        """返回 Skill 分数和命中原因。"""
        best_trigger = ''
        trigger_score = 0
        for trigger in skill.triggers:
            normalized_trigger = self._normalize_text(trigger)
            if normalized_trigger and normalized_trigger in normalized_input:
                score = 100 + len(normalized_trigger)
                if score > trigger_score:
                    trigger_score = score
                    best_trigger = trigger

        keyword_score = 0
        matched_keywords: list[str] = []
        for keyword in skill.keywords:
            normalized_keyword = self._normalize_text(keyword)
            if not normalized_keyword:
                continue
            if normalized_keyword in normalized_input:
                keyword_score += 12
                matched_keywords.append(keyword)
            elif normalized_keyword in input_keywords:
                keyword_score += 8
                matched_keywords.append(keyword)

        if trigger_score > 0:
            return trigger_score + keyword_score, f'触发词: {best_trigger}'

        if keyword_score >= 16:
            return keyword_score, f'关键词: {", ".join(matched_keywords[:3])}'

        return 0, ''

    def get_skill(self, name: str) -> Optional[Skill]:
        """获取指定 Skill"""
        return self.skills.get(name)

    def get_all_skills(self) -> List[Skill]:
        """获取所有 Skill"""
        return list(self.skills.values())

    def get_skill_routing_metadata(self) -> List[Dict[str, object]]:
        """返回供路由模型使用的精简 Skill 元数据。"""
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "tools": skill.tools,
                "priority": skill.priority,
            }
            for skill in self.get_all_skills()
        ]

    def format_skill_catalog_for_llm(self) -> str:
        """将 Skill 元数据格式化为适合放入 prompt 的文本。"""
        sections: list[str] = []
        for skill in self.get_all_skills():
            sections.append(
                "\n".join(
                    [
                        f"- name: {skill.name}",
                        f"  description: {skill.description}",
                        f"  triggers: {', '.join(skill.triggers) if skill.triggers else '无'}",
                        f"  keywords: {', '.join(skill.keywords) if skill.keywords else '无'}",
                        f"  tools: {', '.join(skill.tools) if skill.tools else '无'}",
                        f"  priority: {skill.priority}",
                    ]
                )
            )
        return "\n\n".join(sections)

    def match_skill(self, user_input: str) -> Optional[Skill]:
        """
        根据用户输入匹配最合适的 Skill

        Args:
            user_input: 用户输入文本

        Returns:
            匹配的 Skill，如果没有匹配则返回 None
        """
        normalized_input = self._normalize_text(user_input)
        input_keywords = self._extract_keywords(normalized_input)

        best_match: Optional[Skill] = None
        best_score = 0
        best_reason = ''

        for skill in self.skills.values():
            score, reason = self._score_skill(skill, normalized_input, input_keywords)
            if score <= 0:
                continue

            if best_match is None or score > best_score:
                best_match = skill
                best_score = score
                best_reason = reason
                continue

            if score == best_score:
                current_priority = self._PRIORITY_ORDER.get(skill.priority, 1)
                best_priority = self._PRIORITY_ORDER.get(best_match.priority, 1)
                if current_priority < best_priority:
                    best_match = skill
                    best_reason = reason

        if best_match:
            logger.info(f"匹配到 Skill: {best_match.name} ({best_reason}, score={best_score})")

        return best_match

    def get_skill_prompt(self, skill: Skill) -> str:
        """
        生成 Skill 的系统提示词

        Args:
            skill: Skill 对象

        Returns:
            系统提示词
        """
        prompt = f"""# 当前激活 Skill: {skill.name}

{skill.content}

## 可用工具
{', '.join(skill.tools)}

## 工具结果处理要求
- 只要工具可用且能够提供事实、状态、政策、文档、订单等信息，就必须先调用工具再回答
- 如果工具结果为空、无关或未找到，必须先明确说明“已检索但未找到直接可用的信息”，再向用户索取最小必要补充信息
- 不要在没有工具结果时直接用常识补全答案

请根据上述 Skill 指南处理用户请求。"""

        return prompt


# 全局 Skill 加载器实例
_skill_loader: Optional[SkillLoader] = None


def get_skill_loader() -> SkillLoader:
    """获取全局 Skill 加载器"""
    global _skill_loader
    if _skill_loader is None:
        _skill_loader = SkillLoader()
    return _skill_loader