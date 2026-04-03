# 实体提取器配置说明

MiniMem 支持三种实体提取方式，通过 `ENTITY_EXTRACTOR` 环境变量配置。

## 配置方式

在 `.env` 文件中设置：

```bash
ENTITY_EXTRACTOR=llm  # simple | llm | hybrid
```

## 三种模式对比

### 1. simple（规则匹配）

**特点：**
- ✅ 零依赖，无需 API
- ✅ 速度快，即时响应
- ❌ 精度低，无语义理解
- ❌ 无法识别复杂实体

**适用场景：**
- 开发测试
- 无网络环境
- 对精度要求不高

**示例：**
```
输入："张三在星巴克喝了美式，说今天好累"
输出：["张三", "星巴克", "美式", "今天", "好累"]
```

---

### 2. llm（大模型提取）⭐ 推荐

**特点：**
- ✅ 语义理解好，准确
- ✅ 能识别复杂概念和关系
- ❌ 需要 API，有调用成本
- ❌ 依赖网络

**适用场景：**
- 生产环境
- 对精度要求高
- 有稳定的 LLM 服务

**示例：**
```
输入："张三在星巴克喝了美式，说今天好累"
输出：["张三", "星巴克", "美式", "今天", "累"]
```

**配置：**
```bash
LLM_API_BASE=https://api.openai.com/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=gpt-4o-mini
```

---

### 3. hybrid（混合模式）

**特点：**
- ✅ 结合规则和 LLM 的优势
- ✅ 更全面的实体覆盖
- ❌ 调用两次，稍慢

**适用场景：**
- 需要最高召回率
- LLM 可能漏掉某些实体时

**工作原理：**
1. 先用规则提取明确实体（时间、数字等）
2. 再用 LLM 提取语义实体
3. 合并去重

---

## 测试提取效果

```bash
python test_llm_extract.py
```

会对比三种方式的提取结果。

---

## 自定义 LLM 服务

如果使用其他 LLM 服务（如 OpenAI、Claude、本地部署），修改配置即可：

```bash
# OpenAI
LLM_API_BASE=https://api.openai.com/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=gpt-4o-mini

# 本地部署（Ollama）
LLM_API_BASE=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=qwen2.5

# 火山引擎
LLM_API_BASE=https://ark.cn-beijing.volces.com/api/v3
LLM_API_KEY=xxx
LLM_MODEL=ep-xxx
```

---

## 性能优化建议

1. **批量存储**：一次性存多条记忆，减少 API 调用次数
2. **缓存结果**：对相同文本缓存提取结果
3. **降级策略**：LLM 失败时自动回退到规则匹配（已实现）
4. **异步调用**：生产环境可用异步方式调用 LLM
