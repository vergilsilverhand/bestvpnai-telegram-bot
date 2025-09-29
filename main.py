import os
import logging
import requests
import json
import re
import time
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

# 存储用户消息处理状态
user_processing_status = {}  # {user_id: {'chat_id': chat_id, 'message_id': message_id, 'status': 'processing'}}

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

        # 先处理特殊的HTML标签，转换为Telegram支持的格式
        text = text.replace('<details>', '\n🔍 *详细信息:*\n')
        text = text.replace('</details>', '\n')
        text = text.replace('<summary>', '*')
        text = text.replace('</summary>', '*\n')

        # 处理链接格式 - 保留有用的链接但简化格式
        import re

        # 将 [[数字]] 格式的引用转换为更简单的格式
        text = re.sub(r'\[\[(\d+)\]\]', r'[\1]', text)

        # 处理Markdown链接格式，智能保留有用链接
        def replace_link(match):
            link_text = match.group(1)
            link_url = match.group(2)

            # 如果链接文本本身就是URL或者很长的描述，只保留文本
            if (link_url.lower() in link_text.lower() or
                len(link_text) > 80 or
                'youtube.com' in link_url or
                'wikipedia.org' in link_url):
                return link_text

            # 如果是短标题且有有用的链接，保留简化格式
            if len(link_text) < 30 and not link_text.startswith('http'):
                return f"[{link_text}]({link_url})"

            # 其他情况只保留文本
            return link_text

        text = re.sub(r'\[([^\]]+)\]\(([^\)]+)\)', replace_link, text)

        # 保护已有的加粗格式，但确保格式正确
        text = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', text)

        # 清理可能导致Markdown问题的字符，但保留基本格式
        # text = text.replace('_', '\\_').replace('[', '\\[').replace('`', '\\`')

        # 限制消息长度
        if len(text) > 4096:
            text = text[:4090] + "..."

        return text

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
        """Add message to user's conversation history"""
        user_conversations[user_id].append({"role": role, "content": content})
        # 保持最近20条消息的历史
        if len(user_conversations[user_id]) > 20:
            user_conversations[user_id] = user_conversations[user_id][-20:]
    
    def get_conversation_history(self, user_id):
        """Get user's conversation history"""
        return user_conversations[user_id]
    
    def clear_conversation(self, user_id):
        """Clear user's conversation history"""
        user_conversations[user_id] = []
    
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
    
    def cancel_processing(self, user_id):
        """取消用户当前的消息处理"""
        if user_id in user_processing_status:
            user_processing_status[user_id]['status'] = 'cancelled'
            logger.info(f"Cancelled processing for user {user_id}")
            return True
        return False

    def is_processing_cancelled(self, user_id):
        """检查用户的处理是否被取消"""
        return user_id in user_processing_status and user_processing_status[user_id].get('status') == 'cancelled'

    def set_processing_status(self, user_id, chat_id, message_id):
        """设置用户处理状态"""
        user_processing_status[user_id] = {
            'chat_id': chat_id,
            'message_id': message_id,
            'status': 'processing'
        }

    def clear_processing_status(self, user_id):
        """清除用户处理状态"""
        if user_id in user_processing_status:
            del user_processing_status[user_id]

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
        status_msg = bot.send_message(chat_id, "🤔 正在思考中，请稍候...\n\n💡 发送 /cancel 可以取消当前处理")
        if not status_msg:
            return "抱歉，发送消息时出现问题。"

        message_id = status_msg['result']['message_id']

        # 设置处理状态
        self.set_processing_status(user_id, chat_id, message_id)

        try:
            logger.info(f"Sending request to OpenWebUI: {url}")
            logger.info(f"Using model: {model}")
            logger.info(f"Request payload keys: {list(payload.keys())}")

            response = requests.post(url, headers=headers, json=payload, timeout=600, stream=True)
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
                # 检查是否被取消
                if self.is_processing_cancelled(user_id):
                    bot.edit_message(chat_id, message_id, "❌ 处理已被取消")
                    self.clear_processing_status(user_id)
                    return "处理已被取消"

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

            # 最终检查是否被取消
            if self.is_processing_cancelled(user_id):
                bot.edit_message(chat_id, message_id, "❌ 处理已被取消")
                self.clear_processing_status(user_id)
                return "处理已被取消"

            if full_response.strip():
                # 过滤响应
                filtered_response = self.filter_ai_response(full_response.strip())
                logger.info(f"Filtered response length: {len(filtered_response) if filtered_response else 0}")

                if filtered_response:
                    # 更新消息为最终回复
                    bot.edit_message(chat_id, message_id, filtered_response)
                    self.add_to_conversation(user_id, "assistant", filtered_response)
                    self.clear_processing_status(user_id)
                    logger.info(f"Successfully sent filtered response")
                    return filtered_response
                else:
                    logger.warning("Response was filtered out completely, using original")
                    bot.edit_message(chat_id, message_id, full_response.strip())
                    self.add_to_conversation(user_id, "assistant", full_response.strip())
                    self.clear_processing_status(user_id)
                    return full_response.strip()
            else:
                logger.warning("No content received from streaming response")
                error_msg = "抱歉，我没有收到有效的回复，请稍后再试。"
                bot.edit_message(chat_id, message_id, error_msg)
                self.clear_processing_status(user_id)
                return error_msg

        except Exception as e:
            logger.error(f"Chat completion error: {e}")
            if not self.is_processing_cancelled(user_id):
                bot.edit_message(chat_id, message_id, "抱歉，处理您的请求时出现了问题，请稍后再试。")
            self.clear_processing_status(user_id)
            return "抱歉，处理您的请求时出现了问题，请稍后再试。"

