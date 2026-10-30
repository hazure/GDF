# GDF

极简 QQ AI 机器人框架。入口程序拉起基础组件，插件按事件自动注册，插件出错只隔离自身，不拖垮主进程。

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