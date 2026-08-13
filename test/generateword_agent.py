import dashscope
import json
import os
from dotenv import load_dotenv
from Tools.schema_tool import MarketingInput, CopywriterOutput



# 加载 .env 文件
load_dotenv()
# 设置API Key
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")


class CopywriterAgent:
    def __init__(self, model="qwen-max"):
        self.model = model

    def run(self, user_input: MarketingInput) -> CopywriterOutput:
        print("⏳ [CopywriterAgent] 正在生成营销文案...")

        prompt = f"""
        你是一个资深营销专家。请为以下产品设计营销文案：
        产品名称：{user_input.product_name}
        核心卖点：{user_input.key_features}
        目标受众：{user_input.target_audience}

        请严格以JSON格式输出，包含以下字段：
        1. title: 吸引人的标题（20字以内）
        2. body: 适合社交媒体的正文（150字以内，带emoji）
        3. image_prompt: 用来生成配图的英文画面描述（不要包含文字要求，只描述画面场景，细节丰富）
        """

        # 调用通义千问
        response = dashscope.Generation.call(
            model=self.model,
            prompt=prompt,
            response_format={"type": "json_object"},  # 强制输出JSON
        )

        if response.status_code == 200:
            # 解析返回的JSON
            result_dict = json.loads(response.output.text)
            return CopywriterOutput(**result_dict)
        else:
            raise Exception(f"文案生成失败: {response.code} - {response.message}")