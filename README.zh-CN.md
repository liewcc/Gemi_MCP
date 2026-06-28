# Gemi_MCP

[English](README.md) | **简体中文**

用浏览器自动化驱动 **Gemini 和 DeepSeek 网页版** —— **无需 API Key，无需付费**。它会在真实浏览器里
登录你平时用的账号，自动帮你处理对话、附件和设置。

它由两部分组成：

1. **引擎 + 控制面板** —— 一个小程序（文字界面的控制台，简称 "TUI"），负责打开浏览器、登录并运行自动化。
2. **MCP 服务器** —— 可选的桥梁，让 **Claude Code**、**Cursor** 这类 AI 助手能自动把任务交给 Gemini 或 DeepSeek 处理。

> 你可以只用第 1 部分。第 2 部分只有在你想让别的 AI 工具来控制它时才需要。

---

## ✅ 开始之前（环境要求）

在 **Windows** 上你只需要准备两样东西：

| 需要什么 | 用来做什么 | 怎么检查 |
|----------|------------|----------|
| **Python 3.10 或更新版本** | 运行本程序 | 打开终端，输入 `python --version` |
| **一个 Google 账号** | 用来登录 Gemini | 稍后在程序里登录 |

### 安装 Python（如果还没装）

1. 打开 <https://www.python.org/downloads/>
2. 下载最新的 Windows 安装包并运行。
3. **重要：** 在第一个安装界面，先勾选 **“Add python.exe to PATH”**，再点 *Install Now*。如果忘了勾，下面的命令都会用不了。
4. 装好后，**重新打开**一个终端，运行 `python --version`，应该会看到类似 `Python 3.12.x` 的字样。

---

## 🚀 安装（只需一次）

1. **下载本项目。**
   - 最简单的方法：在 GitHub 页面点绿色的 **`< > Code`** 按钮 → **Download ZIP**，然后解压到一个简单的路径，比如 `D:\Gemi_MCP`。
   - 或者，如果你会用 Git：`git clone <仓库地址>`

2. **打开**刚解压出来的文件夹。

3. **双击 `setup.bat`。**
   会弹出一个黑色窗口，自动安装所有东西：
   - Python 依赖库（Playwright、FastAPI 等）
   - 自动化要驱动的 Chromium 浏览器
   - 运行所需的文件夹
   - **自动注册 MCP**：脚本会自动检测并把 `gemi-mcp` 注册到 **Claude Code** 以及任何已安装的 **Antigravity** 产品（CLI、桌面版、IDE）中 —— 无需手动修改配置文件。

   第一次会花几分钟。当你看到 **`Setup complete!`** 就表示装好了，可以关掉窗口。

> 💡 如果 `setup.bat` 报了关于 `pip` 或 `playwright` 的错误，几乎都是因为 Python 没加到 PATH。请按上面的说明、勾选 **“Add to PATH”** 重新安装 Python，再运行一次 `setup.bat`。

---

## ▶️ 运行 & 首次登录

1. **双击 `run.bat`。**
   终端里会打开一个控制面板 —— 这就是你开关各项功能的地方。

2. **添加你的账号：**
   - 在 **`Engine`** 标签页（第一个界面），点击 **`📋 Add profile`**。
   - 会**弹出一个真实的浏览器窗口**，默认打开 Gemini 页面。
   - 在该窗口中**登录你的 Google 账号**（就像平时一样）。等待 Gemini 完全加载完毕。
   - 在**同一个浏览器窗口**中，导航到 <https://chat.deepseek.com> 并登录你的 DeepSeek 账号。如果出现验证步骤，请手动完成。
   - 确认 Gemini 和 DeepSeek 都已登录后，**关闭该浏览器窗口**。
   - 回到控制面板，按 **`Ctrl+R`** 重新加载。你的账号现在就会出现在列表中。

3. 搞定。两个服务的登录状态都会保存到你的浏览器配置文件中 —— 除非服务把你登出，否则无需再次登录。

### 🤖 多标签页架构 (Multiple-Tab Architecture) 与切换

