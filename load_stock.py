"""
Script que carrega estoque direto via comando do bot.
Envia cada login como se fosse digitado pelo admin.
"""
import requests
import time
import re

token = '8701402389:AAGAj33V5dgLJp2JbP8QJUd9hXTSL2f0_TY'
admin_id = 925542353

# Primeiro, testa mandando UM login manualmente pra ver se o bot processa
msg = "/addlogin TESTE===TESTE===1===teste@teste.com 123456======30 DIAS===teste"

r = requests.post(f'https://api.telegram.org/bot{token}/sendMessage', json={
    'chat_id': admin_id,
    'text': msg
})
print(r.json())
