from langchain_community.chat_models import ChatOpenAI

ds_llm = ChatOpenAI(
    model_name="deepseek-r1:1.5b",
    openai_api_base="http://localhost:11434/v1",  # 注意是 /v1
    openai_api_key="ollama",  # 随便写，不校验，但必须提供
)

for chunk in ds_llm.stream("你好"):
    print(type(chunk))
    print(chunk)
