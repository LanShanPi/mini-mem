FROM dockerproxy.net/library/python:3.10-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 创建备份目录
RUN mkdir -p backups

# 暴露端口
EXPOSE 8765

# 启动命令
CMD ["python", "-m", "uvicorn", "web_server:app", "--host", "0.0.0.0", "--port", "8765"]
