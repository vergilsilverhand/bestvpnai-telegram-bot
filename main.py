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
        payload = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'Markdown'
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send message: {e}")
            return None
    
    def edit_message(self, chat_id, message_id, text):
        """Edit existing message in Telegram chat"""
        url = f"{self.api_url}/editMessageText"
        
        # 清理文本，避免Markdown格式问题
        text = text.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
        
        # 限制消息长度
        if len(text) > 4096:
            text = text[:4090] + "..."
        
        payload = {
            'chat_id': chat_id,
            'message_id': message_id,
            'text': text,
            'parse_mode': 'Markdown'
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                # 如果Markdown失败，尝试纯文本
                payload['parse_mode'] = None
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

        # OpenWebUI标准payload格式
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": 4000,
            "temperature": 0.7
        }

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

            response = requests.post(url, headers=headers, json=payload, timeout=600)
            logger.info(f"Response status: {response.status_code}")

            # 如果状态码不是200，记录详细错误信息
            if response.status_code != 200:
                logger.error(f"API returned non-200 status: {response.status_code}")
                logger.error(f"Response headers: {dict(response.headers)}")
                logger.error(f"Response content: {response.text[:1000]}...")  # 限制长度避免日志过长

            # 检查是否被取消
            if self.is_processing_cancelled(user_id):
                bot.edit_message(chat_id, message_id, "❌ 处理已被取消")
                self.clear_processing_status(user_id)
                return "处理已被取消"

            response.raise_for_status()
            data = response.json()

            # 添加调试日志
            logger.info(f"API Response status: {response.status_code}")
            logger.info(f"API Response keys: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
            if 'choices' in data:
                logger.info(f"Choices length: {len(data['choices'])}")
                if len(data['choices']) > 0:
                    choice = data['choices'][0]
                    logger.info(f"First choice keys: {list(choice.keys()) if isinstance(choice, dict) else 'Not a dict'}")

            # 检查是否被取消
            if self.is_processing_cancelled(user_id):
                bot.edit_message(chat_id, message_id, "❌ 处理已被取消")
                self.clear_processing_status(user_id)
                return "处理已被取消"

            # 尝试多种响应格式解析
            ai_response = ""

            # 格式1: OpenAI标准格式 choices[0].message.content
            if 'choices' in data and len(data['choices']) > 0:
                choice = data['choices'][0]
                if 'message' in choice and 'content' in choice['message']:
                    ai_response = choice['message']['content']
                    logger.info("Found response in choices[0].message.content format")
                # 格式2: 有些API可能直接在choices[0]中有content
                elif 'content' in choice:
                    ai_response = choice['content']
                    logger.info("Found response in choices[0].content format")
                # 格式3: 检查text字段
                elif 'text' in choice:
                    ai_response = choice['text']
                    logger.info("Found response in choices[0].text format")

            # 格式4: 直接在根级别的content字段
            if not ai_response and 'content' in data:
                ai_response = data['content']
                logger.info("Found response in root content format")

            # 格式5: message字段
            if not ai_response and 'message' in data:
                if isinstance(data['message'], str):
                    ai_response = data['message']
                elif isinstance(data['message'], dict) and 'content' in data['message']:
                    ai_response = data['message']['content']
                logger.info("Found response in root message format")

            if ai_response and ai_response.strip():
                # 过滤响应
                filtered_response = self.filter_ai_response(ai_response.strip())
                if filtered_response:
                    # 更新消息为最终回复
                    bot.edit_message(chat_id, message_id, filtered_response)
                    self.add_to_conversation(user_id, "assistant", filtered_response)
                    self.clear_processing_status(user_id)
                    logger.info(f"Response length: {len(filtered_response)}")
                    return filtered_response
                else:
                    logger.warning("Response was filtered out completely")
                    bot.edit_message(chat_id, message_id, "抱歉，我没有收到完整的回复，请稍后再试。")
                    self.clear_processing_status(user_id)
                    return "抱歉，我没有收到完整的回复，请稍后再试。"
            else:
                logger.warning(f"No valid response found in API response. Full response: {data}")
                bot.edit_message(chat_id, message_id, "抱歉，我没有收到有效的回复，请稍后再试。")
                self.clear_processing_status(user_id)
                return "抱歉，我没有收到有效的回复，请稍后再试。"

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