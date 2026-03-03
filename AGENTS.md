## Development
- 不要过度设计，保持代码简单和可维护
- 涉及到网络访问的操作统一使用 httpx 库
- 每个功能点都应该有对应的测试用例，包括正常情况和异常情况，使用 pytest/pytest-asyncio 框架

## Test
- 如果你需改了代码且使用的是 python3 -m pyclaw 启动服务时，确保你最新修改的代码已经安装了，你可以使用 python3 -m pip install . 来安装
- 测试时使用 --provider tencent --model hunyuan-lite 这两个特定参数
