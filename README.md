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
pyclaw serve --channels web wechat --provider agnes --model "agnes-2.0-flash" --port 12321 --host 0.0.0.0
```

| Option | Default | Description |
|--------|---------|-------------|
| `--channels` | `web` | IM channels to enable (`web`, `qq`, `wechat`) |
| `--provider` | `agnes` | AI model provider |
| `--model` | `agnes-2.0-flash` | Model name |
| `--port` | `12321` | HTTP server port |
| `--host` | `127.0.0.1` | Bind address |
| `--log-level` | `INFO` | Logging level |

### pyclaw channel rebind

Rebind (re-authenticate) an IM channel:

```shell
pyclaw channel rebind wechat
```

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
