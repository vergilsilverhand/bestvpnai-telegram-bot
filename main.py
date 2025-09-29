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
    
    def stream_chat_completion(self, bot, chat_id, user_id, message, model="xmptest.https://api.perplexity.ai"):
        """流式处理AI响应并实时更新Telegram消息"""
        url = f"{self.base_url}/api/chat/completions"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        # 添加用户消息到历史
        self.add_to_conversation(user_id, "user", message)
        
        # 获取完整对话历史
        messages = self.get_conversation_history(user_id)
        
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": 8000,
            "temperature": 0.7,
            "top_p": 0.9,
            "frequency_penalty": 0,
            "presence_penalty": 0,
            "stop": None
        }
        
        # 发送初始状态消息
        status_msg = bot.send_message(chat_id, "🤔 正在思考...")
        if not status_msg:
            return "抱歉，发送消息时出现问题。"
        
        message_id = status_msg['result']['message_id']
        
        try:
            logger.info(f"Sending request to OpenWebUI: {url}")
            
            response = requests.post(url, headers=headers, json=payload, timeout=600, stream=True)
            response.raise_for_status()
            
            # 流式处理响应
            full_response = ""
            current_text = ""
            last_update_time = 0
            
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
                                    current_text = self.filter_ai_response(full_response)
                                    
                                    # 每0.5秒更新消息
                                    current_time = time.time()
                                    if current_time - last_update_time > 0.5 and current_text.strip():
                                        bot.edit_message(chat_id, message_id, current_text + " ▋")
                                        last_update_time = current_time
                        except json.JSONDecodeError:
                            continue
            
            # 最终更新 - 移除光标
            if current_text.strip():
                bot.edit_message(chat_id, message_id, current_text)
                self.add_to_conversation(user_id, "assistant", current_text)
                logger.info(f"Final response length: {len(current_text)}")
                return current_text
            else:
                bot.edit_message(chat_id, message_id, "抱歉，我没有收到完整的回复，请稍后再试。")
                return "抱歉，我没有收到完整的回复，请稍后再试。"
                
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            bot.edit_message(chat_id, message_id, "抱歉，处理您的请求时出现了问题，请稍后再试。")
            return "抱歉，处理您的请求时出现了问题，请稍后再试。"
    
    def chat_completion(self, user_id, message, model="xmptest.https://api.perplexity.ai"):
        """Send chat completion request to OpenWebUI with conversation context"""
        url = f"{self.base_url}/api/chat/completions"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        # 添加用户消息到历史
        self.add_to_conversation(user_id, "user", message)
        
        # 获取完整对话历史
        messages = self.get_conversation_history(user_id)
        
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": 4000,
            "temperature": 0.7
        }
        
        try:
            logger.info(f"Sending request to OpenWebUI: {url}")
            logger.info(f"Messages count: {len(messages)}")
            response = requests.post(url, headers=headers, json=payload, timeout=120, stream=True)
            logger.info(f"Response status: {response.status_code}")
            
            if response.status_code == 400:
                logger.error(f"Bad request response: {response.text}")
                
            response.raise_for_status()
            
            # 处理流式响应
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
            
            if full_response.strip():
                # 过滤响应并添加到历史
                filtered_response = self.filter_ai_response(full_response.strip())
                if filtered_response:
                    self.add_to_conversation(user_id, "assistant", filtered_response)
                    return filtered_response
                else:
                    logger.warning("Response filtered out completely")
                    return "抱歉，我没有收到完整的回复，请稍后再试。"
            else:
                logger.warning("No content received from streaming response")
                return "抱歉，我没有收到完整的回复，请稍后再试。"
                
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            logger.error(f"Response content: {e.response.text if hasattr(e, 'response') else 'No response'}")
            return "抱歉，我现在无法处理您的请求，请稍后再试。"
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error: {e}")
            return "抱歉，我现在无法处理您的请求，请稍后再试。"

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
                          "• /help - 查看帮助信息\n\n" + \
                          "我会记住我们的对话内容，支持上下文对话！"
            bot.send_message(chat_id, help_message)
            return jsonify({'ok': True})
        
        # Handle /clear command
        if user_message.startswith('/clear'):
            openwebui_client.clear_conversation(user_id)
            bot.send_message(chat_id, "✅ 对话历史已清除，我们可以开始新的对话了！")
            return jsonify({'ok': True})
        
        # Get streaming response from OpenWebUI with real-time updates
        ai_response = openwebui_client.stream_chat_completion(bot, chat_id, user_id, user_message)
        
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