Gemi_MCP 已升级为**多标签页架构**。在 TUI 的 ENGINE OPERATIONS 面板中，最多可勾选 2 个服务在启动时预热（默认：Gemini + DeepSeek）；其余服务在首次 MCP 调用时自动开启标签页。

#### ✨ 核心优势
- **毫秒级切换：** 服务切换通过 Playwright 的 `bring_to_front()` 在毫秒级内完成，彻底告别了旧单标签页模式下 5-15 秒的页面重载延迟。
- **状态完美保留：** 两个服务的 DOM 状态、聊天历史 and 设置均会被保留在各自的标签页中，切换服务商时不再丢失任何上下文。
- **真正并发执行：** 每个服务商在后台绑定独立的页面引用（`_page_ref`），使得请求可以安全地路由到任意服务，避免了异步交叉感染 Bug。

#### ⚙️ 工作原理
- **并发启动：** 引擎在启动时使用 `asyncio.gather()` 并发打开并预热 Gemini 和 DeepSeek 标签页，共用同一个浏览器上下文。
- **参数支持：** 所有主要的 MCP 工具（`send_chat`、`new_chat`、`apply_settings`、`download_images`、`redo_response`、`discover_capabilities`）现均支持一个可选的 `service` 参数（可用值：`"gemini"` 或 `"deepseek"`）。
- **默认机制：** 若省略 `service` 参数，请求将默认路由至当前活动的默认服务（可通过 `switch_service` 工具变更）。

### 🔐 DeepSeek 验证 —— 浏览器弹出时该怎么做

DeepSeek 具有严格的机器人检测机制，因此可能需要对每个会话进行验证。发生这种情况时，Gemi_MCP 会自动打开一个浏览器窗口并暂停，直到你完成验证。

**详细步骤：**

1. **浏览器窗口打开。** 这意味着 DeepSeek 需要手动验证（通常是 Cloudflare 挑战或登录提示）。
2. **在浏览器中完成验证** —— 解决验证挑战或登录，直到进入正常的 DeepSeek 聊天页面（能看到文本输入框）。
3. **告诉你的 AI 助手：** *"Test if the DeepSeek chat box is ready."* (测试 DeepSeek 输入框是否就绪)
   助手会调用 `new_chat()` 并确认会话是否处于活动状态。
4. **如果测试通过：** 关闭浏览器窗口。
5. **在 TUI 控制面板中**，在 Engine 标签页点击 **`Start Browser`**，以无头（后台）模式重新启动浏览器。
6. **让你的 AI 助手再次测试。** 这一次它应该可以在不弹出任何浏览器窗口的情况下正常工作 —— 确认无头模式已激活且会话有效。
7. **准备就绪。** 接下来可以正常使用 MCP 了。

> 💡 你只需要在 DeepSeek 触发验证时执行此操作。如果之前的会话依然有效，Gemi_MCP 将会在后台静默连接。

### 日常使用

- 直接双击 **`run.bat`** 即可。引擎会自动启动并登录。
- 底部状态栏显示 **`● online`** 就表示引擎已就绪。
- 按 **`q`** 退出。直接关窗口也会干净地把引擎一起关掉。

控制面板的各个标签页可以修改模型、输出文件夹、图片设置、自动化循环，以及切换账号。

### 💡 提示词示例 (如何让你的 AI 助手调用)

由于引擎向外暴露了标准的 MCP 工具，你可以用自然的语言指示你的 AI 助手（如 Claude Code 或 Antigravity）来调用 Gemi_MCP 驱动的浏览器自动化。

下面是一些实际的提示词使用案例，以及它们在后台被 AI 助手转换为 MCP 工具调用的方式：

* **案例 1：修改模型与设置**
  * *你的输入：* `"叫 gemi 调用 Gemini 3.5 Flash (High) extended，查找……"`
  * *AI 的转换：* 调用 `apply_settings(model="3.5 Flash", thinking_level="High", service="gemini")`，接着调用 `send_chat(prompt="查找的内容...", service="gemini")`。
