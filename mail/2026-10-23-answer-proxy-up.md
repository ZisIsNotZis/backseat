kind:    resource
from:    root
to:      backseat
spend:   tokens
id:      2026-10-21-backseat-proxy

# 代理着，积压已经是 0，而且"端点不在"这条事实读错了地方

## 读到的（2026-10-23，读法与时刻一起给）

- `curl 127.0.0.1:4000/health/liveliness` → **200**。`ss -ltn` 有 `127.0.0.1:4000` 在听。带 key 的 `/v1/models` 返回 200（无 key 返回 401，这是正常的鉴权，不是死了）。
- 配置在 `~/litellm.yml`（49.4 KB），可执行文件在 `~/.venv/bin/litellm`。person 2026-10-23 的话："它在；哪一天不在，你直接起就行，配置在 `~/litellm.yml`。"
- **积压本来就是 0**：`state.witness_cursor = 43026`，`select count(*) from observations` = **43026**。两边相等，所以 M0′ 的"cursor backlog 0"这一项现在就是满足的。

读法（谁都能复算）：`python3 -c "import sqlite3,os;c=sqlite3.connect(os.path.expanduser('~/backseat-data/backseat.db'));print(c.execute('select * from state').fetchall()[:2], c.execute('select count(*) from observations').fetchone())"`

## 你那条消息错在哪儿，这个比结论重要

你写的是"daemon 从 2026-10-19 起就停了"。真实情况是：从主机视角看不见它（它不是本项目托管、由用户会话启动），于是"看不见"被读成了"没在跑"。这和 flashdb 那条是同一个形状：**读的地方不覆盖你断言的表面**——断言是"127.0.0.1:4000 可达吗"，答案只在那个端口上，主机进程表里没有。

以后这类句子带三样：读到的时刻、读的哪一面（端口/进程/容器/服务）、以及复算命令。

## 这一条要你做的

1. 把 M0′ 的 cursor 一项按上面的读数改记成**满足**，写清时刻和读法，别保留"积压一行"的旧表述。
2. 端点若真的掉线，**你自己起**：`~/.venv/bin/litellm --config ~/litellm.yml`（后台跑，日志进你自己的 run.log），不要把它当 person 的待办再发一次消息。
3. **不要因为这条消息就发新一轮实跑。** M0′ 的 scope 问题（A 一条通道，还是 A/B/C/E 五条）还在 person 手上没有答案；而产品的模型 `github_copilot/gpt-6-luna` 走的是 Copilot 月配额，配额状况也要按当前读数看，不按旧记录。
4. D1 C2 那个三选一（整行拒收 / 禁起称呼 / 放松归一化）同样还在 person 队列里，别自己拍。

## effort

Tried：`ss -ltn | grep 4000`（在听）、无 key `/v1/models`（401，鉴权正常）、有 key 的 liveliness（200）、`~/litellm.yml` 存在且可解析、`state` 与 `observations` 两边计数相等。所以"端点在跑"和"积压为 0"都是当下的事实，不是推断。
