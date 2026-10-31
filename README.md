# GDF - Graph Development Frame

一个极简的、事件驱动的 Python 异步插件框架，基于 asyncio 构建。入口只做组装，插件靠一个装饰器自动注册，零配置文件解析器、零容器、零元类。三层解耦：连接层收发原始帧，协议层直映射强类型事件，分发层逐插件隔离异常，单个插件崩溃不拖垮主进程。断线走指数退避与会话恢复，插件侧可自带频控与持久化。核心依赖只有 aiohttp 与 loguru。

QQ 机器人（`core/connector/ws.py` + `core/api/client.py` + `plugins/`）是本框架的一个官方实现与用例，不是框架本身。框架核心只定义"连接 → 协议映射 → 事件分发 → 插件执行"这条链路，任何平台都能按同一契约接入。

MIT License。

## 依赖与启动

```
cd ~/gdf
pip install -r requirements.txt
python main.py
```

依赖仅两项：`aiohttp`、`loguru`。Python 3.11+。

## 配置

复制模板后填入自己的凭据，`config.toml` 已被忽略，不会入库：

```
cp config.example.toml config.toml
```

```toml
app_id = "your_app_id"
app_secret = "your_app_secret"
sandbox = true
mode = "ws"

llm_api_key = "your_api_key"
llm_base_url = "https://api.deepseek.com/v1"
llm_model = "deepseek-chat"

llm_temperature = 0.7
llm_top_p = 0.9
llm_max_tokens = 1024
llm_context_limit = 10
llm_thinking = "disabled"
llm_reasoning_effort = "medium"
llm_json_mode = false
llm_logprobs = false
```

`app_id` / `app_secret` 取自 QQ 开放平台。`sandbox = true` 走沙箱基址，上线前改 `false`。`llm_*` 供 LLM 插件使用，缺项按代码内默认值兜底。

## 编写插件

在 `plugins/` 下新建 `.py` 文件即可，无需注册。给函数打上 `__event__` 属性，框架启动时自动扫描并挂载。签名固定为 `async def handler(event, bot)`。

`plugins/hello.py`：

```python
from collections.abc import Awaitable, Callable

from core.bot import Bot
from core.protocol.events import GroupAtMessage

Handler = Callable[..., Awaitable[None]]


def on_group_at(func: Handler) -> Handler:
    setattr(func, "__event__", "GROUP_AT_MESSAGE_CREATE")
    return func


@on_group_at
async def hello(event: GroupAtMessage, bot: Bot) -> None:
    if event.content.strip() != "hi":
        return
    await bot.reply(event, "hello")
```

可用事件：`GROUP_AT_MESSAGE_CREATE`（群 @）、`C2C_MESSAGE_CREATE`（单聊）。`bot.reply(event, 文本)` 自动按事件类型走群或单聊发送。需要更底层的调用时用 `bot.api.post_group_message()` / `bot.api.post_c2c_message()`。

插件内抛异常不会影响框架：分发层捕获、记 ERROR 堆栈、继续处理下一个插件。

## 提示词文件

```
plugins/prompt_system.txt   系统提示词
plugins/prompt_world.txt    世界书设定
```

两者在首次请求时读取并缓存，以空行拼接为单条 system 消息，修改后需重启生效。文件缺失或为空不影响运行，只是没有 system 消息。两文件名已在 `.gitignore` 中，不会入库。

## 命令行参数

```
python main.py                 # 默认模式，挂载 GDF > 控制台
python main.py --no-input      # 无头模式，不挂载控制台，日志只进文件
python main.py --log           # 日志查看模式，只读，不启动机器人
```

`--no-input` 适合 systemd、Termux 后台、nohup 等无人值守场景，Ctrl+C 仍触发优雅退出并打印 `机器人已退出`。

`--log` 独立于机器人逻辑：不读 `config.toml`、不初始化 ApiClient、不连接 QQ。先打印当天 `info` 日志末 50 行，随后每 0.5 秒轮询新增内容实时输出，跨零点自动切换到新文件。Ctrl+C 只退出查看模式。

## 实时查看日志

```
cd ~/gdf
python main.py --log
```

无需另开 tail，也可直接用系统命令：

```
tail -f logs/info_$(date +%F).log
tail -f logs/error_$(date +%F).log
```

## 智能唤醒策略

群里不加区分地回复会刷屏，用 `llm_reply_mode` 控制何时开口：

