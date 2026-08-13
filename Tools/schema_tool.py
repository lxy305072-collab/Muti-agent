
from pydantic import BaseModel, Field,ValidationError
import dashscope
import json
import os
from dotenv import load_dotenv

load_dotenv()

dashscope.api_key = os.getenv("DASH_API_KEY")



# --- 定义严格的输出 Schema ---
class CopywriterSchema(BaseModel):
    title: str = Field(description="吸引人的营销标题")
    body: str = Field(description="营销正文带emoji")
    image_prompt: str = Field(description="用于AI绘画的画面描述，中文最佳")

class ReviewerSchema(BaseModel):
    passed:bool = Field(description="图片是否通过审查")
    feedback:str = Field(description="如果未通过，给出修改画图的中文画面描述")

# --- 带容错修复的 LLM 调用工具 ---
def call_llm_with_schema(model:str,prompt:str,schema:type[BaseModel],max_retries: int = 2):
    """
    调用大模型并强制校验JSON，如果格式错误自动让大模型修复

    """
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)

    for attempt in range(max_retries + 1):
        # 强化提示词，要求严格遵守 Schema
        final_prompt = f"{prompt}\n\n请严格按照以下JSON Schema输出，不要包含任何其他文本或Markdown标记：\n{schema_json}"

        response  = dashscope.Generation.call(
            model=model,
            prompt=final_prompt,
            response_format={"type":"json_object"},
        )

        if response.status_code != 200:
            raise Exception(f"API调用失败：{response.message}")

        text_content = None

        # 1. 先尝试从 choices 里拿（兼容新版/带思考过程的模型）
        # 注意：必须判断 choices 本身不是 None，且列表不为空
        if hasattr(response.output, 'choices') and response.output.choices:
            if response.output.choices[0] and hasattr(response.output.choices[0], 'message'):
                text_content = response.output.choices[0].message.content

        # 2. 如果 choices 没拿到，再去 text 里拿（兼容老版模型）
        if text_content is None and hasattr(response.output, 'text'):
            text_content = response.output.text

        # 3. 终极检查：如果两个抽屉都是空的，直接报错并打印案发现场
        if text_content is None:
            print("=== 大模型原始返回（排错专用） ===")
            print(response)
            raise Exception("大模型返回了空内容，所有抽屉都是空的！请检查提示词或API！")
        # ==========================================
        try:
            # 尝试解析并校验 (这里用我们刚拿到的 text_content)
            result_dict = json.loads(text_content)
            return schema.model_validate(result_dict)

        except (json.JSONDecodeError, ValidationError) as e:
            print(f"⚠️ [Schema校验失败] 第 {attempt + 1} 次，错误: {e}")
            if attempt < max_retries:
                # 核心修复逻辑：把错误信息丢回给大模型
                prompt = f"""
                        你之前的输出格式有误。原始要求：{prompt}
                        你的错误输出：{response.output.text}
                        具体的校验错误：{str(e)}
                        请修正格式，重新输出正确的JSON。
                        """
            else:
                raise Exception(f"大模型多次修复失败，放弃: {e}")

























