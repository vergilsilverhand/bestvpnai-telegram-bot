import os
import logging
import requests
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
OPENWEBUI_BASE_URL = os.environ.get('OPENWEBUI_BASE_URL', 'https://bestvpnai.org')
OPENWEBUI_API_KEY = os.environ.get('OPENWEBUI_API_KEY')

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

class OpenWebUIClient:
    def __init__(self):
        self.base_url = OPENWEBUI_BASE_URL
        self.api_key = OPENWEBUI_API_KEY
        
    def chat_completion(self, message, model="llama3.1"):
        """Send chat completion request to OpenWebUI"""
        url = f"{self.base_url}/api/chat/completions"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": message}
            ]
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if 'choices' in data and len(data['choices']) > 0:
                return data['choices'][0]['message']['content']
            else:
                logger.warning(f"Unexpected response format: {data}")
                return "抱歉，我遇到了一些问题，请稍后再试。"
                
        except requests.exceptions.RequestException as e:
            logger.error(f"OpenWebUI API error: {e}")
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
        
        logger.info(f"Received message from {user_name}: {user_message}")
        
        # Handle /start command
        if user_message.startswith('/start'):
            welcome_message = f"你好 {user_name}! 👋\n\n我是由 BestVPN AI 提供支持的智能助手。\n\n请随时向我提问，我会尽力帮助您！"
            bot.send_message(chat_id, welcome_message)
            return jsonify({'ok': True})
        
        # Handle /help command
        if user_message.startswith('/help'):
            help_message = "🤖 **使用说明**\n\n" + \
                          "• 直接发送消息与我对话\n" + \
                          "• /start - 开始对话\n" + \
                          "• /help - 查看帮助信息\n\n" + \
                          "我会使用 BestVPN AI 的模型为您提供智能回答。"
            bot.send_message(chat_id, help_message)
            return jsonify({'ok': True})
        
        # Get response from OpenWebUI
        ai_response = openwebui_client.chat_completion(user_message)
        
        # Send response back to user
        bot.send_message(chat_id, ai_response)
        
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