```toml
llm_reply_mode = "smart"                        # smart / always / keyword
llm_wake_keywords = ["诺喵莉", "喵莉"]           # keyword 模式的触发词
llm_eval_prompt = "你需要判断是否需要回复这句话。只输出 YES 或 NO。"
```

| 模式 | 行为 |
|---|---|
| `always` | 群内每条 @ 都回复 |
| `keyword` | 内容含任一唤醒词才回复，零额外请求 |
| `smart` | 先发一次极小请求（`max_tokens=8`）让模型判定，返回 YES 才回复 |

私聊不受该开关影响，始终回复。`smart` 的判定请求不进历史、不占频控配额，失败按「不回复」处理。空白内容与 `echo ` / `ping` 命令式消息在判定前即被拦截。

## 多模态图片识别

`llm_vision_enabled = true` 时，消息中的图片会被下载并转成 Base64 Data URL 随正文一起发出。

```toml
llm_vision_enabled = true
```

流程：从事件的 `attachments` 中筛出 `content_type` 为 `image/*` 的项，用框架共享连接池下载，单张上限 5MB，超出则忽略并记 WARNING，转码后构造标准 `image_url` 段，多张依次追加。

注意：QQ 图片链接带 `rkey` 时效签名，无法直接透传给第三方服务，因此必须下载转码。使用前请确认 `llm_model` 为多模态模型（如 `gpt-4o`、`qwen-vl-max`），`deepseek-chat` 等纯文本模型收到图片段会返回 400，此时把 `llm_vision_enabled` 置 `false`。

## MCP 长期记忆（可选）

`mcp_enabled = true` 时，`plugins/mcp_server.py` 会在 `127.0.0.1:8080/mcp` 起一个 MCP-lite 服务，向模型暴露两个工具：

| 工具 | 作用 |
|---|---|
| `remember(key, content)` | 写入一条长期记忆 |
| `recall(key)` | 按 key 前缀检索，返回列表 |

配置：

```toml
mcp_enabled = false          # 默认关闭
mcp_host = "127.0.0.1"       # 只绑本地
mcp_port = 8080
mcp_token = ""               # 非空时校验 Bearer
```

架构：

- 服务端为原生 aiohttp + JSON-RPC 2.0，支持 `initialize` / `tools/list` / `tools/call`，未实现 SSE，标准 MCP 客户端（Claude Desktop 等）连不上。
- 记忆存于 `data/long_term_memory.json`，结构为 `{user_id: {"name": "...", "facts": [...]}}`，按发言人隔离个人档案，每人最多保留最近 20 条事实，写入走 lock + tmp + replace。
- `user_id` 与 `display_name` 由插件侧强制注入：`user_id` 为发言人 openid，`display_name` 取其末 6 位作为称呼。模型无法在 arguments 中伪造他人身份。
- 主流程命中回忆倾向词（「记得」「我是谁」「我上次」等）时，会把 `tool_choice` 临时提为 `required` 迫使模型调用 `recall`；若模型不支持该值，自动降级回 `auto` 重试。
- `recall` 结果回填给模型时会附带「请结合这些事实作答」的提示，避免模型拿到档案仍凭空作答。
- `llm_chat` 在启用时把工具定义注入 `payload["tools"]`，收到 `tool_calls` 后回调本服务，结果以 `role="tool"` 塞回上下文再次请求，最多 3 轮。
- 服务在首次请求时懒启动，进程退出时经 `bot.on_shutdown` 自动关闭。

升级提示：v2.0.0 起记忆结构由「流水账」改为「个人档案」，与旧版不兼容。升级前请手动删除 `data/long_term_memory.json`，或让程序忽略旧数据（旧条目不会报错，但也不会出现在档案里）。

启用前请先确认端口未被占用：

```
curl -s -X POST http://127.0.0.1:8080/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## 数据与日志

```
data/history.json           对话历史，按会话隔离，自动裁剪到最近 llm_context_limit 轮
logs/info_YYYY-MM-DD.log    INFO 及以上，保留 7 天
logs/error_YYYY-MM-DD.log   ERROR，保留 7 天
```

`data/` 与 `logs/` 均为运行时生成，不入库。清空对话记忆直接删除 `data/history.json`。

## 目录

```
main.py                 入口，组装组件并拉起事件循环
core/                   框架核心，插件不修改此目录
plugins/                业务插件区
config.toml             本地配置，不入库
config.example.toml     配置模板
PRINCIPLES.md           项目原则
```