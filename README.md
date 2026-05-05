# PyClaw

PyClaw is a personal AI assistant built on [chatchat](https://github.com/jiauzhang/chatchat).

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
pyclaw serve --provider openrouter --model "tencent/hy3-preview:free" --port 12321 --host 0.0.0.0
```

| Option | Default | Description |
|--------|---------|-------------|
| `--provider` | `openrouter` | AI model provider |
| `--model` | `tencent/hy3-preview:free` | Model name |
| `--port` | `12321` | HTTP server port |
| `--host` | `127.0.0.1` | Bind address |
| `--log-level` | `INFO` | Logging level |

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Server info |
| `/health` | GET | Health check |
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
