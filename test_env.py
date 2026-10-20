from dotenv import load_dotenv
import os

load_dotenv()

print("REDIS_HOST:", os.getenv("REDIS_HOST"))
print("REDIS_PORT:", os.getenv("REDIS_PORT"))
print("REDIS_PASSWORD:", repr(os.getenv("REDIS_PASSWORD")))
