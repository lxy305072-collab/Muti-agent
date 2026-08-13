
from test.test import ImageAgent
from Tools.schema_tool import FinalOutput, MarketingInput
from test.test import CopywriterAgent


class MarketingPipeline:
    def __init__(self):
        self.copywriter = CopywriterAgent()
        self.image_generator = ImageAgent()

    def execute(self, product_name: str,key_features:str,target_audience:str) ->FinalOutput:
        print(f"🚀 开始执行营销物料生成流水线，产品：{product_name}")

        #1.构造输入
        input_data = MarketingInput(
            product_name=product_name,
            key_features=key_features,
            target_audience=target_audience
        )

        #2.执行文案生成
        copy_result = self.copywriter.run(input_data)
        print(f"✅ [文案生成完成] 标题：{copy_result.title}")

        #3.执行图片生成
        try:
            image_url=self.image_generator.run(copy_result.image_prompt)
            print(f"✅ [配图生成完成] URL：{image_url}")
        except Exception as e:
            print(f"⚠️ [配图生成失败] 错误：{e}")
            image_url=None

        #4.组装最终结果
        return FinalOutput(
            title=copy_result.title,
            body=copy_result.body,
            image_url=image_url

        )

if __name__ == '__main__':
    pipeline = MarketingPipeline()

    # 测试用例：一款面向年轻人的降噪耳机
    result = pipeline.execute(
        product_name="红米降噪耳机",
        key_features="深度降噪、长续航、高清音质",
        target_audience="通勤族、大学生"
    )

    print("\n" + "=" * 40)
    print("🎉 最终物料产出：")
    print(f"标题: {result.title}")
    print(f"正文: \n{result.body}")
    print(f"配图URL: {result.image_url if result.image_url else '生成失败'}")
    print("=" * 40)





















