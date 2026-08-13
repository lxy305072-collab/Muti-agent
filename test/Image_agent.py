import time
import os
import json
import dashscope
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")


class ImageAgent:
    def __init__(self, model: str = "wanx-v1"):  # 改成通义万相模型
        self.model = model

    def run(self, image_prompt: str) -> str:
        print("️ [ImageAgent] 正在生成配图...")

        rsp = dashscope.ImageSynthesis.call(
            model=self.model,  # wanx-v1 或 wanx2.1-t2i-turbo
            prompt=image_prompt,
            n=1,
            size='1024*1024'
        )

        if rsp.status_code != 200:
            raise Exception(f"图片任务提交失败:{rsp.code} - {rsp.message}")

        if rsp.output and rsp.output.results:
            image_url = rsp.output.results[0].url
            return image_url
        else:
            raise Exception("图片生成失败，未返回url")