* **案例 2：利用双标签页 (Dual-Tab) 架构进行对比/协同**
  * *你的输入：* `"叫 gemi 调用 dual tab，做这件事……"`（例如：“叫 gemi 调用 dual tab 同时向 Gemini 和 DeepSeek 询问为什么天空是蓝色的，并整理出两者的异同”）
  * *AI 的转换：* 分别并发或顺序调用 `send_chat(prompt="...", service="gemini")` 和 `send_chat(prompt="...", service="deepseek")`，最后由 AI 助手将两个返回的结果进行对比并呈现给您。
* **案例 3：定向免状态路由**
  * *你的输入：* `"叫 gemi 调用 deepseek，做这件事……"`（例如：“叫 gemi 调用 deepseek 写一段 python script 爬取网页数据”）
  * *AI 的转换：* 直接调用 `send_chat(prompt="写一段 python script...", service="deepseek")` 路由到 DeepSeek 标签页，这不会中断或重置你当前处于活跃状态的 Gemini 会话。

---

## 🔌 （可选）接入 Claude Code / Cursor / Antigravity

这一步能让别的 AI 助手通过 Gemi_MCP 把任务发送给 Gemini 或 DeepSeek。

### ⚡ 自动设置（推荐）

`setup.bat` 会自动将 `gemi-mcp` 注册到 **Claude Code** 和任何已安装的 **Antigravity** 产品中。如果你在安装过程中批准了这些步骤，你现在就已经连接好了。

### 🛠️ 手动配置（Cursor 及其他客户端）

把下面这段加到你的 MCP 客户端配置文件中。将路径替换为**你自己**的项目文件夹：

```json
{
  "mcpServers": {
    "gemi-mcp": {
      "command": "python",
      "args": ["D:\\Gemi_MCP\\mcp\\server.py"]
    }
  }
}
```

> Windows 路径里要使用**双反斜杠**（`\\`），如上所示。

### 🚀 使用方法

在你的 AI 客户端发起任何请求之前，请确保 `run.bat` 正在运行（显示 **`● online`**）。你的助手现在获得了诸如 `send_chat`、`switch_service`、`attach_files` 和 `get_last_response` 等工具。

---

## 🛠️ 常见问题排查

| 问题 | 解决办法 |
|------|----------|
| 提示 `'python' is not recognized` | Python 没加到 PATH。重新安装 Python 并勾选 **“Add to PATH”**，开一个新终端再试。 |
| `setup.bat` 在 `playwright install` 这一步失败 | 在项目文件夹里手动运行：`python -m playwright install chromium` |
| 控制面板一直显示 `○ offline` | 启动后等 10～20 秒（浏览器正在登录）。如果一直离线，在 Engine 标签页点击 **`Stop Browser`** 然后点击 **`Start Browser`**。 |
| 每次都要重新登录 | 可能是 Google 把你退出了。重新执行 **Add account (registration mode)** 确保你保持登录状态/勾选了“记住我”。 |
| MCP 客户端连不上 | 确保 `run.bat` 已打开并显示 **`● online`**，再启动你的 AI 客户端。 |
| 使用 DeepSeek 时弹出有头（可见）浏览器窗口 | DeepSeek 需要验证或重新登录。请在弹出的浏览器窗口中完成操作，然后重试你的请求。 |
| 想彻底重来 | 删掉 `data\` 文件夹和 `runtime\browser_user_data\` 文件夹，重新运行 `setup.bat` 并再次登录。 |

---

## 📁 程序会在你电脑上生成什么

下面这些都是自动生成的，**只存在你本机** —— 永远不会被上传：

- `data\` —— 你的设置和账号列表
- `runtime\browser_user_data\` —— 你已登录的浏览器配置（cookie）
- `gemini_outputs\` —— 默认保存生成图片的地方

> ⚠️ 千万不要分享这些文件夹 —— 里面包含你的登录会话。

---

## ℹ️ 说明与限制

- 本工具自动化的是 Gemini 的**免费网页版**，受 Gemini 正常的使用额度和服务条款约束。请用你自己的账号、合理使用。
- 目前在 **Windows** 上构建和测试。
- 这是一个非官方工具，**与 Google 无关**。
