"""
Skill 管理 API 路由
提供 Skill 列表、重载、详情查询等接口
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..skills.loader import get_skill_loader, SkillLoader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skills"])


# ==================== 响应模型 ====================

class SkillSummary(BaseModel):
    """Skill 摘要信息"""
    name: str = Field(..., description="Skill 名称")
    version: str = Field(..., description="版本号")
    description: str = Field(..., description="功能描述")
    priority: str = Field(..., description="优先级：high/medium/low")
    triggers_count: int = Field(..., description="触发词数量")
    tools_count: int = Field(..., description="工具数量")
    file_path: str = Field(..., description="文件路径")


class SkillDetail(BaseModel):
    """Skill 完整信息"""
    name: str = Field(..., description="Skill 名称")
    version: str = Field(..., description="版本号")
    description: str = Field(..., description="功能描述")
    triggers: List[str] = Field(..., description="触发词列表")
    tools: List[str] = Field(..., description="工具列表")
    priority: str = Field(..., description="优先级")
    content: str = Field(..., description="Markdown 正文")
    file_path: str = Field(..., description="文件路径")


class SkillListResponse(BaseModel):
    """Skill 列表响应"""
    total: int = Field(..., description="总数")
    skills: List[SkillSummary] = Field(..., description="Skill 列表")


class ReloadResponse(BaseModel):
    """重载响应"""
    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="消息")
    loaded_count: int = Field(..., description="加载的 Skill 数量")
    timestamp: str = Field(..., description="时间戳")


# ==================== API 端点 ====================

@router.get("", response_model=SkillListResponse)
async def list_skills():
    """
    列出所有已注册的 Skill
    
    返回所有 Skill 的摘要信息，包括名称、描述、状态等
    """
    try:
        loader = get_skill_loader()
        all_skills = loader.get_all_skills()
        
        skill_summaries = [
            SkillSummary(
                name=skill.name,
                version=skill.version,
                description=skill.description,
                priority=skill.priority,
                triggers_count=len(skill.triggers),
                tools_count=len(skill.tools),
                file_path=skill.file_path
            )
            for skill in all_skills
        ]
        
        return SkillListResponse(
            total=len(skill_summaries),
            skills=skill_summaries
        )
        
    except Exception as e:
        logger.error(f"获取 Skill 列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取 Skill 列表失败: {str(e)}")


@router.post("/reload", response_model=ReloadResponse)
async def reload_skills():
    """
    热重载 skills/ 目录
    
    重新扫描并加载所有 SKILL.md 文件，无需重启服务
    """
    try:
        # 重新创建 SkillLoader 实例
        global _skill_loader
        from ..skills.loader import _skill_loader
        
        # 创建新的加载器实例
        new_loader = SkillLoader()
        
        # 替换全局实例
        import sys
        skills_module = sys.modules.get('src.skills.loader')
        if skills_module:
            skills_module._skill_loader = new_loader
        
        loaded_count = len(new_loader.get_all_skills())
        
        logger.info(f"Skill 重载成功，加载了 {loaded_count} 个 Skill")
        
        return ReloadResponse(
            success=True,
            message=f"成功重载 {loaded_count} 个 Skill",
            loaded_count=loaded_count,
            timestamp=datetime.now().isoformat()
        )
        
    except Exception as e:
        logger.error(f"重载 Skill 失败: {e}")
        raise HTTPException(status_code=500, detail=f"重载 Skill 失败: {str(e)}")


@router.get("/{name}", response_model=SkillDetail)
async def get_skill_detail(name: str):
    """
    查看某个 Skill 的完整定义
    
    Args:
        name: Skill 名称
    
    返回指定 Skill 的完整信息，包括触发词、工具、Markdown 内容等
    """
    try:
        loader = get_skill_loader()
        skill = loader.get_skill(name)
        
        if skill is None:
            raise HTTPException(
                status_code=404,
                detail=f"未找到 Skill: {name}"
            )
        
        return SkillDetail(
            name=skill.name,
            version=skill.version,
            description=skill.description,
            triggers=skill.triggers,
            tools=skill.tools,
            priority=skill.priority,
            content=skill.content,
            file_path=skill.file_path
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取 Skill 详情失败 ({name}): {e}")
        raise HTTPException(status_code=500, detail=f"获取 Skill 详情失败: {str(e)}")