bot = TelegramBot()
openwebui_client = OpenWebUIClient()

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
        
        # Handle /start command
        if user_message.startswith('/start'):
            openwebui_client.clear_conversation(user_id)
            welcome_message = f"你好 {user_name}! 👋\n\n我是由 BestVPN AI 提供支持的智能助手。\n\n请随时向我提问，我会记住我们的对话内容！"
            bot.send_message(chat_id, welcome_message)
            return jsonify({'ok': True})
        
        # Handle /help command
        if user_message.startswith('/help'):
            help_message = "🤖 **使用说明**\n\n" + \
                          "• 直接发送消息与我对话\n" + \
                          "• /start - 开始新的对话（清除历史）\n" + \
                          "• /clear - 清除对话历史\n" + \
                          "• /cancel - 取消当前的消息处理\n" + \
                          "• /help - 查看帮助信息\n\n" + \
                          "💡 **提示：**\n" + \
                          "• 我会记住我们的对话内容，支持上下文对话！\n" + \
                          "• 发送新消息时会自动取消之前的处理\n" + \
                          "• 处理过程中可以随时使用 /cancel 取消"
            bot.send_message(chat_id, help_message)
            return jsonify({'ok': True})
        
        # Handle /clear command
        if user_message.startswith('/clear'):
            openwebui_client.clear_conversation(user_id)
            bot.send_message(chat_id, "✅ 对话历史已清除，我们可以开始新的对话了！")
            return jsonify({'ok': True})

        # Handle /cancel command
        if user_message.startswith('/cancel'):
            if openwebui_client.cancel_processing(user_id):
                bot.send_message(chat_id, "✅ 已取消当前的消息处理")
            else:
                bot.send_message(chat_id, "ℹ️ 当前没有正在处理的消息")
            return jsonify({'ok': True})

        # 如果用户正在处理消息，自动取消之前的处理
        if user_id in user_processing_status:
            logger.info(f"Auto-cancelling previous processing for user {user_id}")
            openwebui_client.cancel_processing(user_id)
            # 给用户一个短暂的反馈
            bot.send_message(chat_id, "⏭️ 已自动取消上一个处理，开始处理新消息...")

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
        'name': 'BestVPN AI Telegram Bot',
        'status': 'running',
        'description': 'Telegram bot powered by OpenWebUI API'
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)