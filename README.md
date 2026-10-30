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