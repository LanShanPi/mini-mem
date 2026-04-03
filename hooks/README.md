# Git hooks for MiniMem

## 安装预提交钩子

```bash
# 复制预提交钩子
cp .git/hooks/pre-commit .git/hooks/pre-commit.bak 2>/dev/null || true
cp hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit

# 或者使用绝对路径（开发时）
ln -sf $(pwd)/hooks/pre-commit .git/hooks/pre-commit
```

## 钩子功能

预提交钩子会自动检查：

1. **.env 文件中是否有 API 密钥** - 拒绝提交包含 `LLM_API_KEY=sk-...` 的 .env
2. **api_key.local 是否被追踪** - 拒绝提交 api_key.local
3. **代码中是否有硬编码密钥** - 扫描暂存文件中的密钥模式

## 如果检查失败

1. 将密钥移到 `api_key.local` 文件
2. 或使用环境变量
3. 紧急情况下可跳过：`git commit --no-verify`（不推荐）
