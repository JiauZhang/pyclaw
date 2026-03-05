## Development
- 不要过度设计，保持代码简单和可维护
- 涉及到网络访问的操作统一使用 httpx 库
- 每个功能点都应该有对应的测试用例，包括正常情况和异常情况，使用 pytest/pytest-asyncio 框架

### 依赖项：chatchat

#### 简介

chatchat 是一个支持多种 provider 和 model 的 AI 助手库，提供了统一的接口来调用不同的 AI 模型。

#### 基本用法

```python
from chatchat import AI

provider = 'tencent'
model = 'hunyuan-lite'
timeout = None
proxy = None
ai = AI(provider, model=model, client_kwargs={
    'timeout': timeout,
    'proxy': proxy,
})

# completion mode
prompt = 'Hi'
response = ai.complete(prompt)
# model output
text = response if response.text is None else response.text
print(f'user> {prompt}\nassistant> {text}\n')

# chat mode
print('2. chat mode\n')
while True:
    prompt = input("user> ")
    if prompt == '\x04': # Ctrl+D
        break
    # ai will record the conversation history internally
    response = ai.chat(prompt)
    text = response if response.text is None else response.text
    print(f'assistant> {text}')

# completion mode with streaming
prompt = 'Generate 200 words to me about China.'
response = ai.complete(prompt, stream=True)
print(f'user> {prompt}\nassistant> ', end='')
for chunk in response:
    print(chunk.text, end="", flush=True)
print()

# clear conversation history
ai.clear()
print('\n4. stream chat mode\n')
while True:
    prompt = input("user> ")
    if prompt == '\x04': # Ctrl+D
        break
    response = ai.chat(prompt, stream=True)
    print('assistant> ', end='')
    for chunk in response:
        print(chunk.text, end="", flush=True)
    print()
```

## Test
- 如果你需改了代码且使用的是 python3 -m pyclaw 启动服务时，确保你最新修改的代码已经安装了，你可以使用 python3 -m pip install . 来安装
- 测试时使用 --provider tencent --model hunyuan-lite 这两个特定参数
