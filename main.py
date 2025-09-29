import os
import logging
import requests
import json
import re
import time
import threading
from flask import Flask, request, jsonify
from collections import defaultdict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
OPENWEBUI_BASE_URL = os.environ.get('OPENWEBUI_BASE_URL', 'https://bestvpnai.org')
OPENWEBUI_API_KEY = os.environ.get('OPENWEBUI_API_KEY')

# 存储用户会话上下文
user_conversations = defaultdict(list)

# 速率限制存储
user_rate_limits = defaultdict(list)  # {user_id: [timestamp1, timestamp2, ...]}
session_rate_limits = defaultdict(list)  # {session_key: [timestamp1, timestamp2, ...]}

# 线程锁，确保速率限制检查的原子性
rate_limit_lock = threading.Lock()
conversation_lock = threading.Lock()


class TelegramBot:
    def __init__(self):
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        
    def send_message(self, chat_id, text):
        """Send message to Telegram chat"""
        url = f"{self.api_url}/sendMessage"

        # 清理文本格式
        cleaned_text = self.clean_text_for_telegram(text)

        payload = {
            'chat_id': chat_id,
            'text': cleaned_text,
            'parse_mode': 'Markdown'
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.warning(f"Markdown parsing failed in send_message, trying HTML. Error: {response.text}")
                # 如果Markdown失败，尝试HTML格式
                payload['parse_mode'] = 'HTML'
                response = requests.post(url, json=payload, timeout=10)

                if response.status_code != 200:
                    logger.warning("HTML parsing also failed in send_message, using plain text")
                    # 如果HTML也失败，使用纯文本
                    payload['parse_mode'] = None
                    payload['text'] = text  # 使用原始文本
                    response = requests.post(url, json=payload, timeout=10)

            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send message: {e}")
            return None
    
    def clean_text_for_telegram(self, text):
        """清理文本以适应Telegram的Markdown格式"""
        if not text:
            return text

        import re

        # 分割文本为正文和参考资料部分
        reference_markers = ['🔍 详细信息', '🔍 *详细信息', '参考网站', '参考资料']
        reference_section = ""
        main_content = text

        for marker in reference_markers:
            if marker in text:
                parts = text.split(marker, 1)
                if len(parts) == 2:
                    main_content = parts[0]
                    reference_section = marker + parts[1]
                break

        # 处理正文部分
        # 1. 处理引用链接格式：数字 (完整链接) -> 数字（只在正文中）
        main_content = re.sub(r'(\d+)\s*\(https?://[^\)]+\)', r'\1', main_content)

        # 2. 移除孤立的链接：(完整链接) -> 移除（只在正文中）
        main_content = re.sub(r'\s*\(https?://[^\)]+\)', '', main_content)

        # 3. 简化[[数字]]格式为[数字]
        main_content = re.sub(r'\[\[(\d+)\]\]', r'[\1]', main_content)

        # 合并处理后的内容
        text = main_content + reference_section

        # 4. 处理HTML标签
        text = text.replace('<details>', '\n\n🔍 *详细信息:*\n')
        text = text.replace('</details>', '\n')
        text = text.replace('<summary>', '*')
        text = text.replace('</summary>', '*\n')

        # 5. 改善段落和列表格式
        # 确保列表项前有换行
        text = re.sub(r'([。！？])\s*-\s*', r'\1\n\n• ', text)
        text = re.sub(r'^-\s*', '• ', text, flags=re.MULTILINE)

        # 6. 处理连续的引用数字，避免数字堆积（只在正文中）
        lines = text.split('\n')
        processed_lines = []
        in_reference_section = False

        for line in lines:
            if any(marker in line for marker in reference_markers):
                in_reference_section = True

            if not in_reference_section:
                # 在正文中处理连续数字
                def format_references(match):
                    content = match.group(1)
                    numbers = match.group(2)
                    if len(numbers) > 3:
                        num_list = ', '.join(numbers)
                        return f"{content}[{num_list}]"
                    return match.group(0)

                line = re.sub(r'([^0-9])(\d{3,})\s*$', format_references, line)

            processed_lines.append(line)

        text = '\n'.join(processed_lines)

        # 7. 改善段落分隔
        text = re.sub(r'([。！？])\s*([A-Z\u4e00-\u9fff])', r'\1\n\n\2', text)

        # 8. 修复参考资料部分的链接格式
        if reference_section:
            # 分割出参考资料部分进行特殊处理
            ref_lines = text.split('\n')
            processed_ref_lines = []
            in_ref_section = False

            for line in ref_lines:
                if any(marker in line for marker in reference_markers):
                    in_ref_section = True
                    processed_ref_lines.append(line)
                    continue

                if in_ref_section and line.strip():
                    # 修复被破坏的markdown链接格式
                    # 处理格式：数字: [文本 (URL)](URL) -> 数字: [文本](URL)
                    line = re.sub(r'(\d+):\s*\[([^\]]+?)\s*\(([^)]+)\)\]\(([^)]+)\)', r'\1: [\2](\3)', line)

                    # 处理格式：数字: [文本) -> 数字: 文本（移除不完整的方括号）
                    line = re.sub(r'(\d+):\s*\[([^\]]+?)\)', r'\1: \2', line)

                    # 处理正常的markdown链接，确保格式正确
                    # 如果有完整的 [文本](URL) 格式，保持不变
                    # 如果只有文本和URL分离的情况，尝试组合
                    if '(' in line and ')' in line and '[' not in line:
                        # 格式：数字: 文本 (URL) -> 数字: [文本](URL)
                        match = re.match(r'(\d+):\s*([^(]+?)\s*\(([^)]+)\)', line)
                        if match:
                            num, title, url = match.groups()
                            title = title.strip()
                            if url.startswith('http'):
                                line = f"{num}: [{title}]({url})"

                processed_ref_lines.append(line)

            text = '\n'.join(processed_ref_lines)

        # 9. 清理多余空格，但保留必要的换行
        text = re.sub(r'[ \t]+', ' ', text)  # 清理空格和tab
        text = re.sub(r'\n{3,}', '\n\n', text)  # 限制连续换行

        # 10. 限制消息长度
        if len(text) > 4096:
            text = text[:4090] + "..."

        return text.strip()

    def edit_message(self, chat_id, message_id, text):
        """Edit existing message in Telegram chat"""
        url = f"{self.api_url}/editMessageText"

        # 清理文本格式
        cleaned_text = self.clean_text_for_telegram(text)

        payload = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': cleaned_text,
            'parse_mode': 'Markdown'
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.warning(f"Markdown parsing failed, trying HTML parse mode. Error: {response.text}")
                # 如果Markdown失败，尝试HTML格式
                payload['parse_mode'] = 'HTML'
                response = requests.post(url, json=payload, timeout=10)

                if response.status_code != 200:
                    logger.warning("HTML parsing also failed, using plain text")
                    # 如果HTML也失败，使用纯文本
                    payload['parse_mode'] = None
                    payload['text'] = text  # 使用原始文本
                    response = requests.post(url, json=payload, timeout=10)

            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to edit message: {e}")
            if hasattr(e, 'response'):
                logger.error(f"Response content: {e.response.text}")
            return None

class OpenWebUIClient:
    def __init__(self):
        self.base_url = OPENWEBUI_BASE_URL
        self.api_key = OPENWEBUI_API_KEY
        
    def get_available_models(self):
        """Get available models from OpenWebUI"""
        url = f"{self.base_url}/api/models"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            if 'data' in data:
                models = [model['id'] for model in data['data']]
                logger.info(f"Available models: {models}")
                return models
            return []
        except Exception as e:
            logger.error(f"Failed to get models: {e}")
            return []
    
    def add_to_conversation(self, user_id, role, content):
        """Add message to user's conversation history（线程安全版本）"""
        with conversation_lock:
            user_conversations[user_id].append({"role": role, "content": content})
            # 保持最近20条消息的历史
            if len(user_conversations[user_id]) > 20:
                user_conversations[user_id] = user_conversations[user_id][-20:]
    
    def get_conversation_history(self, user_id):
        """Get user's conversation history（线程安全版本）"""
        with conversation_lock:
            return user_conversations[user_id].copy()  # 返回副本避免外部修改

    def clear_conversation(self, user_id):
        """Clear user's conversation history（线程安全版本）"""
        with conversation_lock:
            user_conversations[user_id] = []

    def check_user_rate_limit(self, user_id, max_requests=5, time_window=86400):
        """
        检查用户速率限制（线程安全版本）
        Args:
            user_id: 用户ID
            max_requests: 时间窗口内最大请求数 (默认: 5次)
            time_window: 时间窗口秒数 (默认: 86400秒 = 24小时)
        Returns:
            (bool, int): (是否允许, 剩余等待时间)
        """
        with rate_limit_lock:
            current_time = time.time()

            # 清理过期的时间戳
            user_rate_limits[user_id] = [
                timestamp for timestamp in user_rate_limits[user_id]
                if current_time - timestamp < time_window
            ]

            # 检查是否超过限制
            if len(user_rate_limits[user_id]) >= max_requests:
                oldest_request = min(user_rate_limits[user_id])
                wait_time = int(time_window - (current_time - oldest_request))
                return False, wait_time

            # 记录当前请求
            user_rate_limits[user_id].append(current_time)
            return True, 0

    def check_session_rate_limit(self, chat_id, user_id, max_requests=2, time_window=10):
        """
        检查会话速率限制（防止快速连续消息，线程安全版本）
        Args:
            chat_id: 聊天ID
            user_id: 用户ID
            max_requests: 时间窗口内最大请求数 (默认: 2次)
            time_window: 时间窗口秒数 (默认: 10秒)
        Returns:
            (bool, int): (是否允许, 剩余等待时间)
        """
        with rate_limit_lock:
            current_time = time.time()
            session_key = f"{chat_id}_{user_id}"

            # 清理过期的时间戳
            session_rate_limits[session_key] = [
                timestamp for timestamp in session_rate_limits[session_key]
                if current_time - timestamp < time_window
            ]

            # 检查是否超过限制
            if len(session_rate_limits[session_key]) >= max_requests:
                oldest_request = min(session_rate_limits[session_key])
                wait_time = int(time_window - (current_time - oldest_request))
                return False, wait_time

            # 记录当前请求
            session_rate_limits[session_key].append(current_time)
            return True, 0

    def get_rate_limit_status(self, user_id):
        """获取用户当前的速率限制状态（线程安全版本）"""
        with rate_limit_lock:
            current_time = time.time()

            # 清理过期记录
            user_rate_limits[user_id] = [
                timestamp for timestamp in user_rate_limits[user_id]
                if current_time - timestamp < 86400  # 24小时
            ]

            used_requests = len(user_rate_limits[user_id])
            remaining_requests = max(0, 5 - used_requests)

            return {
                'used': used_requests,
                'remaining': remaining_requests,
                'limit': 5,
                'window': 86400  # 24小时 = 86400秒
            }
    
    def filter_ai_response(self, text):
        """过滤AI响应，移除推理过程和不必要的内容"""
        if not text:
            return ""
        
        # 移除think标签及其内容
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.MULTILINE | re.DOTALL)
        
        # 移除工具调用JSON块 - 更精确的匹配
        text = re.sub(r'工具调用：\s*\{.*?\}', '', text, flags=re.MULTILINE | re.DOTALL)
        text = re.sub(r'\{[^}]*"tool[^}]*\}', '', text, flags=re.MULTILINE | re.DOTALL)
        text = re.sub(r'\{[^}]*"parameters"[^}]*\}', '', text, flags=re.MULTILINE | re.DOTALL)
        
        # 移除系统提示和搜索状态
        text = re.sub(r'我正在查找相关信息[.…]*\s*', '', text)
        text = re.sub(r'我将为您查询[^.]*\.\s*', '', text)
        text = re.sub(r'（系统将执行[^）]*）\s*', '', text)
        text = re.sub(r'我的知识主要截至[^.]*\.\s*对于[^.]*，[^.]*\.\s*', '', text)
        
        # 清理多余空行
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        text = re.sub(r'^\s*\n+', '', text)
        
        # 如果文本太短或只包含搜索提示，返回友好消息
        cleaned = text.strip()
        if len(cleaned) < 20 or not cleaned:
            return ""
            
        return cleaned

    def cleanup_expired_data(self):
        """清理过期的数据以防止内存泄漏（定期调用，分段加锁避免长时间阻塞）"""
        current_time = time.time()

        # 分别处理不同的数据结构，避免长时间持有锁
        expired_users = []
        expired_sessions = []
        expired_conversations = []

        # 1. 清理速率限制记录（短时间持锁）
        with rate_limit_lock:
            for user_id in list(user_rate_limits.keys()):
                user_rate_limits[user_id] = [
                    timestamp for timestamp in user_rate_limits[user_id]
                    if current_time - timestamp < 86400  # 24小时
                ]
                if not user_rate_limits[user_id]:
                    expired_users.append(user_id)

            for user_id in expired_users:
                del user_rate_limits[user_id]

            # 清理会话限制记录
            for session_key in list(session_rate_limits.keys()):
                session_rate_limits[session_key] = [
                    timestamp for timestamp in session_rate_limits[session_key]
                    if current_time - timestamp < 7200  # 2小时
                ]
                if not session_rate_limits[session_key]:
                    expired_sessions.append(session_key)

            for session_key in expired_sessions:
                del session_rate_limits[session_key]

        # 2. 清理对话记录（短时间持锁）
        with conversation_lock:
            for user_id in list(user_conversations.keys()):
                if not user_conversations[user_id]:
                    # 检查该用户是否在最近活跃（在rate_limit_lock外检查）
                    expired_conversations.append(user_id)

            for user_id in expired_conversations:
                # 二次检查：只删除确实不活跃的用户
                if (user_id not in user_rate_limits and
                    not user_conversations[user_id]):
                    del user_conversations[user_id]

        if expired_users or expired_sessions or expired_conversations:
            logger.info(f"Cleaned up expired data: {len(expired_users)} users, {len(expired_sessions)} sessions, {len(expired_conversations)} conversations")

    def simple_chat_completion(self, bot, chat_id, user_id, message, model="xmptest.https://api.perplexity.ai"):
        """非流式处理AI响应，一次性发送完整回复"""
        url = f"{self.base_url}/api/chat/completions"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        # 添加用户消息到历史
        self.add_to_conversation(user_id, "user", message)

        # 获取完整对话历史
        messages = self.get_conversation_history(user_id)

        # 使用流式API（因为该模型只支持流式调用）
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,  # 改回流式
            "max_tokens": 4000,
            "temperature": 0.7,
            "top_p": 0.9,
            "presence_penalty": 0,
            "frequency_penalty": 0
        }

        # 记录消息历史用于调试
        logger.info(f"Message history length: {len(messages)}")
        for i, msg in enumerate(messages[-3:]):  # 只记录最近3条消息
            logger.info(f"Message {i}: {msg['role']} - {msg['content'][:50]}...")

        # 发送等待状态消息
        status_msg = bot.send_message(chat_id, "🤔 正在思考中，请稍候...\n\n💡 *BestVPN翻墙利器，解锁更多更强的AI工具：https://vp0.org*")
        if not status_msg:
            return "抱歉，发送消息时出现问题。"

        message_id = status_msg['result']['message_id']

        try:
            logger.info(f"Sending request to OpenWebUI: {url}")
            logger.info(f"Using model: {model}")
            logger.info(f"Request payload keys: {list(payload.keys())}")

            response = requests.post(url, headers=headers, json=payload, timeout=300, stream=True)  # 减少到5分钟
            logger.info(f"Response status: {response.status_code}")

            # 如果状态码不是200，记录详细错误信息
            if response.status_code != 200:
                logger.error(f"API returned non-200 status: {response.status_code}")
                logger.error(f"Response headers: {dict(response.headers)}")
                logger.error(f"Response content: {response.text[:1000]}...")
                response.raise_for_status()

            # 处理流式响应，但一次性显示结果
            full_response = ""
            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith('data: '):
                        data_str = line[6:]  # 移除 'data: ' 前缀
                        if data_str.strip() == '[DONE]':
                            break
                        try:
                            data = json.loads(data_str)
                            if 'choices' in data and len(data['choices']) > 0:
                                delta = data['choices'][0].get('delta', {})
                                content = delta.get('content', '')
                                if content:
                                    full_response += content
                        except json.JSONDecodeError:
                            continue

            logger.info(f"Streaming completed. Full response length: {len(full_response)}")

            if full_response.strip():
                # 过滤响应
                filtered_response = self.filter_ai_response(full_response.strip())
                logger.info(f"Filtered response length: {len(filtered_response) if filtered_response else 0}")

                if filtered_response:
                    # 更新消息为最终回复
                    bot.edit_message(chat_id, message_id, filtered_response)
                    self.add_to_conversation(user_id, "assistant", filtered_response)
                    logger.info(f"Successfully sent filtered response")
                    return filtered_response
                else:
                    logger.warning("Response was filtered out completely, using original")
                    bot.edit_message(chat_id, message_id, full_response.strip())
                    self.add_to_conversation(user_id, "assistant", full_response.strip())
                    return full_response.strip()
            else:
                logger.warning("No content received from streaming response")
                error_msg = "抱歉，我没有收到有效的回复，请稍后再试。"
                bot.edit_message(chat_id, message_id, error_msg)
                return error_msg

        except requests.exceptions.Timeout:
            logger.error("API request timeout")
            error_msg = "请求超时，请稍后再试。可能是AI服务较忙。"
            bot.edit_message(chat_id, message_id, error_msg)
            return error_msg
        except requests.exceptions.RequestException as e:
            logger.error(f"API request error: {e}")
            error_msg = "网络连接错误，请检查网络后再试。"
            bot.edit_message(chat_id, message_id, error_msg)
            return error_msg
        except Exception as e:
            logger.error(f"Chat completion error: {e}")
            error_msg = "处理请求时出现未知错误，请稍后再试。"
            bot.edit_message(chat_id, message_id, error_msg)
            return error_msg

