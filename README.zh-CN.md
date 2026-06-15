# Gemi_MCP

[English](README.md) | **简体中文**

用浏览器自动化驱动 **Gemini 网页版** —— **无需 API Key，无需付费**。它会在真实浏览器里
登录你平时用的 Google 账号，帮你生成文字和图片。

它由两部分组成：

1. **引擎 + 控制面板** —— 一个小程序（文字界面的控制台，简称 "TUI"），负责打开浏览器、
   登录 Gemini、运行自动化。
2. **MCP 服务器** —— 可选的桥梁，让 **Claude Code**、**Cursor** 这类 AI 助手能自动把任务
   交给 Gemini 处理。

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
3. **重要：** 在第一个安装界面，先勾选 **“Add python.exe to PATH”**，再点 *Install Now*。
   如果忘了勾，下面的命令都会用不了。
4. 装好后，**重新打开**一个终端，运行 `python --version`，应该会看到类似
   `Python 3.12.x` 的字样。

---

## 🚀 安装（只需一次）

1. **下载本项目。**
   - 最简单的方法：在 GitHub 页面点绿色的 **`< > Code`** 按钮 → **Download ZIP**，
     然后解压到一个简单的路径，比如 `D:\Gemi_MCP`。
   - 或者，如果你会用 Git：`git clone <仓库地址>`

2. **打开**刚解压出来的文件夹。

3. **双击 `setup.bat`。**
   会弹出一个黑色窗口，自动安装所有东西：
   - Python 依赖库（Playwright、FastAPI 等）
   - 自动化要驱动的 Chromium 浏览器
   - 运行所需的文件夹

   第一次会花几分钟。当你看到 **`Setup complete!`** 就表示装好了，可以关掉窗口。

> 💡 如果 `setup.bat` 报了关于 `pip` 或 `playwright` 的错误，几乎都是因为 Python 没加到
> PATH。请按上面的说明、勾选 **“Add to PATH”** 重新安装 Python，再运行一次 `setup.bat`。

---

## ▶️ 运行 & 首次登录

1. **双击 `run.bat`。**
   终端里会打开一个控制面板 —— 这就是你开关各项功能的地方。

2. **登录 Gemini：**
   - 用方向键 / 鼠标切换到 **`Accounts`**（账号）标签页。
   - 按 **`+ Add account (registration mode)`**（添加账号 / 注册模式）。
   - 会**弹出一个真实的浏览器窗口** —— 像平时一样在里面登录你的 Google 账号。
   - 当 Gemini 在那个窗口里正常打开后，回到控制面板按 **`Ctrl+R`** 刷新。你的账号就会
     出现在列表里了。

3. 这样就好了。引擎下次会复用这个登录状态 —— 除非被 Google 退出登录，否则不用再登一次。

### 日常使用

- 直接双击 **`run.bat`** 即可。引擎会自动启动并登录。
- 底部状态栏显示 **`● online`** 就表示引擎已就绪。
- 按 **`q`** 退出。直接关窗口也会干净地把引擎一起关掉。

控制面板的各个标签页可以修改模型、输出文件夹、图片设置、自动化循环，以及切换账号。

---

## 🔌 （可选）接入 Claude Code / Cursor

这一步能让别的 AI 助手通过 Gemi_MCP 把任务发给 Gemini。

**前提：** 使用 MCP 服务器期间，`run.bat` 必须保持运行（这样引擎才在线）。

把下面这段加到你的 MCP 客户端配置里（Claude Code 是 `claude_desktop_config.json` /
`.mcp.json`；Cursor 也有类似的 MCP 设置文件）。把路径换成**你自己的**文件夹：

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

> Windows 路径里要用**双反斜杠**（`\\`），如上所示。

重启你的 AI 客户端。它现在就拥有了 `send_chat`、`set_prompt`、`submit_response`、
`download_images` 等工具，可以替它跟 Gemini 对话。

---

## 🛠️ 常见问题排查

| 问题 | 解决办法 |
|------|----------|
| 提示 `'python' is not recognized` | Python 没加到 PATH。重新安装 Python 并勾选 **“Add to PATH”**，开一个新终端再试。 |
| `setup.bat` 在 `playwright install` 这一步失败 | 在项目文件夹里手动运行：`python -m playwright install chromium` |
| 控制面板一直显示 `○ offline` | 启动后等 10～20 秒（浏览器正在登录）。如果一直离线，去 Engine 标签页按 **Restart** 按钮。 |
| 每次都要重新登录 | 可能是 Google 把你退出了。重新执行 **Add account (registration mode)**，登录时记得保持登录 / 勾选“记住我”。 |
| MCP 客户端连不上 | 确保 `run.bat` 已打开并显示 **`● online`**，再启动你的 AI 客户端。 |
| 想彻底重来 | 删掉 `data\` 文件夹和 `core\browser_user_data\` 文件夹，重新运行 `setup.bat` 并再次登录。 |

---

## 📁 程序会在你电脑上生成什么

下面这些都是自动生成的，**只存在你本机** —— 永远不会被上传：

- `data\` —— 你的设置和账号列表
- `core\browser_user_data\` —— 你已登录的浏览器配置（cookie）
- `gemini_outputs\` —— 默认保存生成图片的地方

> ⚠️ 千万不要分享这些文件夹 —— 里面包含你的登录会话。

---

## ℹ️ 说明与限制

- 本工具自动化的是 Gemini 的**免费网页版**，受 Gemini 正常的使用额度和服务条款约束。
  请用你自己的账号、合理使用。
- 目前在 **Windows** 上构建和测试。
- 这是一个非官方工具，**与 Google 无关**。
