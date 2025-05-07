# 项目简介

# 演示

# 依赖项
+ redis
+ taskiq
+ fastapi
+ langchain

# 部署
0. 启动依赖服
```bash

```

1. 启动worker
```bash
taskiq worker ext_taskiq:broker -fsd
```

2. 启动FastAPI
```bash
python main.py
```
   