bot = TelegramBot()
openwebui_client = OpenWebUIClient()

# 定期清理过期数据的简单实现
import threading
import time

def periodic_cleanup():
    """后台线程定期清理过期数据"""
    while True:
        try:
            time.sleep(3600)  # 每小时清理一次
            openwebui_client.cleanup_expired_data()
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

# 启动清理线程
cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
cleanup_thread.start()

@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle Telegram webhook updates"""
    try:
        update = request.get_json()
        
        if not update or 'message' not in update:
            return jsonify({'ok': True})
        
        message = update['message']
        chat_id = message['chat']['id']
        
        if 'text' not in message:
            bot.send_message(chat_id, "抱歉，我只能处理文本消息。")
            return jsonify({'ok': True})
        
        user_message = message['text']
        user_name = message.get('from', {}).get('first_name', 'User')
        user_id = str(message.get('from', {}).get('id', chat_id))
        
        logger.info(f"Received message from {user_name} (ID: {user_id}): {user_message}")

        # Skip rate limiting for commands
        if not user_message.startswith('/'):
            # 检查用户速率限制
            user_allowed, user_wait_time = openwebui_client.check_user_rate_limit(user_id)
            if not user_allowed:
                # 将等待时间转换为更友好的显示格式
                if user_wait_time >= 3600:
                    wait_display = f"{user_wait_time // 3600} 小时 {(user_wait_time % 3600) // 60} 分钟"
                elif user_wait_time >= 60:
                    wait_display = f"{user_wait_time // 60} 分钟 {user_wait_time % 60} 秒"
                else:
                    wait_display = f"{user_wait_time} 秒"

                rate_limit_msg = f"⏱️ 您今日的请求次数已用完，请等待 {wait_display} 后再试。\n\n📋 每日限制：5次请求\n\n💡 *BestVPN翻墙利器，解锁更多更强的AI工具：https://vp0.org*"
                bot.send_message(chat_id, rate_limit_msg)
                logger.warning(f"User {user_id} hit daily rate limit, wait time: {user_wait_time}s")
                return jsonify({'ok': True})

            # 检查会话速率限制
            session_allowed, session_wait_time = openwebui_client.check_session_rate_limit(chat_id, user_id)
            if not session_allowed:
                session_limit_msg = f"🚀 请慢一点！您发送消息太快了，请等待 {session_wait_time} 秒。\n\n💬 会话限制：10秒内最多2条消息\n\n💡 *BestVPN翻墙利器，解锁更多更强的AI工具：https://vp0.org*"
                bot.send_message(chat_id, session_limit_msg)
                logger.warning(f"Session {chat_id}_{user_id} hit rate limit, wait time: {session_wait_time}s")
                return jsonify({'ok': True})

        # Handle /start command
        if user_message.startswith('/start'):
            logger.info(f"Processing /start command for user {user_name} (ID: {user_id})")
            openwebui_client.clear_conversation(user_id)

            welcome_message = f"""👋 你好 {user_name}！

