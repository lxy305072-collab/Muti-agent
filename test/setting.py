from typing import TypedDict ,Optional,Annotated
from pydantic import BaseModel,Field

#用于在Langgraph中合并列表或覆盖更新
def overwrite(old,new):
    return new

class CampaignState(TypedDict):
    product_name: str
    key_features: str
    target_audience: str

    title: str
    body: str
    image_prompt: str

    image_url:Optional[str]
    review_passed: bool
    review_feedback: str
    retry_count: int


