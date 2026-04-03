#!/bin/bash
# start_neo4j.sh - 启动 Neo4j Docker 容器

echo "🚀 启动 Neo4j..."

# 检查 Docker 是否运行
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker 未运行，请先启动 Docker"
    exit 1
fi

# 检查是否已有容器
if docker ps -a | grep -q neo4j; then
    echo "ℹ 发现已有 neo4j 容器"
    docker start neo4j
else
    echo "📦 创建新容器..."
    docker run -d \
        --name neo4j \
        -p 7474:7474 -p 7687:7687 \
        -e NEO4J_AUTH=neo4j/minimem123 \
        -e NEO4J_PLUGINS='["apoc"]' \
        neo4j:5
fi

# 等待 Neo4j 启动
echo "⏳ 等待 Neo4j 启动..."
sleep 5

# 检查是否成功
if docker ps | grep -q neo4j; then
    echo "✅ Neo4j 已启动！"
    echo ""
    echo "📍 访问地址:"
    echo "   - Browser: http://localhost:7474"
    echo "   - Bolt:    bolt://localhost:7687"
    echo ""
    echo "🔐 登录信息:"
    echo "   - 用户名：neo4j"
    echo "   - 密码：minimem123"
    echo ""
    echo "🛑 停止命令：docker stop neo4j"
else
    echo "❌ 启动失败，请检查 Docker 日志"
    docker logs neo4j --tail 20
fi
