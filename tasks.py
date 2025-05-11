import asyncio
import os
from langchain_community.chat_models import ChatOpenAI
from langchain.memory import ChatMessageHistory, ConversationBufferMemory
from prompt import ai_learn_path_prompt_template, ai_followup_prompt_template
from ext_taskiq import broker
from ext_redis import redis_client
import json
from typing import Dict, List
from dotenv import load_dotenv

DEBUG=True

if DEBUG:
    load_dotenv(".env.example")
else:
    load_dotenv(".env")

MODEL_NAME = os.getenv("MODEL_NAME")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

print(f"MODEL_NAME:{MODEL_NAME}")
print(f"OPENAI_API_BASE:{OPENAI_API_BASE}")
print(f"OPENAI_API_KEY:{OPENAI_API_KEY}")

message_histories: Dict[str, ChatMessageHistory] = {}

@broker.task
async def process_query_llm_task(question, task_id, conversation_id=None, is_followup=False):
    should_stop = False

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"llm_control:{task_id}")

    async def check_stop_signal():
        nonlocal should_stop
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.01)
                if message is not None and message["type"] == "message":
                    data = json.loads(message["data"])
                    if data.get("command") == "stop":
                        should_stop = True
                        return
                # 给其他协程一个运行的机会
                await asyncio.sleep(0.01)
        except Exception as e:
            print(f"Error in check_stop_signal: {str(e)}")

    stop_task = asyncio.create_task(check_stop_signal())

    if conversation_id not in message_histories:
        message_histories[conversation_id] = ChatMessageHistory()

    ds_llm = ChatOpenAI(
        model_name=MODEL_NAME,
        openai_api_base=OPENAI_API_BASE,  # 注意是 /v1
        openai_api_key=OPENAI_API_KEY,  # 随便写，不校验，但必须提供
    )

    message_histories[conversation_id].add_user_message(question)
    if not is_followup:
        prompt = ai_learn_path_prompt_template.invoke({"question": question})
    else:
        message_history = message_histories[conversation_id].messages
        prompt = ai_followup_prompt_template.invoke({
            "history": message_history[:-1],
            "question": question
        })

    channel_name = f"llm_response:{task_id}"

    print("begin reply to api server")

    await redis_client.publish(channel_name, json.dumps({"event": "start", "data": "begin reply"}))

    full_response = ""
    try:
        for chunk in ds_llm.stream(prompt):
            if should_stop:
                print("stop llm stream")
                await redis_client.publish(channel_name, json.dumps(
                    {"event": "interrupted", "data": "generation was interrupted by user"}))
                break

            full_response += chunk.content

            await redis_client.publish(channel_name, json.dumps({"event": "message", "data": chunk.content}))

            # 给检查停止信号的任务一个执行的机会
            await asyncio.sleep(0)

        if not should_stop:
            message_histories[conversation_id].add_ai_message(full_response)
            while len(message_histories[conversation_id].messages) > 10:
                message_histories[conversation_id].messages.pop(0)
                message_histories[conversation_id].messages.pop(0)

    except Exception as e:
        await redis_client.publish(channel_name, json.dumps({"event": "error", "data": str(e)}))
    finally:
        await redis_client.publish(channel_name, json.dumps({"event": "end", "data": "end reply"}))

        # 取消监听任务
        if not stop_task.done():
            stop_task.cancel()
            try:
                await stop_task
            except asyncio.CancelledError:
                pass

        await pubsub.unsubscribe()

        print("reply end")
