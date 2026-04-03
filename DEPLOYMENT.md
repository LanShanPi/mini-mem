# MiniMem 生产环境部署指南

本文档说明如何将 MiniMem 部署到生产环境。

---

## 📋 前提条件

- 服务器：至少 2 核 4GB 内存（推荐 4 核 8GB）
- Docker 和 Docker Compose 已安装
- 域名（如果需要 HTTPS）
- SSL 证书（可使用 Let's Encrypt 免费证书）

---

## 🚀 快速部署

### 1. 克隆项目并配置

```bash
cd /path/to/mini_mem

# 复制环境变量模板
cp .env.example .env 2>/dev/null || true

# 编辑配置
vim .env
```

**必须配置的环境变量**：

```bash
# Neo4j 密码（必须修改）
NEO4J_PASSWORD=your_secure_password_here

# API 密钥（可选，用于认证）
MINIMEM_API_KEY=your_api_key_here

# LLM 配置
LLM_API_BASE=https://your-llm-gateway.com/v1
LLM_API_KEY=your_llm_api_key
LLM_MODEL=your-model-name

# Grafana 管理员密码
GRAFANA_ADMIN_PASSWORD=your_grafana_password
```

### 2. 生成 SSL 证书

**选项 A：自签名证书（测试用）**

```bash
./generate-self-signed-cert.sh
```

**选项 B：Let's Encrypt 正式证书（生产推荐）**

```bash
# 使用 certbot
apt install certbot -y
certbot certonly --standalone -d your-domain.com

# 复制证书到项目目录
cp /etc/letsencrypt/live/your-domain.com/fullchain.pem ./ssl/
cp /etc/letsencrypt/live/your-domain.com/privkey.pem ./ssl/
```

### 3. 修改 nginx.conf

编辑 `nginx.conf`，将 `server_name` 改为你的域名：

```nginx
server_name your-domain.com;
```

### 4. 启动服务

```bash
# 启动所有服务
docker-compose -f docker-compose.prod.yml up -d

# 查看日志
docker-compose -f docker-compose.prod.yml logs -f

# 检查服务状态
docker-compose -f docker-compose.prod.yml ps
```

---

## 🔍 访问各服务

| 服务 | 地址 | 说明 |
|------|------|------|
| Web API | https://your-domain.com | 主服务（HTTPS） |
| Grafana | https://your-domain.com/grafana | 监控仪表盘 |
| Prometheus | https://your-domain.com/prometheus | 原始监控数据 |
| Neo4j Browser | http://localhost:7474 | 仅内网访问 |

---

## 📊 监控配置

### Grafana 仪表盘

启动后访问 `https://your-domain.com/grafana`：
- 用户名：`admin`
- 密码：你在 `.env` 中配置的 `GRAFANA_ADMIN_PASSWORD`

预置仪表盘包含：
- API 请求量和响应时间
- Neo4j 节点/边数量
- 服务健康状态
- 记忆存储/检索速率
- 错误统计

### 日志查看

```bash
# 应用日志
docker-compose -f docker-compose.prod.yml logs -f minimem

# Nginx 日志
docker-compose -f docker-compose.prod.yml logs -f nginx

# 或直接查看文件
tail -f logs/minimem/error.log
tail -f logs/nginx/error.log
```

---

## 🔧 日常运维

### 备份数据

```bash
# 手动触发备份
curl -X POST https://your-domain.com/api/backup \
  -H "X-API-Key: your_api_key"

# 备份文件位置
ls -la backups/
```

### 导出数据

```bash
# 导出完整记忆图
curl https://your-domain.com/api/export \
  -H "X-API-Key: your_api_key" \
  -o backup_$(date +%Y%m%d).json
```

### 服务重启

```bash
# 重启单个服务
docker-compose -f docker-compose.prod.yml restart minimem

# 重启所有服务
docker-compose -f docker-compose.prod.yml restart
```

### 升级版本

```bash
# 拉取最新代码
git pull

# 重新构建并重启
docker-compose -f docker-compose.prod.yml up -d --build
```

---

## 🛡️ 安全建议

### 1. 防火墙配置

```bash
# 仅开放必要端口
ufw allow 80/tcp    # HTTP（用于证书验证）
ufw allow 443/tcp   # HTTPS
ufw allow 22/tcp    # SSH
ufw enable
```

### 2. 修改默认密码

确保修改以下默认值：
- `NEO4J_PASSWORD`
- `GRAFANA_ADMIN_PASSWORD`
- `MINIMEM_API_KEY`

### 3. 定期备份

建议添加 cron 任务：

```bash
# 每天凌晨 2 点备份
0 2 * * * curl -X POST https://your-domain.com/api/backup -H "X-API-Key: your_api_key"
```

### 4. 日志轮转

已配置 Nginx 和 Grafana 日志，建议添加 logrotate：

```bash
# /etc/logrotate.d/minimem
/path/to/mini_mem/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
}
```

---

## 📈 性能调优

### Neo4j 内存配置

编辑 `docker-compose.prod.yml` 中的 Neo4j 环境变量：

```yaml
environment:
  - NEO4J_dbms_memory_heap_initial__size=2G  # 根据服务器调整
  - NEO4J_dbms_memory_heap_max__size=4G
  - NEO4J_dbms_memory_pagecache_size=1G
```

### Gunicorn 工作进程

编辑 `Dockerfile.prod`：

```bash
-w 2          # CPU 核心数 × 2
--threads 4   # 每个工作进程的线程数
```

### Nginx 限流

编辑 `nginx.conf`：

```nginx
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;  # 根据需求调整
```

---

## 🆘 故障排查

### 服务无法访问

```bash
# 检查容器状态
docker-compose -f docker-compose.prod.yml ps

# 查看日志
docker-compose -f docker-compose.prod.yml logs minimem

# 检查端口占用
netstat -tlnp | grep :8765
```

### Neo4j 连接失败

```bash
# 检查 Neo4j 是否健康
docker-compose -f docker-compose.prod.yml ps neo4j

# 查看 Neo4j 日志
docker-compose -f docker-compose.prod.yml logs neo4j
```

### 证书问题

```bash
# 检查证书有效期
openssl x509 -in ssl/fullchain.pem -noout -dates

# 重新生成证书
./generate-self-signed-cert.sh
```

---

## 📞 技术支持

遇到问题请查看：
- [DOCUMENTATION.md](DOCUMENTATION.md) - 完整设计文档
- [USAGE.md](USAGE.md) - 使用指南
- GitHub Issues - 提交问题报告
