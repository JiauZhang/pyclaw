# PyClaw

PyClaw is a personal AI assistant built on [chatchat](https://github.com/jiauzhang/chatchat) and [imchat](https://github.com/jiauzhang/imchat).

## Installation

```shell
pip install pyclaw
```

## Quick Start

Start the server:

```shell
pyclaw serve
```

Open your browser and navigate to:

```
http://127.0.0.1:12321/chat
```

## Command Line Options

```shell
pyclaw serve --channels web wechat --provider agnes --model "agnes-2.5-flash" --port 12321 --host 0.0.0.0
```

| Option | Default | Description |
|--------|---------|-------------|
| `--channels` | `wechat` | IM channels to enable (`web`, `qq`, `wechat`) |
| `--provider` | `agnes` | AI model provider |
| `--model` | `agnes-2.5-flash` | Model name |
| `--port` | `12321` | HTTP server port |
| `--host` | `127.0.0.1` | Bind address |
| `--log-level` | `INFO` | Logging level |

### pyclaw channel rebind

Rebind (re-authenticate) an IM channel:

```shell
pyclaw channel rebind wechat
```

## Private Tools & Skills (Plugins)

Private tools/skills can be injected without touching pyclaw's source, via
[entry points](https://packaging.python.org/en/latest/specifications/entry-points/).
Create a private package that declares two entry point groups:

```toml
[project.entry-points."pyclaw.tools"]
mypriv = "mypriv"

[project.entry-points."pyclaw.skills"]
mypriv = "mypriv"
```

The module must expose `tools` (a list of `chatchat.tool.Tool`) and/or `skills`
(a list of SKILL.md root directories):

```python
from chatchat.tool import tool

@tool(name='secret_lookup', description='...', parameters={'type': 'object', 'properties': {'key': {'type': 'string'}}, 'required': ['key']})
def secret_lookup(key):
    return get_secret(key)

tools = [secret_lookup]
skills = ['/abs/path/to/my/skill/roots']
```

After `pip install` (a local `pip install -e .` works), pyclaw discovers them
at startup and injects them into the per-session Team's sub-agents. The leader
instruction is auto-generated to include the discovered tool names.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Server info |
| `/v1/status` | GET | Detailed runtime status |
| `/v1/{method}` | POST | RPC endpoint |
| `/ws` | WebSocket | General WebSocket connection |
| `/chat/ws` | WebSocket | WebChat streaming connection |
| `/chat` | GET | WebChat UI |

## Sponsor

<table align="center">
    <thead>
        <tr>
            <th colspan="2">公众号</th>
        </tr>
    </thead>
    <tbody align="center" valign="center">
        <tr>
            <td colspan="2"><img src="https://jiauzhang.github.io/ghstatic/images/ofa_m.png" style="height: 196px" alt="AliPay.png"></td>
        </tr>
    </tbody>
    <thead>
        <tr>
            <th>AliPay</th>
            <th>WeChatPay</th>
        </tr>
    </thead>
    <tbody align="center" valign="center">
        <tr>
            <td><img src="https://jiauzhang.github.io/AliPay.png" style="width: 196px; height: 196px" alt="AliPay.png"></td>
            <td><img src="https://jiauzhang.github.io/WeChatPay.png" style="width: 196px; height: 196px" alt="WeChatPay.png"></td>
        </tr>
    </tbody>
</table>
