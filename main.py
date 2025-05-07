from contextlib import asynccontextmanager
import json
import uuid
import asyncio
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
    # 创建一个事件字典来控制各个任务的监听循环
    stop_events = {}

    async def listen_for_redis_messages(task_id, stop_event):
        """监听特定任务的Redis消息"""
        pubsub = redis_client.pubsub()
        try:
            await pubsub.subscribe(f"llm_response:{task_id}")
            while not stop_event.is_set():
                # 使用带超时的方式获取消息，以便能检查stop_event
                message = await pubsub.get_message(timeout=0.1)
                if message and message["type"] == "message":
                    result = json.loads(message["data"])
                    await websocket.send_json({
                        "type": "llm_response",
                        "event": result.get("event"),
                        "data": result.get("data")
                    })

                    if result.get("event") in ["end", "interrupted", "error"]:
                        stop_event.set()  # 设置停止事件
                        if task_id in active_tasks:
                            del active_tasks[task_id]
                        break
                await asyncio.sleep(0.01)  # 避免CPU过度使用
        finally:
            await pubsub.unsubscribe()
            print(f"Unsubscribed from Redis channel for task {task_id}")

    try:
        # 创建接收WebSocket消息的主循环
        while True:
            # 使用带超时的方式接收消息
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
            except asyncio.TimeoutError:
                # 没有新消息，继续下一个循环
                continue

            if data.get("type") == "question":
                question = data.get("question")
                task_id = str(uuid.uuid4())

                # 启动任务
                task = await process_query_llm_task.kiq(question, task_id)
                active_tasks[task_id] = task

                # 创建停止事件
                stop_event = asyncio.Event()
                stop_events[task_id] = stop_event

                # 通知客户端任务已开始
                await websocket.send_json({
                    "type": "task_started",
                    "task_id": task_id
                })

                # 创建并启动监听Redis消息的任务
                asyncio.create_task(listen_for_redis_messages(task_id, stop_event))

            elif data.get("type") == "stop" and data.get("task_id") in active_tasks:
                print("receive stop message")
                task_id = data.get("task_id")

                # 停止任务
                if task_id in active_tasks:
                    del active_tasks[task_id]

                # 发送停止命令到Redis
                await redis_client.publish(
                    f"llm_control:{task_id}",
                    json.dumps({"command": "stop"})
                )

                # 设置停止事件，通知监听循环结束
                if task_id in stop_events:
                    stop_events[task_id].set()
                    del stop_events[task_id]

                await websocket.send_json({
                    "type": "command_sent",
                    "command": "stop",
                    "task_id": task_id
                })

                print("stop generation")

    except WebSocketDisconnect:
        print("WebSocket disconnected")
        # 清理所有活动任务
        for task_id, task_result in active_tasks.items():
            await redis_client.publish(
                f"llm_control:{task_id}",
                json.dumps({"command": "stop"})
            )
            # 设置停止事件
            if task_id in stop_events:
                stop_events[task_id].set()

        active_tasks.clear()
        stop_events.clear()


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