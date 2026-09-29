# Video Analysis QQBot

一个基于 PySide6 的 QQ 视频解析机器人控制台，支持抖音和哔哩哔哩链接解析，并通过 OneBot 11 正向 WebSocket 向 QQ 回复视频或图文内容。

## 功能

- 抖音视频解析：回复作品信息、视频和封面缩略图。
- 抖音图文解析：回复作品信息，并以合并转发发送图片。
- Bilibili 视频解析：支持 BV/AV 链接和 `b23.tv` 短链。
- Bilibili 按账号实际可用的清晰度解析，并按默认清晰度下载发送视频。
- NapCat、SnowLuma 和 `auto` backend。
- QQ 私聊和群聊白名单。
- 本地 Cookie、浏览器 profile、NapCat Token 和运行配置不进入 Git。
- 运行页面显示全局日志；抖音、Bilibili 和 QQBot 页面显示各自来源日志。

## 环境要求

- Windows
- Python `3.13` 或更高版本
- `uv`
- NapCat 或 SnowLuma OneBot 11 正向 WebSocket
- 抖音登录需要 Edge/Chrome 和 Selenium 驱动环境

## 安装

在项目根目录执行：

```powershell
uv sync
```

## 启动

推荐双击根目录的：

```text
启动QQBot.bat
```

或手动执行：

```powershell
uv run python LoginCenter.py
```

## 配置 NapCat

打开控制台的 `NapCat WebSocket` 页面：

1. 选择 `Auto`、`NapCat` 或 `SnowLuma` backend。
2. 填写 WS 地址，例如：`ws://127.0.0.1:3001/`。
3. 按需填写鉴权 Token。
4. 配置监听白名单：
   - `*QQ号`：私聊
   - `#群号`：群聊
5. 点击保存配置。
6. 点击 `启动机器人`，程序会依次检查抖音、Bilibili 和 NapCat QQ 登录状态。

启动机器人前至少配置一个白名单条目。

## 平台登录

### 抖音

在 `抖音登录` 页面可以：

- 打开登录页
- 复用浏览器 profile 刷新 Cookie
- 检查登录状态
- 试解析分享链接
- 清空本地 Cookie 和浏览器 profile

### Bilibili

在 `哔哩哔哩登录` 页面可以：

- 自动检查已有本地 Cookie
- 点击 `验证/扫码登录` 复用已有 Cookie；无有效 Cookie 时才打开扫码浏览器
- 检查登录状态
- 试解析 Bilibili 链接，不下载视频
- 清空本地 Cookie 和浏览器 profile
- 设置默认清晰度

Bilibili 默认清晰度遵循以下规则：选择“不高于默认档位的最高可用画质”。例如默认设置为 1080P，而账号只能获取 720P，则发送 720P。默认清晰度只用于选择实际发送的视频，不会出现在 QQ 返回文本中。

Bilibili DASH 视频的音频和视频是分离流。发送视频前需要 `ffmpeg` 合并音视频。哔哩哔哩页面提供 `检测 FFmpeg` 和 `一键安装 FFmpeg`，安装到 `BiliCore/bilicore/ffmpeg/`，不会修改系统 PATH。

## QQ 回复流程

收到白名单内的抖音或 Bilibili 链接后，机器人会先发送引用进度消息。

抖音视频：

1. 回复作品信息。
2. 下载视频和封面。
3. 发送视频消息，NapCat 附带封面缩略图。

抖音图文：

1. 回复作品信息。
2. 下载图片。
3. 使用合并转发发送文案和图片。

Bilibili 视频：

1. 回复作品信息。
2. 按默认清晰度选择实际可用视频流。
3. 下载视频。
4. NapCat 下载并附带封面缩略图后发送；SnowLuma 发送独立 video segment。

## Core 结构

两个平台解析核心彼此独立：

```text
DouyinCore/
├─ douyin_core/
│  ├─ cookies.json
│  └─ .browser_profile/
└─ tests/

BiliCore/
├─ bilicore/
│  ├─ cookies.json
│  ├─ .browser_profile/
│  └─ settings.json
└─ tests/
```

QQBot 和 `LoginCenter.py` 只负责消息路由、运行控制和界面，不把一个平台的持久化数据写入另一个 Core。

## 本地敏感数据

以下文件仅保存在本机，并由 `.gitignore` 排除：

```text
DouyinCore/douyin_core/cookies.json
DouyinCore/douyin_core/.browser_profile/
BiliCore/bilicore/cookies.json
BiliCore/bilicore/.browser_profile/
BiliCore/bilicore/settings.json
QQBot/config.json
QQBot/allow.txt
QQBot/platforms.json
```

不要把 Cookie、Token、浏览器 profile 或本地配置复制到 Git 提交中。

## 测试

运行全量测试：

```powershell
uv run pytest -q
```

其他检查：

```powershell
uv run python -m py_compile LoginCenter.py
uv lock --check
git diff --check
```

## 日志

日志统一显示为：

```text
time | tag | belong | msg
```

其中 `belong` 使用以下来源标记：

- `全局`
- `QQbot`
- `抖音`
- `哔哩哔哩`

运行页面汇总全局运行日志；平台页面只显示对应平台日志。