🌐 欢迎使用搜外网

🤖 我是您的专属信息助手，基于先进AI技术驱动，专门为您提供：

🔍 核心功能
• 搜索任何外网资讯和信息
• 实时获取全球最新动态
• 智能分析和整理信息
• 多语言内容理解和翻译

💬 智能对话
• 支持连续对话，记住上下文
• 个性化回答您的问题
• 24/7随时为您服务

✨ 直接发送您想了解的任何问题，我会为您搜索并提供详细信息！

💡 BestVPN翻墙利器，解锁更多更强的AI工具：https://vp0.org"""

            result = bot.send_message(chat_id, welcome_message)
            if result:
                logger.info("Welcome message sent successfully")
            else:
                logger.error("Failed to send welcome message")
            return jsonify({'ok': True})
        
        # Handle /help command
        if user_message.startswith('/help'):
            help_message = "🌐 **搜外网 - 使用指南**\n\n" + \
                          "🤖 **关于搜外网**\n" + \
                          "• AI驱动的智能信息助手\n" + \
                          "• 专业搜索外网资讯和信息\n" + \
                          "• 实时获取全球最新动态\n\n" + \
                          "📋 **可用命令**\n" + \
                          "• 直接发送问题 - 搜索并获取信息\n" + \
                          "• /start - 开始使用（清除对话历史）\n" + \
                          "• /clear - 清除对话历史\n" + \
                          "• /status - 查看使用限制状态\n" + \
                          "• /help - 查看本帮助信息\n\n" + \
                          "🔍 **使用示例**\n" + \
                          "• 「特斯拉最新财报」\n" + \
                          "• 「比特币今日价格走势」\n" + \
                          "• 「OpenAI最新动态」\n" + \
                          "• 「美国大选最新消息」\n\n" + \
                          "💡 **智能特性**\n" + \
                          "• 支持上下文对话，记住聊天内容\n" + \
                          "• 自动翻译和整理信息\n" + \
                          "• 提供准确的外网资讯\n\n" + \
                          "⚡ **使用限制**\n" + \
                          "• 每日限制：5次搜索请求\n" + \
                          "• 会话限制：10秒内最多2条消息\n\n" + \
                          "💡 *BestVPN翻墙利器，解锁更多更强的AI工具：https://vp0.org*"
            bot.send_message(chat_id, help_message)
            return jsonify({'ok': True})
        
        # Handle /clear command
        if user_message.startswith('/clear'):
            openwebui_client.clear_conversation(user_id)
            bot.send_message(chat_id, "✅ 对话历史已清除，我们可以开始新的对话了！")
            return jsonify({'ok': True})

        # Handle /status command
        if user_message.startswith('/status'):
            status_info = openwebui_client.get_rate_limit_status(user_id)
            status_message = f"📊 **您的速率限制状态**\n\n" + \
                           f"🔢 今日已使用：{status_info['used']}/{status_info['limit']} 次\n" + \
                           f"✅ 剩余请求：{status_info['remaining']} 次\n" + \
                           f"⏱️ 重置时间：每日00:00\n\n" + \
                           f"📋 **限制说明：**\n" + \
                           f"💬 会话限制：10秒内最多2条消息\n" + \
                           f"📅 每日限制：24小时内最多5次请求"
            bot.send_message(chat_id, status_message)
            return jsonify({'ok': True})

        # Get response from OpenWebUI (non-streaming)
        ai_response = openwebui_client.simple_chat_completion(bot, chat_id, user_id, user_message)
        
        return jsonify({'ok': True})
        
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return jsonify({'ok': False}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'service': 'bestvpnai-bot'})

@app.route('/', methods=['GET'])
def index():
    """Root endpoint"""
    return jsonify({
        'name': '搜外网 (SouWaiWang)',
        'status': 'running',
        'description': 'AI-powered global information search assistant - Your gateway to worldwide news and information',
        'features': [
            '🔍 Search external websites and global news',
            '🤖 AI-driven intelligent responses',
            '🌐 Multi-language support and translation',
            '💬 Context-aware conversations',
            '⚡ Real-time information retrieval'
        ],
        'version': '1.0.0',
        'powered_by': 'OpenWebUI API & BestVPN'
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)