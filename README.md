# BestVPN AI Telegram Bot

一个集成了 OpenWebUI API 的 Telegram 聊天机器人，可以通过 Railway 轻松部署。

## 功能特性

- 🤖 与用户进行智能对话
- 🔗 集成 OpenWebUI API（bestvpnai.org）
- 📱 支持 Telegram webhook
- ☁️ 可在 Railway 上一键部署
- 🚀 高可用性和自动重启

## 快速开始

### 1. 创建 Telegram Bot

1. 在 Telegram 中搜索 [@BotFather](https://t.me/botfather)
2. 发送 `/newbot` 命令
3. 按照提示设置机器人名称和用户名
4. 获取 Bot Token

### 2. 获取 OpenWebUI API Key

1. 访问 [bestvpnai.org](https://bestvpnai.org)
2. 登录您的账户
3. 进入 Settings > Account
4. 复制 API Key

### 3. 在 Railway 上部署

#### 方法一：从 GitHub 导入

1. 将此代码推送到您的 GitHub 仓库
2. 访问 [Railway](https://railway.app)
3. 点击 "New Project" → "Deploy from GitHub repo"
4. 选择您的仓库
5. 设置环境变量（见下方）

#### 方法二：直接部署

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template)

### 4. 设置环境变量

在 Railway 项目设置中添加以下环境变量：

```
TELEGRAM_BOT_TOKEN=你的_telegram_bot_token
OPENWEBUI_BASE_URL=https://bestvpnai.org
OPENWEBUI_API_KEY=你的_openwebui_api_key
PORT=5000
```

### 5. 设置 Webhook

部署完成后，获取 Railway 提供的域名，然后设置 Telegram webhook：

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://your-railway-domain.railway.app/webhook"}'
```

## 本地开发

1. 克隆仓库：
```bash
git clone <your-repo-url>
cd bestvpnaibot
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 复制环境变量文件：
```bash
cp .env.example .env
```

4. 编辑 `.env` 文件，填入您的配置

5. 运行应用：
```bash
python main.py
```

## 支持的命令

- `/start` - 开始与机器人对话
- `/help` - 显示帮助信息
- 直接发送文本消息与 AI 对话

## 项目结构

```
bestvpnaibot/
├── main.py              # 主应用程序
├── requirements.txt     # Python 依赖
├── Procfile            # Railway 启动配置
├── railway.json        # Railway 部署配置
├── .env.example        # 环境变量示例
├── .gitignore          # Git 忽略文件
└── README.md           # 项目说明
```

## 故障排除

### 常见问题

1. **Bot 没有响应**
   - 检查 webhook 是否正确设置
   - 确认环境变量是否正确配置
   - 查看 Railway 日志

2. **OpenWebUI API 调用失败**
   - 验证 API Key 是否有效
   - 确认 bestvpnai.org 网站可以正常访问
   - 检查模型名称是否正确

3. **部署失败**
   - 确保所有必需的文件都已提交
   - 检查 requirements.txt 中的依赖版本
   - 查看 Railway 构建日志

### 健康检查

访问 `https://your-railway-domain.railway.app/health` 检查服务状态。

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

MIT License