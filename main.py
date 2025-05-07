from contextlib import asynccontextmanager
import json
import uuid
from ext_redis import redis_client
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
from tasks import process_query_llm_task
from ext_taskiq import broker
from taskiq import TaskiqDepends
from typing import Annotated


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("start broker")
    if not broker.is_worker_process:
        await broker.startup()
    yield
    print("stop broker")
    if not broker.is_worker_process:
        await broker.shutdown()


app = FastAPI(
    title="AI-Tutor",
    lifespan=lifespan
)


@app.get("/")
def ping():
    return {
        "code": 0,
        "message": "ok",
        "status": 200
    }


@app.websocket("/ws/llm")
async def ws_handler(websocket: Annotated[WebSocket, TaskiqDepends()]):
    await websocket.accept()

    active_tasks = {}

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "question":
                question = data.get("question")
                task_id = str(uuid.uuid4())

                print("execute taskiq task[process_query_llm_task]")
                task_result = await process_query_llm_task.kiq(question, task_id)
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
