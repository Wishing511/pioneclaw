"""
AutoDream LLM Prompt 模板

所有 Prompt 要求 JSON 输出，便于程序解析。
"""

DEDUPLICATION_SYSTEM_PROMPT = """你是一个记忆整理助手，专门识别语义重复的记忆条目。"""

DEDUPLICATION_PROMPT = """以下是一组记忆条目，请找出语义重复或高度相似的条目。

规则：
1. 内容实质相同但表述不同 → merge
2. 内容完全包含关系 → merge（子集合并到父集）
3. 同一事件的多个视角记录 → merge
4. 仅时间不同但内容独立 → 不处理

对于每组重复，输出决策：
- keep: 保留的条目 filename
- merge_from: 被合并的条目 filename 列表
- delete: 合并后删除的条目 filename 列表（通常与 merge_from 相同）

输入：
{entries}

输出 JSON（严格格式）：
{{
  "duplicates": [
    {{"keep": "filename1.md", "merge_from": ["filename2.md", "filename3.md"], "delete": ["filename2.md", "filename3.md"]}}
  ],
  "reasoning": "简要说明"
}}

注意：
- 只处理确实重复/相似的条目
- 如果不存在重复，返回空 duplicates 数组
- filename 必须与输入完全一致
"""

CONSOLIDATION_SYSTEM_PROMPT = """你是一个记忆归纳助手，擅长从多个零散记忆中提炼高层级模式和趋势。"""

CONSOLIDATION_PROMPT = """以下是一组去重后的记忆条目，请生成 1-3 条高层级归纳记忆。

要求：
- 不要复述原文，要提炼出"模式"或"趋势"
- 标注涉及的时间范围
- 标注涉及的条目数量
- type 统一为原组的 type

输入：
{entries}

输出 JSON（严格格式）：
{{
  "consolidated_memories": [
    {{
      "name": "归纳记忆名称",
      "description": "一句话摘要",
      "content": "详细归纳内容...",
      "source_filenames": ["filename1.md", "filename2.md"]
    }}
  ]
}}

注意：
- 如果记忆数量太少（少于 3 条）或主题过于分散，可以返回空数组
- name 要简洁，不超过 30 字
- content 要具体，包含关键信息和数据
"""

FRESHNESS_SYSTEM_PROMPT = """你是一个记忆时效评估助手，判断记忆条目当前是否仍有价值。"""

FRESHNESS_PROMPT = """评估以下记忆条目的当前时效价值。

输出决策：
- keep: 仍有价值，保留
- archive: 历史价值，建议归档
- delete: 无价值，建议删除

考虑因素：
- 创建时间（超过 {archive_after_days} 天的 project 记忆优先 archive）
- 内容是否涉及已完成的临时任务
- 内容是否为永久性知识（如架构决策 keep，临时 bug 修复 archive）
- 记忆类型：user/feedback 通常保留较久，project 可能随项目进展过期

输入：
{entries}

输出 JSON（严格格式）：
{{
  "decisions": [
    {{"filename": "xxx.md", "action": "keep|archive|delete", "reason": "简短理由"}}
  ]
}}

注意：
- 谨慎建议 delete，只有明显过时且无历史价值的才 delete
- 大多数记忆应该是 keep 或 archive
- filename 必须与输入完全一致
"""
