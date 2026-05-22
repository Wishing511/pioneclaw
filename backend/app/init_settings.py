"""
初始化系统设置数据
"""

import asyncio

from sqlalchemy import select

from app.api.settings import DEFAULT_SETTINGS
from app.core import async_session_maker
from app.models import SystemSetting


async def init_settings():
    """初始化系统设置"""
    async with async_session_maker() as session:
        # 检查是否已有设置
        result = await session.execute(select(SystemSetting).limit(1))
        if result.scalar_one_or_none():
            print("系统设置已存在，跳过初始化")
            return

        # 创建默认设置
        for key, (value, category, description) in DEFAULT_SETTINGS.items():
            setting = SystemSetting(
                key=key, value=value, category=category, description=description
            )
            session.add(setting)

        await session.commit()
        print("系统设置初始化完成！")


if __name__ == "__main__":
    asyncio.run(init_settings())
