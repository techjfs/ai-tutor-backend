# 项目简介

## 特性
1. 使用DeepSeek R1大模型
2. LLM生成结果中，用户可中断生成任务
3. 不是AI相关的问题，回复“不知道”
4. 

# 演示

# 依赖项
+ redis
+ taskiq
+ fastapi
+ langchain

# 部署
0. 启动依赖服
```bash
docker run -itd -p 6379:6379 redis
```

1. 启动worker
```bash
taskiq worker ext_taskiq:broker -fsd -w 2
```

2. 启动FastAPI
```bash
python main.py
```
   

