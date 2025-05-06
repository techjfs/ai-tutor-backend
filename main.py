import json
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
from langchain_community.chat_models import ChatOpenAI
from celery import Celery
import redis.asyncio as redis

from prompt import ai_learn_path_prompt_template

app = FastAPI()

celery_app = Celery(
    "llm_worker",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1"
)

redis_client = redis.Redis(host="localhost", port=6379, db=2)


@app.get("/")
def ping():
    return {
        "code": 0,
        "message": "ok",
        "status": 200
    }


@celery_app.task(bind=True)
async def process_query_llm_task(self, question, task_id):
    should_stop = False

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"llm_control:{task_id}")

    def check_stop_signal():
        nonlocal should_stop
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                if data.get("command") == "stop":
                    should_stop = True
                    return

    # TODO: 先用线程实现，后面判断要不要改成asyncio
    import threading
    stop_thread = threading.Thread(target=check_stop_signal)
    stop_thread.daemon = True
    stop_thread.start()

    prompt = ai_learn_path_prompt_template.invoke({"question": question})
    ds_llm = ChatOpenAI(
        model_name="deepseek-r1:1.5b",
        openai_api_base="http://localhost:11434/v1",  # 注意是 /v1
        openai_api_key="ollama",  # 随便写，不校验，但必须提供
    )

    channel_name = f"llm_response:{task_id}"

    await redis_client.publish(channel_name, json.dumps({"event": "start", "data": "begin reply"}))

    try:
        for chunk in ds_llm.stream(prompt):
            if should_stop:
                await redis_client.publish(channel_name, json.dumps(
                    {"event": "interrupted", "data": "generation was interrupted by user"}))
                break

            await redis_client.publish(channel_name, json.dumps({"event": "message", "data": chunk}))
    except Exception as e:
        await redis_client.publish(channel_name, json.dumps({"event": "error", "data": str(e)}))
    finally:
        await redis_client.publish(channel_name, json.dumps({"event": "end", "data": "end reply"}))
        await pubsub.unsubscribe()


@app.websocket("/ws/llm")
async def ws_handler(websocket: WebSocket):
    await websocket.accept()

    active_tasks = {}

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "question":
                question = data.get("question")
                task_id = str(uuid.uuid4())

                task_result = process_query_llm_task.delay(question, task_id)
                active_tasks[task_id] = task_result

                await websocket.send_json({
                    "type": "task_started",
                    "task_id": task_id
                })

                pubsub = redis_client.pubsub()
                await pubsub.subscribe(f"llm_response:{task_id}")
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        result = json.loads(message["data"])
                        await websocket.send_json({
                            "type": "llm_response",
                            "event": result.get("event"),
                            "data": result.get("data")
                        })

                        if result.get("event") in ["end", "interrupted", "error"]:
                            active_tasks.pop(task_id, None)
                            break
                await pubsub.unsubscribe()

            elif data.get("type") == "stop" and data.get("task_id") in active_tasks:
                task_id = data.get("task_id")
                task_result = active_tasks[task_id]

                if not task_result.ready():
                    task_result.revoke(terminate=True)

                await redis_client.publish(
                    f"llm_control: {task_id}",
                    json.dumps({"command": "stop"})
                )

                await websocket.send_json({
                    "type": "command_sent",
                    "command": "stop",
                    "task_id": task_id
                })

            elif data.get("type") == "check_status" and data.get("task_id") in active_tasks:
                # 新增功能：检查任务状态
                task_id = data.get("task_id")
                task_result = active_tasks[task_id]

                await websocket.send_json({
                    "type": "task_status",
                    "task_id": task_id,
                    "state": task_result.state,
                    "ready": task_result.ready(),
                    "successful": task_result.successful() if task_result.ready() else None
                })

    except WebSocketDisconnect:
        for task_id, task_result in active_tasks.items():
            if not task_result.ready():
                task_result.revoke(terminate=True)
            await redis_client.publish(
                f"llm_control:{task_id}",
                json.dumps({"command": "stop"})
            )
        active_tasks.clear()


def main():
    uvicorn.run(
        "main:app",  # 这里假设这个文件名为main.py
        host="0.0.0.0",
        port=8050,
        reload=True,  # 热重载，开发环境中很有用
        workers=1  # 工作进程数
    )


if __name__ == "__main__":
    main()
